import hashlib
from angola_tax.industrial_tax import revenue
from decimal import Decimal
import frappe
from frappe.model.document import Document
from frappe.utils import getdate, now
from angola_tax.industrial_tax.model import money
from angola_tax.industrial_tax.configuration import role_account, get_company_currency
from angola_tax.industrial_tax.computation import fingerprint
from angola_tax.industrial_tax.credit_model import provisional, quantized, last_working_month


ROLES = {
    'Own Provisional': ('Provisional Liability','Provisional Asset'),
    'Suffered Withholding': ('Suffered Withholding Asset',),
    'Collected Resident Withholding': ('Collected Resident Withholding Liability',),
    'Collected Nonresident Withholding': ('Collected Nonresident Withholding Liability',),
}


class IndustrialTaxMechanismProfile(Document):
    def validate(self):
        annual = frappe.get_doc('Industrial Tax Profile',self.annual_profile)
        if annual.company != self.company:
            frappe.throw('Mechanism and annual profile company must agree.')
        self.annual_tax_type = annual.tax_type
        expected = 2 if self.mechanism == 'Own Provisional' else 6.5
        if self.mechanism not in ROLES or money(self.rate) != money(expected):
            frappe.throw('Only general-regime provisional 2% and reviewed ordinary service withholding 6.5% are enabled.')
        if self.amount_precision not in (0,2):
            frappe.throw('Approve tax monetary precision (0 or 2).')
        if getdate(self.effective_from) > getdate(self.effective_to):
            frappe.throw('Invalid mechanism profile dates.')
        seen = set()
        for row in self.account_roles or []:
            if row.role in seen:
                frappe.throw('Duplicate account role.')
            seen.add(row.role)
            config=frappe.get_doc('Tax Account Configuration',row.configuration)
            if config.company!=self.company or config.tax_type!=self.tax_type or config.account_role!=row.role:
                frappe.throw('Role mapping must match company, mechanism Tax Type and account role.')
        if self.mechanism=='Suffered Withholding':
            if self.tax_type!=self.annual_tax_type:
                frappe.throw('Suffered own-tax credit uses the existing annual II Tax Type.')
        elif self.tax_type==self.annual_tax_type:
            frappe.throw('Provisional and collected-withholding debts require distinct Tax Type identities.')

    def before_submit(self):
        self.validate()
        frappe.db.sql('select name from `tabCompany` where name=%s for update',self.company)
        if frappe.db.exists('Industrial Tax Mechanism Profile',dict(company=self.company,mechanism=self.mechanism,docstatus=1,name=['!=',self.name],effective_from=['<=',self.effective_to],effective_to=['>=',self.effective_from])):
            frappe.throw('A submitted mechanism profile already covers these dates; amend the approved configuration deliberately.')
        annual=frappe.get_doc('Industrial Tax Profile',self.annual_profile)
        tax=frappe.get_doc('Tax Type',self.tax_type)
        if annual.docstatus!=1 or tax.docstatus!=1 or not tax.active or tax.company!=self.company:
            frappe.throw('Submit the matching annual profile and active company Tax Type first.')
        if self.mechanism!='Suffered Withholding' and tax.tax_code in ('IRTI','IRTD','INSS'):
            frappe.throw('Do not reuse a payroll/IRTI Tax Type for industrial withholding or provisional tax.')
        expected_period='Annual' if self.mechanism=='Own Provisional' else 'Monthly'
        if self.mechanism!='Suffered Withholding' and (tax.obligation_type!='Payable' or tax.periodicity!=expected_period):
            frappe.throw(f'Mechanism requires Payable / {expected_period} Tax Type.')
        if not self.scope_reviewed or not self.approval_evidence or not self.legal_reference:
            frappe.throw('Review service scope, exclusions, exemptions and legal rate; attach approval evidence.')
        for role in sorted(set(ROLES[self.mechanism])|{r.role for r in self.account_roles or []}):
            role_account(self,role,self.effective_from)
        if self.mechanism!='Suffered Withholding':
            rule=frappe.get_doc('Tax Deadline Configuration',self.deadline_configuration)
            timing='Second Following Month' if self.mechanism=='Own Provisional' else 'Next Month'
            if (rule.docstatus!=1 or not rule.active or rule.company!=self.company or rule.tax_type!=self.tax_type
                    or rule.legal_due_timing!=timing or rule.legal_due_day_rule!=('End of Month' if self.mechanism=='Collected Nonresident Withholding' else 'Last Working Day')):
                frappe.throw(f'Mechanism deadline requires submitted {timing} and the approved statutory day rule.')


