"""GL adapter and effective, approved revenue-account classification."""
import frappe
from frappe.model.document import Document
from frappe.utils import getdate
from angola_tax.industrial_tax.configuration import get_company_currency
from angola_tax.industrial_tax import revenue_model


class IndustrialTaxRevenueConfiguration(Document):
    def validate(self):
        if getdate(self.effective_from)>getdate(self.effective_to):frappe.throw('Invalid revenue configuration dates.')
        seen=set()
        for row in self.accounts or []:
            if row.account in seen:frappe.throw('Map each revenue account once.')
            seen.add(row.account)
            account=frappe.get_doc('Account',row.account)
            if account.company!=self.company or account.is_group or account.disabled or account.root_type!='Income':
                frappe.throw('Revenue mapping requires active company Income leaf accounts.')
            if row.classification not in ('Eligible','Excluded','Mixed'):
                frappe.throw('Classify each revenue account.')

    def before_submit(self):
        self.validate()
        if not self.accounts:frappe.throw('Map the company revenue accounts before approval.')
        for row in self.accounts or []:
            if not row.reason:frappe.throw('Record the fiscal classification reason for each revenue account.')
            if row.classification in ('Excluded','Mixed') and not row.legal_reference:
                frappe.throw('Excluded or mixed revenue requires a legal reference.')
        if not self.review_complete or not self.review_evidence:frappe.throw('Review the revenue classifications and attach evidence.')

    def before_cancel(self):
        if frappe.db.exists('Industrial Tax Provisional',{'revenue_configuration':self.name,'docstatus':1}):
            frappe.throw('Approved computations use this revenue configuration; controlled revision is required.')

    def before_update_after_submit(self):frappe.throw('Approved revenue classifications are immutable; amend the configuration.')

    @frappe.whitelist()
    def load_income_accounts(self):
        self.check_permission('write')
        if self.docstatus!=0:frappe.throw('Load revenue accounts while Draft.')
        seen={r.account for r in self.accounts or []}
        for row in frappe.get_all('Account',filters=dict(company=self.company,is_group=0,disabled=0,root_type='Income'),fields=['name'],order_by='name',limit_page_length=0):
            if row.name not in seen:self.append('accounts',dict(account=row.name,classification='Mixed'))
        self.save();return len(self.accounts)


def configuration(doc):
    if not doc.revenue_configuration:frappe.throw('Select a submitted Revenue Configuration before capturing H1 revenue.')
    config=frappe.get_doc('Industrial Tax Revenue Configuration',doc.revenue_configuration)
    if (config.docstatus!=1 or config.company!=doc.company or
            getdate(config.effective_from)>getdate(doc.fiscal_period_from) or
            getdate(config.effective_to)<getdate(doc.fiscal_period_to)):
        frappe.throw('Revenue configuration must be approved and cover the company and complete H1 period.')
    config.validate()
    return config


def sources(doc):
    config=configuration(doc)
    gl=frappe.db.sql('''select g.name,g.account,g.posting_date,g.voucher_type,g.voucher_no,
        g.debit,g.credit,g.is_opening from `tabGL Entry` g join `tabAccount` a on a.name=g.account
        where g.company=%s and a.company=%s and a.root_type='Income' and a.is_group=0
          and g.is_cancelled=0 and coalesce(g.is_opening,'No')!='Yes'
          and g.voucher_type!='Period Closing Voucher' and g.posting_date between %s and %s
        order by g.name''',(doc.company,doc.company,doc.fiscal_period_from,doc.fiscal_period_to),as_dict=True)
    invoice_meta=frappe.get_meta('Sales Invoice');item_meta=frappe.get_meta('Sales Invoice Item')
    available=(all(invoice_meta.has_field(n) for n in ('que_retencao','base_retencao_fonte','total_retencao_na_fonte'))
               and item_meta.has_field('retencao_na_fonte'))
    evidence=("s.que_retencao as withholding_kind,s.base_retencao_fonte as invoice_withholding_base,"
              "s.total_retencao_na_fonte as invoice_withholding_total,i.retencao_na_fonte as invoice_withholding,"
              "1 as withholding_fields_available" if available else
              "null as withholding_kind,0 as invoice_withholding_base,0 as invoice_withholding_total,"
              "0 as invoice_withholding,0 as withholding_fields_available")
    invoices=frappe.db.sql('''select i.name as source_row,i.parent as sales_invoice,s.posting_date,
        i.item_code,i.income_account,i.base_net_amount as base_amount,i.net_amount as invoice_net_amount,
        s.currency as invoice_currency,s.conversion_rate,''' + evidence + '''
        from `tabSales Invoice Item` i join `tabSales Invoice` s on s.name=i.parent
        where s.company=%s and s.docstatus=1 and s.posting_date between %s and %s
          and coalesce(s.is_opening,'No')!='Yes' order by i.name''',
        (doc.company,doc.fiscal_period_from,doc.fiscal_period_to),as_dict=True)
    mapping=[dict(account=r.account,classification=r.classification,reason=r.reason,legal_reference=r.legal_reference)
             for r in config.accounts or []]
    return gl,invoices,mapping


def capture(doc):
    gl,invoices,mapping=sources(doc)
    totals=revenue_model.aggregate(gl,invoices,mapping,get_company_currency(doc.company))
    doc.set('revenue_accounts',[]);doc.set('source_lines',[])
    for row in totals:doc.append('revenue_accounts',{k:float(v) if hasattr(v,'as_tuple') else v for k,v in row.items()})
    doc.source_hash=revenue_model.source_hash(gl,invoices,mapping)
    doc.source_format='GL Revenue v1';doc.review_complete=0;doc.base_completeness_reviewed=0
    doc.gl_movement_count=len(gl);doc.invoice_line_count=len(invoices)
    return len(totals)


def assert_current(doc):
    gl,invoices,mapping=sources(doc)
    if revenue_model.source_hash(gl,invoices,mapping)!=doc.source_hash:
        frappe.throw('GL, invoice withholding or revenue classifications changed. Recapture Draft sources or use controlled correction after approval.')
    actual=revenue_model.aggregate(gl,invoices,mapping,get_company_currency(doc.company))
    protected=('account','classification','classification_reason','gl_revenue','credit','debit',
               'invoice_revenue','captured_withholding_base','reconciliation_difference',
               'withholding_exception_count','gl_count','invoice_line_count')
    def values(rows):
        return sorted([tuple(str(r.get(k) or '') if k in ('account','classification','classification_reason')
                       else revenue_model.money(r.get(k)) for k in protected) for r in rows],key=lambda v:v[0])
    if values(actual)!=values(doc.revenue_accounts or []):frappe.throw('Captured account totals changed; capture the source again.')


def calculate(doc,strict=False):
    try:values=revenue_model.calculate(doc.revenue_accounts or [],strict)
    except ValueError as exc:frappe.throw(str(exc))
    for key,value in values.items():setattr(doc,key,float(value) if hasattr(value,'as_tuple') else value)
    doc.base_review_complete=int(not values['pending_account_count'] and not values['unexplained_difference'])
    return values
