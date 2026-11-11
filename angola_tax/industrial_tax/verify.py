"""Read-only post-migration inspection: bench --site SITE execute ...verify.run."""
import json
import frappe


def run():
    from frappe.model.base_document import get_controller
    expected = {
        'Fiscal Obligation': 'IndustrialFiscalObligation',
        'Tax Deadline Configuration': 'IndustrialDeadlineConfiguration',
        'Industrial Tax Computation': 'IndustrialTaxComputation',
        'Tax Assessment': 'IndustrialTaxAssessment',
        'Tax Settlement': 'IndustrialTaxSettlement',
        'Industrial Tax Mechanism Profile': 'IndustrialTaxMechanismProfile',
        'Industrial Tax Credit': 'IndustrialTaxCredit',
        'Industrial Tax Provisional': 'IndustrialTaxProvisional',
        'Industrial Tax Withholding': 'IndustrialTaxWithholding',
        'Industrial Tax Withholding Period': 'IndustrialTaxWithholdingPeriod',
        'Industrial Tax Revenue Configuration': 'IndustrialTaxRevenueConfiguration',
        'Industrial Tax AGT Import':'IndustrialTaxAGTImport',
    }
    result = {}
    for doctype, cls in expected.items():
        actual = get_controller(doctype).__name__
        result[doctype] = {'controller': actual, 'expected': cls, 'ok': actual == cls}
    for doctype, field in [('Fiscal Obligation','determination_basis'),
            ('Tax Account Configuration','ingestion_policy'), ('Tax Deadline Configuration','holiday_list'),
            ('Industrial Tax Computation','credit_allocations'), ('Industrial Tax Computation','offset_journal_entry'),
            ('Fiscal Obligation','tax_code'),('Industrial Tax Credit','eligibility_date'),('Industrial Tax Provisional','base_completeness_reviewed'),('Tax Settlement','gross_tax_amount'),('Tax Settlement','credit_applied_amount'),('Industrial Tax Provisional','revenue_accounts'),('Industrial Tax Provisional','source_format'),('Industrial Tax Provisional','revenue_configuration'),('Industrial Tax Credit','agt_import'),('Industrial Tax Credit','asset_recognition_journal'),('Industrial Tax Withholding','accounting_stage'),
            ('Industrial Tax Withholding Invoice','source_reference_row'),('Industrial Tax Withholding Period','gl_movements'),('Industrial Tax Withholding Period','map_invoices'),('Industrial Tax Withholding','invoice_lines'),('Industrial Tax Withholding Period','period_gl_hash'),
            ('Industrial Tax AGT Import','report_rows'),('Industrial Tax AGT Import','allocations')]:
        if not frappe.get_meta(doctype).has_field(field):
            frappe.throw(f'Missing migrated field {doctype}.{field}')
    for doctype in ['Industrial Tax Rule', 'Industrial Tax Profile', 'Industrial Tax Source',
            'Industrial Tax Adjustment', 'Industrial Tax Account Role', 'Industrial Tax Estimate',
            'Industrial Tax Withholding GL Line','Industrial Tax Credit Allocation','Industrial Tax Provisional Line','Industrial Tax Withholding Period Line','Industrial Tax Revenue Mapping','Industrial Tax Revenue Summary','Industrial Tax AGT Line','Industrial Tax AGT Allocation','Industrial Tax Withholding Invoice']:
        if not frappe.db.exists('DocType', doctype):
            frappe.throw(f'Missing DocType {doctype}')
    for key, value in result.items():
        if not value['ok']:
            frappe.throw(f'Controller overridden by another app: {key}: {value}')
    hooks=frappe.get_hooks('doc_events')
    for typ,event,path in [('Journal Entry','before_cancel','angola_tax.industrial_tax.guards.protect_source'),
            ('Tax Collection Reference','validate','angola_tax.industrial_tax.credits.protect_credit_confirmation'),
            ('Tax Assessment','validate','angola_tax.industrial_tax.configuration.protect_ii_assessment')]:
        if path not in hooks.get(typ,{}).get(event,[]):frappe.throw(f'Missing fiscal guard {typ}.{event}: {path}')
    result['stage2_guard_hooks']='ok'
    result['revenue_capture']='GL Revenue v1 / Income accounts / account-level exception review'
    result['monthly_withholding_capture']='GL Withholding v1 / payment to invoice or journal references / refresh submitted map / no new journal'
    result['agt_contract']='AGT monthly XLSX map / retained and paid report / reviewed credit allocations / no separate certificate'
    result['existing_annual_tax'] = frappe.db.get_value('Tax Type', 'IIMOD1', ['name','docstatus'], as_dict=True)
    result['configuration'] = frappe.db.get_value('Tax Account Configuration', 'TAXCFG-00231-1',
        ['name','docstatus','account','account_role','ingestion_policy'], as_dict=True)
    result['deadline'] = frappe.db.get_value('Tax Deadline Configuration','TAXDL-00232-3',
        ['name','docstatus','legal_due_timing','legal_due_day_rule'], as_dict=True)
    print(json.dumps(result, indent=2, default=str))
    return result