def mechanism_context(doc, expected=None):
    profile=frappe.get_doc('Industrial Tax Mechanism Profile',doc.profile)
    if profile.company!=doc.company or (expected and profile.mechanism!=expected):
        frappe.throw('Mechanism profile mismatch.')
    day=getdate(doc.get('event_date') or doc.fiscal_period_to)
    if day<getdate(profile.effective_from) or day>getdate(profile.effective_to):
        frappe.throw('Mechanism profile is not effective for the event/exercise.')
    if doc.get('fiscal_period_from') and getdate(doc.fiscal_period_from)<getdate(profile.effective_from):
        frappe.throw('Mechanism profile must cover the complete source period.')
    doc.tax_type,doc.annual_tax_type=profile.tax_type,profile.annual_tax_type
    doc.currency=get_company_currency(doc.company)
    return profile


def due_date(doc):
    profile=frappe.get_doc('Industrial Tax Mechanism Profile',doc.profile)
    rule=frappe.get_doc('Tax Deadline Configuration',profile.deadline_configuration)
    end=getdate(doc.fiscal_period_to)
    offset=2 if profile.mechanism=='Own Provisional' else 1
    timing='Second Following Month' if offset==2 else 'Next Month'
    if (rule.docstatus!=1 or not rule.active or rule.company!=doc.company or rule.tax_type!=doc.tax_type
            or rule.legal_due_timing!=timing or rule.legal_due_day_rule!=('End of Month' if profile.mechanism=='Collected Nonresident Withholding' else 'Last Working Day')
            or end<getdate(rule.effective_from) or (rule.effective_to and end>getdate(rule.effective_to))):
        frappe.throw('Submitted mechanism deadline must match tax, period and last-working-day rule.')
    from calendar import monthrange
    serial=end.year*12+end.month-1+offset
    year,month0=divmod(serial,12)
    nominal=end.replace(year=year,month=month0+1,day=1)
    nominal=nominal.replace(day=monthrange(year,month0+1)[1])
    if profile.mechanism=='Collected Nonresident Withholding':return nominal
    calendar=frappe.get_doc('Holiday List',rule.holiday_list)
    month_start=nominal.replace(day=1)
    from calendar import monthrange
    month_end=nominal.replace(day=monthrange(nominal.year,nominal.month)[1])
    if getdate(calendar.from_date)>month_start or getdate(calendar.to_date)<month_end:
        frappe.throw('Holiday List must cover the entire mechanism payment month.')
    return last_working_month(end,offset,[getdate(r.holiday_date) for r in calendar.holidays])


def make_obligation(doc,liability):
    existing=frappe.db.exists('Fiscal Obligation',dict(company=doc.company,tax_type=doc.tax_type,
        fiscal_period_from=doc.fiscal_period_from,fiscal_period_to=doc.fiscal_period_to,docstatus=['!=',2]))
    if existing:frappe.throw('An obligation already exists for this mechanism/period; reconcile it first.')
    tax=frappe.get_doc('Tax Type',doc.tax_type)
    obligation=frappe.get_doc(dict(doctype='Fiscal Obligation',company=doc.company,tax_type=doc.tax_type,
        tax_name=tax.tax_name,obligation_type='Payable',obligation_origin='Operational',source_module='Accounting',
        source_document_type=doc.doctype,source_document=doc.name,determination_basis='Computation',
        fiscal_period_from=doc.fiscal_period_from,fiscal_period_to=doc.fiscal_period_to,currency=doc.currency,
        liability_account=liability,responsible_user=doc.responsible_user,reviewer=doc.approved_by))
    obligation.insert();doc.db_set('fiscal_obligation',obligation.name)
    return obligation


