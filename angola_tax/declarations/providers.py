"""Read approved source data; never invoke validate/submit/posting on source docs."""
import json
from decimal import Decimal
import frappe
from frappe.utils import getdate
from angola_tax.declarations.model import digest, VERSION

ADAPTERS = {'II Withholding Map v1': ('Industrial Tax Withholding Period', 'xlsx'),
            'II Annual Portal v1': ('Industrial Tax Computation', 'json'),
            'VAT Portal v1': ('VAT Period Control', 'json')}
IGNORED = {'creation', 'modified', 'modified_by', 'owner', '_user_tags', '_comments',
    '_assign', '_liked_by', 'notes', 'status', 'declaration_reference', 'submission_date',
    'declaration_document', 'submission_reference', 'submission_status'}

def semantic(value):
    if isinstance(value, dict):
        return {k: semantic(v) for k, v in value.items() if k not in IGNORED and not k.startswith('__')}
    if isinstance(value, list): return [semantic(v) for v in value]
    return value

def read_source(kind, name, company):
    doc = frappe.get_doc(kind, name)
    doc.check_permission('read')
    if doc.company != company or doc.docstatus != 1:
        frappe.throw('Declaration source must be approved and belong to this Company.')
    return doc

def dependencies(source, rows):
    result = []
    refs = set()
    for r in rows:
        kind = r.get('voucher_type') or r.get('source_document_type') or r.get('invoice_doctype')
        name = r.get('voucher_no') or r.get('source_document') or r.get('invoice')
        if kind and name: refs.add((kind, name))
    for kind, name in sorted(refs):
        doc = frappe.get_doc(kind, name); doc.check_permission('read')
        if doc.company != source.company or doc.docstatus != 1:
            frappe.throw(f'Source voucher {kind} {name} was cancelled or changed company.')
        facts = semantic(doc.as_dict())
        result.append({'doctype': kind, 'name': name, 'facts': facts})
        party_kind, party = ('Supplier', doc.get('supplier')) if doc.get('supplier') else ('Customer', doc.get('customer'))
        if party:
            master = frappe.get_doc(party_kind, party); master.check_permission('read')
            result.append({'doctype': party_kind, 'name': party, 'facts': {
                'tax_id': master.get('tax_id'), 'supplier_name': master.get('supplier_name'),
                'customer_name': master.get('customer_name')}})
    return result

