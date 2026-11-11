import calendar
import frappe
from frappe.utils import getdate, nowdate, now_datetime
from angola_tax.declarations import model
from angola_tax.declarations.controller import TaxDeclaration, controlled_save, require_role
from angola_tax.industrial_tax.adapters import IndustrialFiscalObligation

def expected_filings(start, end, configuration=None, company=None, permission_check=True):
    """One shared read-only iterator for calendar generation and missing-filing warnings."""
    start, end = getdate(start), getdate(end)
    if end < start or (end-start).days > 732: frappe.throw('Select a period of at most two years.')
    filters = {'declaration_purpose': 'Declaration', 'active': 1, 'docstatus': 1}
    if configuration: filters['name'] = configuration
    if company: filters['company'] = company
    query = frappe.get_list if permission_check else frappe.get_all
    configs = query('Tax Deadline Configuration', filters=filters, pluck='name', limit_page_length=0)
    for name in configs:
        config = frappe.get_doc('Tax Deadline Configuration', name)
        if permission_check: config.check_permission('read')
        if config.declaration_applicability != 'Required': continue
        typ = frappe.get_doc('Tax Declaration Type', config.declaration_type)
        if typ.verification_state != 'Verified': continue
        rule = config.deadline_rules[0]
        if rule.periodicity == 'Transaction': continue  # Explicit event creation only.
        cursor = start.replace(day=1)
        while cursor <= end:
            month = cursor.month
            is_end = rule.periodicity == 'Monthly' or (rule.periodicity == 'Quarterly' and month in (3,6,9,12)) or (rule.periodicity == 'Annual' and month == 12)
            period_end = cursor.replace(day=calendar.monthrange(cursor.year, month)[1])
            if is_end and start <= period_end <= end and getdate(config.effective_from) <= period_end and (not config.effective_to or period_end <= getdate(config.effective_to)):
                first_month = month if rule.periodicity == 'Monthly' else month-2 if rule.periodicity == 'Quarterly' else 1
                period_start = cursor.replace(month=first_month, day=1)
                key = model.occurrence(config.company, config.declaration_type, period_start, period_end)
                yield config, period_start, period_end, key
            cursor = cursor.replace(year=cursor.year + (month == 12), month=1 if month == 12 else month+1)

@frappe.whitelist()
def generate_expected(start, end, configuration=None):
    require_role('Tax Preparer', 'Tax Approver')
    # Preserve the existing review result alongside the shared iterator.
    filters = {'declaration_purpose': 'Declaration', 'active': 1, 'docstatus': 1}
    if configuration: filters['name'] = configuration
    created, existing, review = [], [], []
    for name in frappe.get_list('Tax Deadline Configuration', filters=filters, pluck='name', limit_page_length=0):
        config = frappe.get_doc('Tax Deadline Configuration', name); config.check_permission('read')
        typ = frappe.get_doc('Tax Declaration Type', config.declaration_type)
        if config.declaration_applicability == 'Needs Review' or (config.declaration_applicability == 'Required' and typ.verification_state != 'Verified'): review.append(name)
    for config, period_start, period_end, key in expected_filings(start, end, configuration):
        found = frappe.db.get_value('Tax Declaration', {'original_key': key}, 'name')
        if found: existing.append(found)
        else:
            doc = frappe.get_doc({'doctype': 'Tax Declaration', 'company': config.company,
                'declaration_type': config.declaration_type, 'deadline_configuration': config.name,
                'period_start': period_start, 'period_end': period_end,
                'responsible_user': config.declaration_responsible_user or config.owner,
                'reviewer': config.declaration_reviewer, 'submission_kind': 'Original', 'revision': 1})
            doc.insert(); created.append(doc.name)
    return {'created': created, 'existing': existing, 'needs_review': review}

