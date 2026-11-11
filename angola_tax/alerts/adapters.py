"""Read authoritative source documents. Never save fiscal documents."""
import json
from collections import defaultdict
from datetime import timedelta
import frappe
from frappe.utils import getdate, nowdate
from angola_tax.alerts.catalog import CATALOG, PHASES
from angola_tax.alerts.model import due_window, identity, legacy_reasons

TERMINAL = {'Cancelled','Closed','Superseded'}

def documents(doctype, company):
    meta = frappe.get_meta(doctype)
    filters = {'docstatus':['!=',2]}
    if meta.has_field('company'): filters['company'] = company
    for name in frappe.get_all(doctype, filters=filters, pluck='name', limit_page_length=0):
        doc = frappe.get_doc(doctype, name)
        if company_of(doc) == company: yield doc

def company_of(doc):
    if doc.get('company'): return doc.company
    # Collection references have no company field: follow their fiscal parent.
    for dt, field in [('Fiscal Obligation','fiscal_obligation'),('Tax Payment Plan','payment_plan'),
                      ('Tax Settlement','tax_settlement')]:
        if doc.get(field):
            value = frappe.db.get_value(dt, doc.get(field), 'company')
            if value: return value
    return None

def candidate(rule, doc, explanation, action, detail='', due=None, notice=False, extra=None):
    company = company_of(doc)
    if company != rule.company: raise ValueError('Cross-company fiscal source refused.')
    return dict(condition_key=identity(company,rule.fiscal_process,rule.condition,doc.doctype,doc.name,detail),
        company=company,fiscal_process=rule.fiscal_process,condition=rule.condition,rule=rule.name,
        source_doctype=doc.doctype,source_name=doc.name,detail_key=detail,
        title=CATALOG[rule.condition][2],explanation=explanation,action_needed=action,
        kind='Notice' if notice or rule.kind=='Notice' else 'Alert',
        severity=(rule.overdue_severity or rule.severity) if due and getdate(due)<getdate(nowdate()) else rule.severity,
        due_date=str(due)[:10] if due else None,
        responsible_user=rule.responsible_user or doc.get('responsible_user') or doc.get('declaration_responsible_user'),
        responsible_role=rule.responsible_role, escalation_user=rule.escalation_user or doc.get('reviewer'),
        facts_json=json.dumps(extra or {},default=str,ensure_ascii=False))