class IndustrialTaxProvisional(Document):
    def validate(self):
        profile=mechanism_context(self,'Own Provisional')
        start,end=getdate(self.fiscal_period_from),getdate(self.fiscal_period_to)
        if start!=start.replace(month=1,day=1) or end!=start.replace(month=6,day=30):
            frappe.throw('Provisional source period must be 1 January to 30 June of the same year.')
        self.fiscal_year=str(start.year)
        self.period_key=hashlib.sha256(f'{self.company}|{self.tax_type}|{self.fiscal_year}|provisional'.encode()).hexdigest()
        if self.source_format == 'GL Revenue v1':
            values = revenue.calculate(self)
            if self.base_review_complete:
                rows = [dict(amount=values['taxable_base'],classification='Eligible',reason='Approved revenue account bridge')]
                base,raw,tax=provisional(rows,profile.amount_precision,self.exemption_basis or None)
                self.taxable_base,self.tax_raw,self.tax_amount=float(base),float(raw),float(tax)
            else:
                self.taxable_base=self.tax_raw=self.tax_amount=0
        else:
            # Preserve reading and accounting of already approved legacy documents.
            reviewed=all(r.classification in ('Eligible','Excluded') and r.reason for r in self.source_lines or [])
            self.base_review_complete=int(reviewed)
            rows=[dict(amount=r.base_amount,classification=r.classification,reason=r.reason) for r in self.source_lines or []]
            if reviewed:
                base,raw,tax=provisional(rows,profile.amount_precision,self.exemption_basis or None)
                self.taxable_base,self.tax_raw,self.tax_amount=float(base),float(raw),float(tax)
            else:self.taxable_base=self.tax_raw=self.tax_amount=0
        self.rounding_difference=float(money(self.tax_amount)-money(self.tax_raw))
        self.net_cash_requirement=self.tax_amount
        self.credit_applied_amount=0
        self.asset_configuration=next((r.configuration for r in profile.account_roles if r.role=='Provisional Asset'),None)

    def source_query(self):
        return frappe.db.sql('''select i.name as source_row,i.parent as sales_invoice,
              s.posting_date,i.item_code,i.base_net_amount as base_amount
            from `tabSales Invoice Item` i join `tabSales Invoice` s on s.name=i.parent
            where s.company=%s and s.docstatus=1 and s.posting_date between %s and %s
              and coalesce(s.is_opening,'No')!='Yes' order by i.name''',
            (self.company,self.fiscal_period_from,self.fiscal_period_to),as_dict=True)

    def source_fingerprint(self,rows):
        import json
        data=[dict(source_row=r.get('source_row'),sales_invoice=r.get('sales_invoice'),
              posting_date=str(r.get('posting_date')),item_code=r.get('item_code'),
              base_amount=format(money(r.get('base_amount')).normalize(),'f')) for r in rows]
        return hashlib.sha256(json.dumps(sorted(data,key=lambda r:r['source_row']),sort_keys=True).encode()).hexdigest()

    @frappe.whitelist()
    def capture_source(self):
        self.check_permission('write')
        if self.docstatus!=0:frappe.throw('Capture H1 sources while Draft.')
        mechanism_context(self,'Own Provisional')
        count=revenue.capture(self)
        self.save()
        return count

    @frappe.whitelist()
    def get_revenue_detail(self, account, source='GL', page=1):
        self.check_permission('read')
        if account not in {r.account for r in self.revenue_accounts or []}:
            frappe.throw('Select an account from the captured revenue summary.')
        if source not in ('GL','Invoices'):frappe.throw('Choose GL or Invoices.')
        if not frappe.has_permission('GL Entry' if source=='GL' else 'Sales Invoice','read'):
            frappe.throw('Read permission is required for source accounting documents.')
        page=max(int(page),1);size=25
        gl,invoices,mapping=revenue.sources(self)
        rows=([r for r in gl if r.account==account] if source=='GL' else
              [r for r in invoices if r.income_account==account])
        return dict(rows=rows[(page-1)*size:page*size],total=len(rows),page=page,
                    source_changed=revenue.revenue_model.source_hash(gl,invoices,mapping)!=self.source_hash)

    def before_submit(self):
        if self.accounting_complete or self.recognition_journal_entry or self.fiscal_obligation:
            frappe.throw('Draft provisional accounting links must be empty; accounting is completed by submission.')
        profile=mechanism_context(self,'Own Provisional')
        if profile.docstatus!=1 or not self.review_complete or not self.base_completeness_reviewed or not self.review_evidence:
            frappe.throw('Approve the profile, H1 revenue-account review and completeness reconciliation.')
        if not self.existing_recognition_journal and not self.no_existing_recognition_confirmed:
            frappe.throw('Adopt an existing provisional recognition journal or confirm reconciliation found no existing recognition.')
        if self.existing_recognition_journal:self.validate_existing_recognition(profile)
        if self.source_format != 'GL Revenue v1' or not self.source_hash:
            frappe.throw('Capture the H1 GL account summary before approval; old Draft invoice captures must be recaptured.')
        revenue.assert_current(self)
        values=revenue.calculate(self,strict=True)
        base,raw,tax=provisional([dict(amount=values['taxable_base'],classification='Eligible',
            reason='Reviewed GL revenue bridge')],profile.amount_precision,self.exemption_basis or None)
        self.taxable_base,self.tax_raw,self.tax_amount=float(base),float(raw),float(tax)
        self.net_cash_requirement=self.tax_amount
        self.rounding_difference=float(money(self.tax_amount)-money(self.tax_raw))
        if self.exemption_basis and not self.exemption_evidence:
            frappe.throw('Provisional exemption requires statutory eligibility and communication evidence.')
        self.approved_by,self.approved_on=frappe.session.user,now()

    def on_submit(self):
        profile=mechanism_context(self,'Own Provisional')
        make_obligation(self,role_account(profile,'Provisional Liability',self.fiscal_period_to))
        # Keep determination, journal and obligation in the submission transaction.
        # Do not catch posting failures or commit here: Frappe rolls back Submit.
        self._complete_accounting()
        if not self.accounting_complete:
            frappe.throw('Provisional submission requires completed accounting.')

    @frappe.whitelist()
    def complete_accounting(self):
        self.check_permission('submit')
        if self.docstatus!=1:frappe.throw('Approve provisional determination first.')
        frappe.db.sql('select name from `tabIndustrial Tax Provisional` where name=%s for update',self.name)
        self.reload()
        return self._complete_accounting()

    def _complete_accounting(self):
        if self.accounting_complete:return self.fiscal_obligation
        profile=mechanism_context(self,'Own Provisional')
        if self.source_format == 'GL Revenue v1':
            revenue.assert_current(self)
            revenue.calculate(self,strict=True)
        elif self.source_fingerprint(self.source_query())!=self.source_hash:
            frappe.throw('Approved H1 sources changed; controlled correction is required.')
        if getdate(self.accounting_date)<getdate(self.fiscal_period_to):
            frappe.throw('Provisional accounting date cannot precede H1 end.')
        asset=role_account(profile,'Provisional Asset',self.accounting_date)
        liability=role_account(profile,'Provisional Liability',self.accounting_date)
        if asset==liability:frappe.throw('Provisional asset and liability must differ.')
        if self.existing_recognition_journal:
            self.validate_existing_recognition(profile)
            self.db_set('recognition_journal_entry',self.existing_recognition_journal)
            obligation=frappe.get_doc('Fiscal Obligation',self.fiscal_obligation)
            from angola_tax.gl_ingestion import add_gl_movement
            config=frappe.get_doc('Tax Account Configuration',next(r.configuration for r in profile.account_roles if r.role=='Provisional Liability'))
            for gle in frappe.get_all('GL Entry',filters=dict(company=self.company,voucher_type='Journal Entry',voucher_no=self.existing_recognition_journal,account=liability,is_cancelled=0),fields=['name','posting_date','company','account','voucher_type','voucher_no','debit','credit','remarks'],limit_page_length=0):
                owner=frappe.db.get_value('Fiscal Obligation Movement',{'gl_entry':gle.name},'parent')
                if owner and owner!=obligation.name:frappe.throw('Existing provisional GL belongs to another obligation; reconcile first.')
                if not owner:add_gl_movement(obligation,gle,config,'Reclassification','Automatic')
            obligation.save()
        elif money(self.tax_amount):
            je=frappe.new_doc('Journal Entry');je.company=self.company;je.posting_date=self.accounting_date
            je.voucher_type='Journal Entry';je.user_remark=f'Own provisional II / {self.name} / {self.fiscal_year}'
            je.append('accounts',dict(account=asset,cost_center=self.cost_center,debit_in_account_currency=self.tax_amount))
            je.append('accounts',dict(account=liability,cost_center=self.cost_center,credit_in_account_currency=self.tax_amount))
            je.insert();self.db_set('recognition_journal_entry',je.name);je.submit()
        # The obligation controller reads this flag from the source document.
        # Set it after journal submission, within the same uncommitted transaction.
        self.db_set('accounting_complete',1)
        obligation=frappe.get_doc('Fiscal Obligation',self.fiscal_obligation)
        if obligation.docstatus==0:obligation.submit()
        if obligation.docstatus!=1:
            frappe.throw('Provisional accounting requires a submitted fiscal obligation.')
        return obligation.name

    def validate_existing_recognition(self,profile):
        name=self.existing_recognition_journal
        frappe.db.sql('select name from `tabJournal Entry` where name=%s for update',name)
        je=frappe.get_doc('Journal Entry',name)
        asset=role_account(profile,'Provisional Asset',je.posting_date)
        liability=role_account(profile,'Provisional Liability',je.posting_date)
        if (je.docstatus!=1 or je.company!=self.company or je.is_opening=='Yes'
                or not self.recognition_evidence or getdate(je.posting_date)<getdate(self.fiscal_period_to)
                or any(r.account not in (asset,liability) for r in je.accounts)):
            frappe.throw('Adopt a reviewed submitted non-opening provisional asset/liability journal for this exercise.')
        if frappe.db.exists('Industrial Tax Provisional',{'recognition_journal_entry':name,'docstatus':1,'name':['!=',self.name]}):
            frappe.throw('Provisional journal is already owned by another determination.')
        rows=frappe.get_all('GL Entry',filters=dict(company=self.company,voucher_type='Journal Entry',voucher_no=name,is_cancelled=0),fields=['account','debit','credit'],limit_page_length=0)
        debit=sum((money(r.debit)-money(r.credit) for r in rows if r.account==asset),Decimal(0))
        credit=sum((money(r.credit)-money(r.debit) for r in rows if r.account==liability),Decimal(0))
        if not rows or debit!=money(self.tax_amount) or credit!=debit:
            frappe.throw('Existing provisional recognition must exactly reconcile the approved amount; reconcile partial or multiple journals before adoption.')

    @frappe.whitelist()
    def create_credit(self,collection_reference):
        self.check_permission('read')
        if self.docstatus!=1 or not self.accounting_complete:frappe.throw('Complete provisional accounting first.')
        existing=frappe.db.get_value('Industrial Tax Credit',dict(source_provisional=self.name,
            collection_reference=collection_reference,docstatus=['!=',2]),'name')
        if existing:return existing
        credit=frappe.get_doc(dict(doctype='Industrial Tax Credit',company=self.company,
            credit_kind='Paid Provisional',source_provisional=self.name,collection_reference=collection_reference))
        credit.insert();return credit.name

    def before_cancel(self):frappe.throw('Approved provisional tax requires controlled revision; direct cancellation is disabled.')
    def before_update_after_submit(self):frappe.throw('Approved provisional determination is immutable.')