def refresh_chain(root):
    versions = frappe.get_all('Tax Declaration', filters={'root_declaration': root},
        fields=['name', 'revision', 'delivery_state', 'docstatus'], order_by='revision')
    accepted = model.effective_version(versions)
    for row in versions:
        state = 'Superseded' if accepted and row.name != accepted and row.revision < next(v.revision for v in versions if v.name == accepted) else 'Current Version'
        if accepted and row.name == accepted:
            state = 'Replacement Pending' if row.revision < versions[-1].revision else 'Current Accepted'
        frappe.db.set_value('Tax Declaration', row.name,
            {'effective_accepted_version': accepted, 'chain_state': state,
             'is_latest_version':int(row.name == versions[-1].name)}, update_modified=False)
    # Compatibility fields are deliberately informational; no amount field is written.
    links = frappe.get_all('Tax Declaration Obligation Link', filters={'parent': ['in', [v.name for v in versions]]}, pluck='fiscal_obligation', limit_page_length=0)
    for obligation in set(links):
        doc = frappe.get_doc('Fiscal Obligation', obligation)
        if apply_summary(doc):
            data = {f: doc.get(f) for f in ('filing_status', 'is_filing_overdue', 'days_filing_overdue',
                'submission_status','is_submission_overdue','days_submission_overdue') if doc.meta.has_field(f)}
            frappe.db.set_value('Fiscal Obligation', obligation, data, update_modified=False)

def related_declarations(obligation):
    if not frappe.db.table_exists('Tax Declaration Obligation Link'): return []
    names = frappe.get_all('Tax Declaration Obligation Link', filters={'fiscal_obligation': obligation}, pluck='parent', limit_page_length=0)
    if not names: return []
    rows = frappe.get_all('Tax Declaration', filters={'name': ['in', names]},
        fields=['name','root_declaration','revision','delivery_state','is_overdue','days_overdue','applicability','chain_state','effective_due_date'], limit_page_length=0)
    # Use effective accepted version; a pending correction never erases its delivery.
    groups = {}
    for row in rows: groups.setdefault(row.root_declaration, []).append(row)
    return [next((r for r in group if r.chain_state in ('Current Accepted','Replacement Pending')), max(group, key=lambda r:r.revision)) for group in groups.values()]

def apply_summary(doc):
    declarations = related_declarations(doc.name)
    if not declarations: return False
    required = [r for r in declarations if r.applicability == 'Required']
    doc.is_filing_overdue = int(any(r.is_overdue for r in required))
    doc.days_filing_overdue = max([r.days_overdue or 0 for r in required] or [0])
    complete = bool(required) and all(r.delivery_state in ('Acknowledged','Accepted') for r in required)
    due_dates = [getdate(r.effective_due_date) for r in required if r.effective_due_date and r.delivery_state not in ('Acknowledged','Accepted')]
    remaining = (min(due_dates)-getdate(nowdate())).days if due_dates else None
    pending_status = 'Due Today' if remaining == 0 else 'Due Soon' if remaining is not None and 0 < remaining <= 5 else 'Not Due'
    doc.filing_status = 'Overdue' if doc.is_filing_overdue else 'Filed' if complete else pending_status
    doc.submission_status = 'Overdue' if doc.is_filing_overdue else 'Submitted' if complete else pending_status
    doc.is_submission_overdue = doc.is_filing_overdue
    doc.days_submission_overdue = doc.days_filing_overdue
    return True

class DeclarationFiscalObligation(IndustrialFiscalObligation):
    def calculate_filing_status(self):
        if not apply_summary(self): return super().calculate_filing_status()
    def calculate_submission_status(self):
        if not apply_summary(self): return super().calculate_submission_status()
    def set_submission_due_date(self):
        if not related_declarations(self.name): return super().set_submission_due_date()
    def set_filing_due_date(self):
        if not related_declarations(self.name): return super().set_filing_due_date()
    def validate_submission(self):
        if not related_declarations(self.name): return super().validate_submission()
    def validate_filing(self):
        if not related_declarations(self.name): return super().validate_filing()