def evaluate(rule):
    today = getdate(nowdate()); condition = rule.condition
    within = lambda due: due_window(due,today,rule.advance_days)
    result = []
    def add(doc, explanation, action, **kw): result.append(candidate(rule,doc,explanation,action,**kw))
    if condition == 'missing_filing':
        from angola_tax.declarations.service import expected_filings
        from angola_tax.declarations.configuration import resolve
        start = today.replace(year=today.year-1,month=1,day=1)
        for config, start_date, end_date, key in expected_filings(start,today,company=rule.company,permission_check=False):
            if frappe.db.exists('Tax Declaration', {'original_key':key,'docstatus':['!=',2]}): continue
            due = resolve(config,start_date,end_date)['effective_due_date']
            if within(due):
                add(config,f'A required {config.declaration_type} filing for {start_date} to {end_date} has not been created.',
                    'Generate the required declaration from the existing fiscal calendar; then prepare and approve it.',
                    detail=key,due=due,extra={'period_start':str(start_date),'period_end':str(end_date),'declaration_type':config.declaration_type})
        return result
    if condition == 'configuration_health': return configuration_health(rule)
    types = PHASES if condition in ('litigation_due','authority_response') else [CATALOG[condition][1]]
    for dt in types:
        for doc in documents(dt,rule.company):
            if doc.get('status') in TERMINAL or doc.get('phase_status')=='Closed': continue
            if dt=='Tax Declaration':
                if not doc.is_latest_version or doc.applicability not in ('Required','Optional','Needs Review'): continue
                due=doc.effective_due_date
                delivered=doc.delivery_state in ('Submitted','Acknowledged','Accepted')
                if condition=='preparation_due' and doc.applicability=='Required' and doc.preparation_state not in ('Prepared','Approved') and within(due):
                    add(doc,'This required declaration has not been prepared.', 'Prepare its source package before the delivery deadline.',due=due)
                elif condition=='approval_due' and doc.applicability=='Required' and doc.preparation_state!='Approved' and within(due):
                    add(doc,'This required declaration has not been approved.', 'Prepare it if necessary and ask the authorised approver to approve the package.',due=due)
                elif condition=='submission_due' and doc.applicability=='Required' and not delivered and within(due):
                    add(doc,'No successful external submission is recorded for this required declaration.', 'Submit the approved package and record the submission with evidence.',due=due)
                elif condition=='rejected_submission' and doc.delivery_state=='Rejected':
                    add(doc,'The most recent recorded external outcome is rejection.', 'Review the rejection, prepare the required correction and record the new external outcome.')
                elif condition=='correction_pending' and doc.previous_version and doc.delivery_state not in ('Acknowledged','Accepted'):
                    add(doc,'A corrective or replacement version remains unfinished.', 'Complete and deliver the new version; retain the previously accepted version.',due=due)
                elif condition=='sources_changed' and doc.snapshot_file:
                    changed=doc.freshness=='Sources Changed'
                    try: doc.verify_package()
                    except frappe.PermissionError: raise
                    except Exception: changed=True
                    else: changed=False
                    if changed: add(doc,'The prepared package no longer passes the existing source verification.', 'Review the source changes and prepare a new draft package or linked revision.')
                elif condition=='submission_evidence' and delivered:
                    submissions=[e for e in doc.events or [] if e.action=='Submission']
                    latest=submissions[-1] if submissions else None
                    if latest is None or not evidence_exists(latest.get('evidence')):
                        add(doc,'The recorded delivery has no readable submission evidence file.', 'Use the declaration evidence correction action to attach valid submission evidence.')
                elif condition=='acknowledgement_evidence' and delivered:
                    latest=next((e for e in reversed(doc.events or []) if e.action=='Submission'),None)
                    responses=[e for e in doc.events or [] if e.action in ('Acknowledged','Accepted') and (not latest or e.attempt_id==latest.attempt_id)]
                    if not responses or not evidence_exists(responses[-1].evidence):
                        add(doc,'The latest submission lacks acknowledgement or its evidence file.', 'Obtain the authority acknowledgement and record it against the correct submission attempt.')
            elif condition=='legacy_compliance' and doc.compliance_alert:
                add(doc,doc.compliance_message or f'Fiscal compliance requires attention: {doc.compliance_status}.',
                    'Review the fiscal obligation and complete its outstanding action.',notice=doc.compliance_status=='Paid Late',extra={'reasons':sorted(legacy_reasons(doc))})
            elif condition in ('determination_due','liquidation_due','payment_internal_due'):
                field={'determination_due':'determination_target_date','liquidation_due':'liquidation_target_date','payment_internal_due':'payment_target_date'}[condition]
                critical={'determination_due':'determination_critical_date','liquidation_due':'liquidation_critical_date','payment_internal_due':'payment_critical_date'}[condition]
                due=doc.get(field) or doc.get(critical)
                pending=(doc.docstatus==0) if condition=='determination_due' else (doc.status in ('Awaiting Liquidation','Awaiting Assessment','Determined','Approved') and not doc.latest_tax_settlement) if condition=='liquidation_due' else (doc.obligation_type=='Payable' and float(doc.outstanding_amount or 0)>0 and not doc.payment_plan)
                if pending and within(due):
                    add(doc,'The fiscal process remains outstanding against its existing internal target.',
                        'Complete the outstanding preparation or payment action through the fiscal obligation workflow.',due=due)
            elif condition=='payment_due':
                from angola_tax.statutory_deadline import get_statutory_payment_due_date
                due=get_statutory_payment_due_date(doc)
                if doc.obligation_type=='Payable' and float(doc.outstanding_amount or 0)>0 and not doc.payment_plan and within(due):
                    add(doc,'The existing fiscal workflow reports an outstanding payable amount.', 'Review the collection reference and complete confirmed payment.',due=due,extra={'outstanding_amount':doc.outstanding_amount,'currency':doc.currency})
            elif condition=='payment_evidence' and doc.docstatus==1:
                if doc.status=='Awaiting DC Pago' or (doc.status=='Confirmed' and (not doc.dc_pago_reference or not evidence_exists(doc.dc_pago))):
                    add(doc,'Payment confirmation is pending or its authority evidence is missing.', 'Attach DC Pago and complete the existing confirmation action; a ledger posting is insufficient.')
            elif condition=='unclassified_gl':
                rows=[r for r in doc.movements or [] if r.movement_type=='Unclassified' or r.classification_status=='Unclassified']
                if rows: add(doc,f'{len(rows)} recorded tax movements remain unclassified.', 'Classify and review the movements in the fiscal obligation.',extra={'rows':[r.name for r in rows]})
            elif condition=='withholding_gl':
                rows=[r for r in doc.gl_movements or [] if r.classification=='Review']
                if rows or doc.pending_gl_count or (abs(float(doc.unregistered_liability_credit or 0))>0.01 and not doc.gl_completeness_reviewed):
                    add(doc,'Withholding movements still require classification.', 'Review the existing withholding GL classifications without changing approved tax amounts.')
            elif condition=='incomplete_map':
                from angola_tax.industrial_tax.withholding_map import map_values
                invalid=not doc.map_review_complete
                try: map_values(doc)
                except frappe.PermissionError: raise
                except Exception: invalid=True
                if invalid: add(doc,'The withholding map is unreviewed or fails its existing completeness checks.', 'Complete the original invoice details and supported per-invoice withholding; review the map. Preserve the approved period amount.')
            elif condition=='agt_allocation' and (not doc.review_complete or abs(float(doc.unallocated_paid_total or 0))>0.01):
                add(doc,'Imported authority amounts remain unreviewed or unallocated.', 'Review the AGT import and complete supported allocations in its existing workflow.')
            elif condition=='vat_classification':
                rows=[r for r in doc.transactions or [] if r.classification_status=='Accounting Only']
                if rows: add(doc,f'{len(rows)} VAT movements have accounting-only classification.', 'Review their VAT nature and declaration mapping in the VAT control.')
            elif condition in ('vat_variance','vat_reconciliation'):
                rows=[r for r in doc.reconciling_items or [] if r.resolution_status=='Open']
                if abs(float(doc.unexplained_difference or 0))>0.01 or rows:
                    add(doc,'VAT has unexplained differences or open reconciling items.', 'Explain or correct the differences through the existing VAT reconciliation.',extra={'unexplained_difference':doc.unexplained_difference,'open_items':len(rows)})
            elif condition=='provisional_variance' and (doc.pending_account_count or abs(float(doc.unexplained_difference or 0))>0.01):
                add(doc,'The provisional computation contains pending accounts or unexplained differences.', 'Review the existing revenue classifications and supported exclusions.')
            elif condition=='fiscal_variance' and (doc.correction_required or (doc.has_variance and not doc.variance_reason)):
                add(doc,'The fiscal obligation reports a correction requirement or an unexplained variance.', 'Resolve it using the existing assessment or reconciliation workflow.')
            elif condition=='plan_variance' and abs(float(doc.gl_variance or 0))>0.01:
                add(doc,'The payment plan reports a difference against its control account.', 'Review the plan reconciliation and confirmed payment allocations.')
            elif condition=='assessment_due':
                due=doc.action_due_date or doc.legal_time_limit_date
                if doc.assessment_decision=='Pending' and not due:
                    add(doc,'The pending assessment has no stored decision due date.', 'Review the official notice and complete its existing action deadline fields; no legal deadline has been assumed.')
                elif doc.assessment_decision=='Pending' and within(due):
                    add(doc,'The assessment decision remains pending.', 'Record the taxpayer decision and its supporting evidence.',due=due)
            elif condition=='litigation_due' and not doc.submission_date and not doc.company_submission_due_date:
                add(doc,'The taxpayer action has no stored submission deadline.', 'Review the official case documents and complete the existing phase deadline field.')
            elif condition=='litigation_due' and not doc.submission_date and within(doc.company_submission_due_date):
                add(doc,'The taxpayer submission for this litigation phase is outstanding.', 'Complete the phase submission and record its reference and document.',due=doc.company_submission_due_date)
            elif condition=='authority_response' and doc.submission_date and not doc.agt_response and within(doc.response_due_date):
                add(doc,'No authority response is recorded against the existing reply deadline.', 'Follow up with the authority and record the response when received.',due=doc.response_due_date,notice=True)
            elif condition=='notification_deadline' and not doc.decision:
                add(doc,'The notification has no stored response due date in the installed schema.', 'Review the official notice and record the response deadline in the existing assessment or litigation phase. Do not derive a legal deadline from the interval alone.')
            elif condition=='instalment_due' and doc.status in ('Active','In Arrears','Awaiting Reissue'):
                for row in doc.instalments or []:
                    if row.payment_status!='Paid' and float(row.outstanding_amount or 0)>0 and within(row.due_date):
                        add(doc,f'Instalment {row.instalment_no} remains outstanding according to the payment plan.',
                            'Review its collection reference and record confirmed payment.',detail=row.name,due=row.due_date,
                            extra={'instalment':row.instalment_no,'outstanding_amount':row.outstanding_amount})
            elif condition=='collection_review':
                if doc.status=='Expired' or (doc.status not in ('Confirmed','Superseded','Cancelled') and doc.due_date and getdate(doc.due_date)<today):
                    add(doc,'The collection reference is expired or its existing due date has passed without confirmation.', 'Review validity with the authority and use the controlled reissue or payment-confirmation workflow.',due=doc.due_date)
            elif condition=='fiscal_deadline_missing' and doc.obligation_type=='Payable' and float(doc.outstanding_amount or 0)>0:
                from angola_tax.statutory_deadline import get_statutory_payment_due_date
                if not get_statutory_payment_due_date(doc):
                    add(doc,'The outstanding obligation has no statutory payment date supplied by the existing workflow.', 'Review its shared deadline configuration or official assessment date. No date has been invented.')
    return result