def withholding_role(doc,profile):
    if profile.mechanism=='Suffered Withholding' and doc.get('accounting_stage')=='Pending Evidence':
        return 'Pending Suffered Withholding Asset'
    return ROLES[profile.mechanism][0]


class IndustrialTaxWithholding(Document):
    def validate(self):
        profile=mechanism_context(self)
        if profile.mechanism not in ROLES or profile.mechanism=='Own Provisional':
            frappe.throw('Select a withholding mechanism profile.')
        self.mechanism=profile.mechanism
        role=withholding_role(self,profile)
        self.account_configuration=next((r.configuration for r in profile.account_roles if r.role==role),None)
        self.party_type='Customer' if self.mechanism=='Suffered Withholding' else 'Supplier'
        self.fiscal_year=str(getdate(self.event_date).year)
        self.tax_raw=float(money(self.taxable_base)*money(profile.rate)/100)
        self.tax_amount=float(quantized(self.tax_raw,profile.amount_precision))
        recognised=money(self.get('recognised_tax_amount'))
        if recognised<0 or recognised!=quantized(recognised,profile.amount_precision):
            frappe.throw('Adopted GL tax must be nonnegative and match the approved amount precision.')
        if recognised>0:
            self.tax_amount=float(money(self.recognised_tax_amount))
        self.rounding_difference=float(money(self.tax_amount)-quantized(self.tax_raw,profile.amount_precision))
        if money(self.taxable_base)<=0:frappe.throw('Document the positive service base excluding VAT and excluded components.')
        self.event_key=hashlib.sha256(f'{self.company}|{self.mechanism}|{self.event_voucher_type}|{self.event_voucher}|{self.party}'.encode()).hexdigest()
        self.validate_vouchers()

    def validate_vouchers(self):
        for typ,num in [(self.event_voucher_type,self.event_voucher),
                        (self.accounting_voucher_type,self.accounting_voucher)]:
            if typ not in ('Payment Entry','Journal Entry','Sales Invoice','Purchase Invoice'):
                frappe.throw('Unsupported withholding source voucher.')
            doc=frappe.get_doc(typ,num)
            if doc.docstatus!=1 or doc.company!=self.company:
                frappe.throw('Withholding evidence must reference submitted company vouchers.')
            party=doc.get('party') or doc.get('customer') or doc.get('supplier')
            if party and party!=self.party:frappe.throw('Withholding counterparty differs from source voucher.')
            expected='Customer' if self.mechanism=='Suffered Withholding' else 'Supplier'
            if typ=='Payment Entry' and doc.party_type!=expected:frappe.throw('Payment counterparty type does not match withholding mechanism.')
            if (typ=='Sales Invoice' and expected!='Customer') or (typ=='Purchase Invoice' and expected!='Supplier'):
                frappe.throw('Invoice side does not match withholding mechanism.')
        event=frappe.get_doc(self.event_voucher_type,self.event_voucher)
        if self.event_voucher_type not in ('Payment Entry','Journal Entry'):
            frappe.throw('Withholding event requires effective payment or a reviewed event journal; invoice accrual alone is not evidence of withholding payment.')
        if getdate(event.posting_date)!=getdate(self.event_date):
            frappe.throw('Withholding event date must match the payment/event journal date.')

    def source_query(self):
        profile=mechanism_context(self)
        account=role_account(profile,withholding_role(self,profile),self.event_date)
        return frappe.get_all('GL Entry',filters=dict(company=self.company,account=account,
            voucher_type=self.accounting_voucher_type,voucher_no=self.accounting_voucher,is_cancelled=0),
            fields=['name as gl_entry','posting_date','account','voucher_type','voucher_no','debit','credit'],
            order_by='name',limit_page_length=0)

    @frappe.whitelist()
    def capture_source(self):
        self.check_permission('write')
        if self.docstatus!=0:frappe.throw('Capture withholding evidence while Draft.')
        self.validate();rows=self.source_query();self.set('source_rows',[])
        for row in rows:self.append('source_rows',row)
        self.source_hash=fingerprint(rows);self.review_complete=0;self.save();return len(rows)

    def before_submit(self):
        profile=mechanism_context(self)
        if profile.docstatus!=1 or not self.review_complete or not self.review_evidence:
            frappe.throw('Approve profile, statutory service/payment scope and review evidence.')
        from angola_tax.industrial_tax import withholding_map
        if self.mechanism!='Suffered Withholding' and not self.get('invoice_lines'):
            frappe.throw('Review invoice/payment allocations before approving collected withholding.')
        withholding_map.validate_lines(self)
        if money(self.get('rounding_difference')):
            if not self.rounding_review_complete or not self.rounding_reason:
                frappe.throw('Review and explain adopted GL withholding rounding differences.')
            if not self.get('invoice_lines') and abs(money(self.rounding_difference))>Decimal('.01'):
                frappe.throw('Rounding difference exceeds 0.01; provide reviewed invoice allocations or use controlled correction.')
        frappe.db.sql('select name from `tabCompany` where name=%s for update',self.company)
        if self.mechanism!='Suffered Withholding' and frappe.db.exists('Industrial Tax Withholding Period',dict(company=self.company,tax_type=self.tax_type,docstatus=1,fiscal_period_from=['<=',self.event_date],fiscal_period_to=['>=',self.event_date])):
            frappe.throw('This withholding month is already approved; controlled correction is required.')
        rows=self.source_query()
        if not rows or not self.source_hash or fingerprint(rows)!=self.source_hash or fingerprint(self.source_rows or [])!=self.source_hash:
            frappe.throw('Withholding GL evidence changed or is missing. Capture and review again.')
        for row in sorted(rows,key=lambda r:r.gl_entry):
            frappe.db.sql('select name from `tabGL Entry` where name=%s for update',row.gl_entry)
        rows=self.source_query()
        if fingerprint(rows)!=self.source_hash:frappe.throw('Withholding GL evidence changed while approval was pending.')
        sign=1 if self.mechanism=='Suffered Withholding' else -1
        ledger=sum((money(r.debit)-money(r.credit) for r in rows),Decimal(0))*sign
        used=frappe.db.sql('''select coalesce(sum(w.tax_amount),0) from `tabIndustrial Tax Withholding` w
             join `tabTax Account Configuration` c on c.name=w.account_configuration
             where w.company=%s and c.account=%s and w.accounting_voucher_type=%s
               and w.accounting_voucher=%s and w.docstatus=1 and w.name!=%s''',
             (self.company,rows[0].account,self.accounting_voucher_type,self.accounting_voucher,self.name))[0][0]
        if money(used)+money(self.tax_amount)>ledger:
            frappe.throw('Withholding exceeds the unregistered GL asset/liability evidence; do not recognise it a second time.')
        self.approved_by,self.approved_on=frappe.session.user,now()

    @frappe.whitelist()
    def capture_invoice_allocations(self):
        self.check_permission('write')
        from angola_tax.industrial_tax.withholding_map import capture_invoice
        return capture_invoice(self)

    @frappe.whitelist()
    def create_credit(self):
        self.check_permission('read')
        if self.docstatus!=1 or self.mechanism!='Suffered Withholding':
            frappe.throw('Only approved suffered withholding creates an own-tax credit.')
        existing=frappe.db.get_value('Industrial Tax Credit',dict(source_withholding=self.name,docstatus=['!=',2]),'name')
        if existing:return existing
        credit=frappe.get_doc(dict(doctype='Industrial Tax Credit',company=self.company,
            credit_kind='Suffered Withholding',source_withholding=self.name))
        credit.insert();return credit.name

    def before_cancel(self):frappe.throw('Approved withholding evidence requires controlled revision; direct cancellation is disabled.')
    def before_update_after_submit(self):frappe.throw('Approved withholding evidence is immutable.')