def daily():
    """Recovery sweep. One failure cannot suppress another company's filing calendar."""
    today = getdate(nowdate())
    start = today.replace(year=today.year-1, month=1, day=1)
    try: generate_expected(start, today)
    except Exception: frappe.log_error(frappe.get_traceback(), 'Declaration expected-filings sweep')
    roots = set()
    for row in frappe.get_all('Tax Declaration', filters={'docstatus': ['!=', 2]}, fields=['name','root_declaration','snapshot_file'], limit_page_length=0):
        try:
            doc = frappe.get_doc('Tax Declaration', row.name)
            if row.snapshot_file: doc.check_sources()
            else: doc.compute_overdue(); controlled_save(doc)
            roots.add(row.root_declaration)
        except Exception: frappe.log_error(frappe.get_traceback(), f'Declaration sweep: {row.name}')
    for root in roots: refresh_chain(root)

def protect_file(doc, method=None):
    if not frappe.db.table_exists('Tax Declaration'):
        return
    previous = doc.get_doc_before_save()
    urls = {doc.get('file_url'), previous.get('file_url') if previous else None} - {None}
    for url in urls:
        protected = frappe.db.sql('''select name from `tabTax Declaration`
            where docstatus=1 and (snapshot_file=%s or manifest_file=%s or generated_file=%s) limit 1''', (url,url,url))
        evidence = frappe.db.exists('Tax Declaration Event', {'evidence': url})
        if not (protected or evidence):
            continue
        # A new File row may alias existing immutable bytes. It may not replace,
        # edit, reparent or delete an existing protected File row.
        if method == 'on_trash' or not doc.is_new():
            frappe.throw('This file is preserved declaration evidence. Attach a new file and append an event.')
        if not doc.get('is_private') or doc.get('attached_to_doctype') != 'Tax Declaration' or not doc.get('attached_to_name'):
            frappe.throw('A protected-file alias must be private and attached to a declaration.')
        target = frappe.get_doc('Tax Declaration', doc.attached_to_name)
        target.check_permission('read')
        names = frappe.get_all('File', filters={'file_url': url, 'is_private': 1},
            pluck='name', order_by='name', limit_page_length=0)
        original = None
        for name in names:
            candidate = frappe.get_doc('File', name)
            try:
                candidate.check_permission('read')
            except frappe.PermissionError:
                continue
            original = candidate
            break
        if original is None:
            frappe.throw('No readable private original exists for this protected URL.', frappe.PermissionError)
        expected = original.get_content()
        if isinstance(expected, str):
            expected = expected.encode('utf-8')
        if not isinstance(expected, bytes):
            frappe.throw('The preserved original content cannot be verified.')
        incoming = doc.get('content')
        if incoming is not None:
            if doc.get('decode'):
                import base64
                incoming = base64.b64decode(incoming, validate=True)
            elif isinstance(incoming, str):
                incoming = incoming.encode('utf-8')
            if not isinstance(incoming, bytes) or incoming != expected:
                frappe.throw('A new alias cannot change preserved declaration content.')

def protect_legacy_submission(doc, method=None):
    old = doc.get_doc_before_save()
    if not old: return
    if doc.doctype == 'Fiscal Obligation': managed = bool(related_declarations(doc.name))
    else: managed = frappe.db.exists('Tax Declaration', {'source_doctype':doc.doctype, 'source_name':doc.name})
    if managed:
        for field in ('filing_date','filing_due_date','declaration_reference','submission_date','submission_reference','declaration_document'):
            if doc.meta.has_field(field) and doc.get(field) != old.get(field):
                frappe.throw('Record external delivery in Tax Declaration; legacy filing fields are read-only for managed declarations.')

def calendar_events(start, end, filters=None):
    require_role('Tax Preparer', 'Tax Approver', 'Tax Submission User')
    return frappe.get_list('Tax Declaration', filters={'effective_due_date':['between',[start,end]],
        'is_latest_version':1}, fields=['name','declaration_type as title',
        'effective_due_date as start','effective_due_date as end','delivery_state','company'], limit_page_length=0)

calendar_events = frappe.whitelist()(calendar_events)