def evidence_exists(url):
    if not url: return False
    names=frappe.get_all('File',filters={'file_url':url},pluck='name',limit_page_length=0)
    for name in names:
        file=frappe.get_doc('File',name)
        try:
            if file.get_content() is not None: return True
        except (FileNotFoundError,OSError): pass
    return False

def configuration_health(rule):
    today=getdate(nowdate()); result=[]
    def add(doc,message,detail=''):
        result.append(candidate(rule,doc,message,'Review the shared fiscal configuration and its effective dates; retain referenced historical records.',detail=detail,notice=True))
    types=['Tax Deadline Configuration','Tax Account Configuration','Industrial Tax Profile',
           'Industrial Tax Mechanism Profile','Industrial Tax Revenue Configuration','VAT Settings']
    for dt in types:
        groups=defaultdict(list)
        for doc in documents(dt,rule.company):
            if doc.meta.has_field('active') and not doc.active: continue
            if doc.meta.is_submittable and doc.docstatus!=1: continue
            end=doc.get('effective_to'); start=doc.get('effective_from')
            if end and getdate(end)<today:
                add(doc,'An active fiscal configuration has reached the end of its effective period. Confirm whether a successor is required.','expired')
                continue
            key=(doc.get('tax_type'),doc.get('declaration_type'),doc.get('declaration_purpose'),
                 doc.get('account'),doc.get('account_role'),doc.get('mechanism'))
            groups[key].append(doc)
            if start and getdate(start)>today: continue
            if dt=='Tax Deadline Configuration' and doc.declaration_purpose=='Declaration':
                typ=frappe.get_doc('Tax Declaration Type',doc.declaration_type)
                if len(doc.deadline_rules or [])!=1 or (doc.deadline_rules and doc.deadline_rules[0].deadline_type!='Submission'):
                    add(doc,'The declaration configuration has no single valid submission deadline rule.','missing-rule')
                if doc.declaration_applicability=='Needs Review' or typ.verification_state!='Verified':
                    add(doc,'Declaration applicability or its filing type still needs verification.','applicability')
            for field,target in [('deadline_configuration','Tax Deadline Configuration'),('profile','Industrial Tax Profile'),('rule_version','Industrial Tax Rule')]:
                if doc.meta.has_field(field):
                    name=doc.get(field)
                    if not name or not frappe.db.exists(target,name): add(doc,f'A required linked {target.lower()} is missing.',field)
                    else:
                        linked=frappe.get_doc(target,name)
                        if linked.get('company') and linked.company!=doc.company: add(doc,'A linked configuration belongs to another company.',field)
                        elif linked.docstatus==2 or (linked.meta.has_field('active') and not linked.active) or (linked.get('effective_to') and getdate(linked.effective_to)<today):
                            add(doc,'A linked fiscal configuration is cancelled, inactive or outside its effective period.',field)
        for group in groups.values():
            for doc in group:
                overlaps=[other for other in group if other.name!=doc.name
                    and getdate(doc.get('effective_from') or '1900-01-01')<=getdate(other.get('effective_to') or '9999-12-31')
                    and getdate(other.get('effective_from') or '1900-01-01')<=getdate(doc.get('effective_to') or '9999-12-31')]
                if overlaps:add(doc,'Fiscal configurations overlap for the same current or future purpose. Resolve the ambiguity before use.','overlap')
    # Verify missing monetary configurations for known operational obligations only.
    configs=list(documents('Tax Deadline Configuration',rule.company))
    accounts=list(documents('Tax Account Configuration',rule.company))
    for doc in documents('Fiscal Obligation',rule.company):
        if doc.status in TERMINAL or doc.obligation_origin!='Operational': continue
        end=getdate(doc.fiscal_period_to) if doc.fiscal_period_to else None
        matches=[c for c in configs if c.docstatus==1 and c.active and c.declaration_purpose!='Declaration'
            and c.tax_type==doc.tax_type and (not c.effective_from or (end and getdate(c.effective_from)<=end))
            and (not c.effective_to or (end and end<=getdate(c.effective_to)))]
        if doc.liability_account and end:
            mapped=[c for c in accounts if c.docstatus==1 and c.active and c.tax_type==doc.tax_type and c.account==doc.liability_account
                and (not c.effective_from or getdate(c.effective_from)<=end) and (not c.effective_to or end<=getdate(c.effective_to))]
            if not mapped:add(doc,'No active tax account configuration matches this known obligation liability account and period.','missing-account')
        if not matches: add(doc,'No active monetary deadline configuration matches this known fiscal obligation and period.','missing-deadline')
        elif len(matches)>1: add(doc,'More than one monetary deadline configuration matches this known obligation and period.','ambiguous-deadline')
    return result