def collect(declaration):
    typ = frappe.get_doc('Tax Declaration Type', declaration.declaration_type)
    if typ.adapter_key not in ADAPTERS:
        frappe.throw('This filing exporter is not implemented. Keep the scheduled filing visible; do not generate a substitute format.')
    kind, extension = ADAPTERS[typ.adapter_key]
    if declaration.source_doctype != kind or not declaration.source_name:
        frappe.throw(f'Select the approved {kind} source.')
    source = read_source(kind, declaration.source_name, declaration.company)
    start = source.get('fiscal_period_from') or source.get('period_start')
    end = source.get('fiscal_period_to') or source.get('period_end')
    if str(start)[:10] != str(declaration.period_start)[:10] or str(end)[:10] != str(declaration.period_end)[:10]:
        frappe.throw('Declaration period differs from its approved computation.')
    company = frappe.get_doc('Company', declaration.company); company.check_permission('read')
    if not company.tax_id: frappe.throw('Complete the Company NIF before preparing the declaration.')
    facts = semantic(source.as_dict())
    if typ.adapter_key == 'II Withholding Map v1':
        if not source.accounting_complete or not source.map_review_complete:
            frappe.throw('Complete the approved monthly AGT map review before preparation.')
        from angola_tax.industrial_tax.withholding_map import map_values
        rows = map_values(source)
        if source.get('source_format') == 'GL Withholding v1':
            from angola_tax.industrial_tax.withholding_gl import current, query
            current(source); scope = [dict(r) for r in query(source)]
        else:
            scope = []
            for r in source.withholdings:
                origin = read_source('Industrial Tax Withholding', r.withholding, source.company)
                scope.append(semantic(origin.as_dict()))
        values = {'rows': rows, 'tax_amount': source.tax_amount, 'currency': source.currency}
        reference_rows = [r.as_dict() for r in source.get('map_invoices') or []]
        reference_rows += [r.as_dict() for r in source.get('gl_movements') or []]
    elif typ.adapter_key == 'II Annual Portal v1':
        if not source.accounting_complete or not source.approved_on:
            frappe.throw('Use a fully approved and accounted Industrial Tax computation.')
        profile = frappe.get_doc('Industrial Tax Profile', source.profile); profile.check_permission('read')
        facts['approved_profile'] = semantic(profile.as_dict())
        if source.rule_version:
            rule = frappe.get_doc('Industrial Tax Rule', source.rule_version); rule.check_permission('read')
            facts['approved_rule'] = semantic(rule.as_dict())
        from angola_tax.industrial_tax.computation import fingerprint
        scope = source.source_query(profile)
        if fingerprint(scope) != source.source_hash:
            frappe.throw('Annual source scope changed. Correct through Industrial Tax before declaring.')
        values = {k: source.get(k) for k in ('result_before_tax', 'addbacks', 'deductions',
            'fiscal_result', 'taxable_base', 'tax_raw', 'tax_amount', 'rounding_difference',
            'credit_applied_amount', 'net_cash_requirement', 'currency')}
        values['adjustments'] = [r.as_dict() for r in source.adjustments or []]
        values['credit_allocations'] = [r.as_dict() for r in source.get('credit_allocations') or []]
        reference_rows = [dict(r) for r in scope]
    else:
        if not source.is_reconciled or not source.modelo_7_confirmed or not source.reviewed_on:
            frappe.throw('Use an approved reconciled VAT period with confirmed Modelo 7.')
        if not source.vat_reconciliation:
            frappe.throw('VAT declaration requires its approved reconciliation.')
        reconciliation = read_source('VAT Reconciliation', source.vat_reconciliation, source.company)
        if not reconciliation.is_reconciled: frappe.throw('VAT reconciliation is not approved as reconciled.')
        settings = frappe.get_doc('VAT Settings', source.vat_settings); settings.check_permission('read')
        accounts = [r.account for r in settings.account_mappings or [] if r.active]
        if not accounts: frappe.throw('VAT account mapping is missing.')
        import_journals = set(frappe.get_all('VAT Import Document', filters={'company':source.company}, pluck='recognition_journal_entry', limit_page_length=0))
        adjustment_journals = {r.journal_entry for r in source.reconciling_items or [] if r.journal_entry}
        gl = frappe.get_all('GL Entry', filters={'company':source.company,
            'posting_date':['between',[start,end]], 'account':['in',accounts], 'is_cancelled':0},
            fields=['name','posting_date','account','voucher_type','voucher_no','debit','credit','is_opening'],
            order_by='name', limit_page_length=0)
        scope = [dict(r, gl_entry=r.name) for r in gl if r.is_opening != 'Yes'
            and r.voucher_no != source.closing_journal_entry
            and (r.voucher_type in ('Sales Invoice','Purchase Invoice') or
                (r.voucher_type == 'Journal Entry' and r.voucher_no in import_journals | adjustment_journals))]
        reference_rows = [r.as_dict() for r in source.get_vat_determination_transactions()]
        values = {k: source.get(k) for k in ('declared_state_amount', 'declared_taxpayer_amount',
            'previous_credit', 'declared_payable', 'declared_credit', 'currency')}
        values['modelo_7_lines'] = [r.as_dict() for r in source.modelo_7_lines or []]
        values['declaration_details'] = [r.as_dict() for r in source.declaration_details or []]
        facts['approved_reconciliation'] = semantic(reconciliation.as_dict())
        facts['vat_settings'] = semantic(settings.as_dict())
        # Compare approved scope to current GL, including inserts and cancellations.
        def gl_facts(rows):
            return sorted([(r.get('gl_entry') or r.get('name'), str(r.get('posting_date'))[:10],
                r.get('account'), r.get('voucher_type'), r.get('voucher_no'),
                str(Decimal(str(r.get('debit') or 0)).normalize()),
                str(Decimal(str(r.get('credit') or 0)).normalize())) for r in rows])
        expected = gl_facts(reference_rows)
        actual = gl_facts(scope)
        if expected != actual: frappe.throw('VAT GL scope changed; review the originating VAT control.')
    manifest = {'provider_version': VERSION, 'adapter': typ.adapter_key,
        'source': {'doctype': kind, 'name': source.name, 'facts': facts},
        'selection': {'company': source.company, 'start': str(start), 'end': str(end)},
        'current_scope': scope, 'dependencies': dependencies(source, reference_rows),
        'company': {'name': company.name, 'tax_id': company.tax_id},
        'filing_type': {'name': typ.name, 'adapter_key': typ.adapter_key,
            'schema_version': typ.schema_version, 'verification_state': typ.verification_state,
            'completion_rule': typ.completion_rule, 'supports_replacement': typ.supports_replacement,
            'supports_corrective': typ.supports_corrective}}
    return {'manifest': manifest, 'values': values, 'manifest_hash': digest(manifest),
            'source_obligation': source.get('fiscal_obligation'), 'extension': extension}

def export(snapshot):
    """Generate from frozen payload, never by rereading a live computation."""
    if snapshot['adapter'] == 'II Withholding Map v1':
        from angola_tax.industrial_tax.withholding_map import build_xlsx
        return ('xlsx', build_xlsx(snapshot['company_nif'], snapshot['period_start'][:7], snapshot['values']['rows']))
    # These are explicit portal-entry summaries, not invented AGT import schemas.
    from angola_tax.declarations.model import canonical
    return ('json', canonical(snapshot).encode('utf-8'))