class IndustrialTaxWithholdingPeriod(Document):
    def validate(self):
        profile=mechanism_context(self)
        if profile.mechanism not in ('Collected Resident Withholding','Collected Nonresident Withholding'):
            frappe.throw('Collected-withholding monthly control requires a matching profile.')
        start,end=getdate(self.fiscal_period_from),getdate(self.fiscal_period_to)
        from calendar import monthrange
        if start.day!=1 or end!=start.replace(day=monthrange(start.year,start.month)[1]):
            frappe.throw('Monthly withholding must cover one complete calendar month.')
        self.fiscal_year=str(start.year)
        self.period_key=hashlib.sha256(f'{self.company}|{self.tax_type}|{start}|withholding'.encode()).hexdigest()
        if self.get('source_format') == 'GL Withholding v1':
            from angola_tax.industrial_tax.withholding_gl import amounts
            data=amounts(self.gl_movements or [])
            self.tax_raw=data['tax'];self.pending_gl_count=data['pending'];self.gl_settlement_total=data['settlement']
        else:
            self.tax_raw=float(sum((money(r.tax_amount) for r in self.withholdings or []),Decimal(0)))
        self.tax_amount=self.tax_raw;self.net_cash_requirement=self.tax_amount
        self.rounding_difference=self.credit_applied_amount=0

    def source_query(self):
        profile=mechanism_context(self)
        return frappe.get_all('Industrial Tax Withholding',filters=dict(company=self.company,
            tax_type=profile.tax_type,mechanism=profile.mechanism,docstatus=1,
            event_date=['between',[self.fiscal_period_from,self.fiscal_period_to]]),
            fields=['name as withholding','tax_amount'],order_by='name',limit_page_length=0)

    @frappe.whitelist()
    def capture_source(self):
        self.check_permission('write')
        if self.docstatus!=0:frappe.throw('Capture monthly withholdings while Draft.')
        self.validate()
        from angola_tax.industrial_tax.withholding_gl import capture
        return capture(self)

    def capture_legacy_source(self):
        self.validate();self.set('withholdings',[])
        for row in self.source_query():self.append('withholdings',row)
        from angola_tax.industrial_tax.withholding_map import capture_gl_review
        capture_gl_review(self)
        self.review_complete=0;self.save();return len(self.withholdings)

    def before_submit(self):
        if self.get('source_format') == 'GL Withholding v1':
            from angola_tax.industrial_tax.withholding_gl import approve
            return approve(self)
        profile=mechanism_context(self)
        if profile.docstatus!=1 or not self.review_complete or not self.review_evidence:
            frappe.throw('Approve profile and complete monthly withholding reconciliation.')
        frappe.db.sql('select name from `tabCompany` where name=%s for update',self.company)
        expected={r.withholding:money(r.tax_amount) for r in self.source_query()}
        actual={r.withholding:money(r.tax_amount) for r in self.withholdings or []}
        if not expected or actual!=expected or len(actual)!=len(self.withholdings):
            frappe.throw('Monthly source changed or contains duplicate/missing registers. Capture and review again.')
        from angola_tax.industrial_tax.withholding_map import map_values, require_gl_review
        require_gl_review(self)
        map_values(self)
        self.approved_by,self.approved_on=frappe.session.user,now()

    @frappe.whitelist()
    def refresh_agt_invoice_links(self):
        self.check_permission('write')
        from angola_tax.industrial_tax.withholding_gl import refresh_map
        return refresh_map(self)

    @frappe.whitelist()
    def export_agt_map(self):
        from angola_tax.industrial_tax.withholding_map import export_map
        return export_map(self)

    def on_submit(self):
        if self.get('source_format') == 'GL Withholding v1':
            from angola_tax.industrial_tax.withholding_gl import adopt
            return adopt(self)
        profile=mechanism_context(self)
        obligation=make_obligation(self,role_account(profile,ROLES[profile.mechanism][0],self.fiscal_period_to))
        self.db_set('accounting_complete',1)
        # Adopt amounts from submitted withholding registers. Source journals have already recognised the liability.
        config=frappe.get_doc('Tax Account Configuration',next(r.configuration for r in profile.account_roles
            if r.role==ROLES[profile.mechanism][0]))
        for row in self.withholdings:
            register=frappe.get_doc('Industrial Tax Withholding',row.withholding)
            if frappe.db.exists('Industrial Tax Withholding Period Line',{'withholding':register.name,'parent':['!=',self.name],'docstatus':1}):
                frappe.throw('Withholding register already belongs to another submitted control.')
            obligation.append('movements',dict(posting_date=register.event_date,
                voucher_type=register.accounting_voucher_type,voucher_no=register.accounting_voucher,
                account=config.account,debit=0,credit=register.tax_amount,net_amount=register.tax_amount,
                movement_type='Reclassification',classification_status='Automatic',source_module='Accounting',
                remarks=f'Adopted withholding register {register.name}; partial GL allocation tracked in register'))
        obligation.save();obligation.submit()

    def before_cancel(self):frappe.throw('Submitted monthly withholding requires controlled revision.')
    def before_update_after_submit(self):
        if self.get('source_format') == 'GL Withholding v1':
            from angola_tax.industrial_tax.withholding_gl import check_map_update
            return check_map_update(self)
        frappe.throw('Submitted monthly withholding control is immutable.')
