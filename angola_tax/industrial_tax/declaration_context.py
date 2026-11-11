"""Read-only contract for the future declarative-obligations engine (no AGT submission)."""
import frappe
from angola_tax.industrial_tax.model import money

SUPPORTED=('Industrial Tax Computation','Industrial Tax Provisional','Industrial Tax Withholding Period')


@frappe.whitelist()
def get_context(source_document_type,source_document):
    if source_document_type not in SUPPORTED:frappe.throw('Unsupported industrial-tax declaration source.')
    doc=frappe.get_doc(source_document_type,source_document);doc.check_permission('read')
    if doc.docstatus!=1 or not doc.accounting_complete or not doc.fiscal_obligation:
        frappe.throw('Declaration context requires an approved source with completed accounting and obligation.')
    obligation=frappe.get_doc('Fiscal Obligation',doc.fiscal_obligation);obligation.check_permission('read')
    from angola_tax.industrial_tax.adapters import source
    source(obligation)
    from angola_tax.payment_control import get_confirmed_amount
    profile_type='Industrial Tax Profile' if source_document_type=='Industrial Tax Computation' else 'Industrial Tax Mechanism Profile'
    profile=frappe.get_doc(profile_type,doc.profile)
    return dict(contract_version='1.0',source_document_type=doc.doctype,source_document=doc.name,
        company=doc.company,tax_type=doc.tax_type,currency=doc.currency,
        fiscal_period_from=str(doc.fiscal_period_from),fiscal_period_to=str(doc.fiscal_period_to),
        profile=doc.profile,rule_version=doc.get('rule_version'),deadline_configuration=profile.deadline_configuration,
        source_hash=doc.get('source_hash'),approved_by=doc.approved_by,approved_on=str(doc.approved_on),
        gross_tax=format(money(doc.tax_amount),'f'),credit_applied=format(money(doc.credit_applied_amount),'f'),
        net_cash_requirement=format(money(doc.net_cash_requirement),'f'),
        confirmed_cash=format(money(get_confirmed_amount(fiscal_obligation=obligation.name)),'f'),
        fiscal_obligation=obligation.name,filing_due_date=str(obligation.filing_due_date),
        filing_date=str(obligation.filing_date) if obligation.filing_date else None,
        declaration_reference=obligation.declaration_reference,
        assessment=obligation.latest_tax_assessment,settlement=obligation.latest_tax_settlement,
        recognition_journal_entry=doc.get('recognition_journal_entry'),offset_journal_entry=doc.get('offset_journal_entry'),
        credit_allocations=[dict(credit=r.credit,amount=format(money(r.amount),'f'),asset_account=r.asset_account)
            for r in doc.get('credit_allocations') or []],
        withholdings=[dict(withholding=r.withholding,tax_amount=format(money(r.tax_amount),'f'))
            for r in doc.get('withholdings') or []])
