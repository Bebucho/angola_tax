import hashlib
from collections import defaultdict
from decimal import Decimal
import frappe
from frappe.model.document import Document
from frappe.utils import getdate, now
from angola_tax.industrial_tax.model import money, accepts_tax_credit_account
from angola_tax.industrial_tax.configuration import get_company_currency
from angola_tax.industrial_tax.credit_model import available, bridge


class IndustrialTaxCredit(Document):
    def validate(self):
        self.currency = get_company_currency(self.company)
        if self.credit_kind == 'Suffered Withholding':
            origin = frappe.get_doc('Industrial Tax Withholding', self.source_withholding)
            if origin.mechanism != 'Suffered Withholding' or origin.docstatus != 1:
                frappe.throw('Credit requires approved customer withholding.')
            origin.validate_vouchers()
            from angola_tax.industrial_tax.computation import fingerprint
            origin_rows=origin.source_query()
            if fingerprint(origin_rows) != origin.source_hash:
                frappe.throw('Approved withholding GL evidence changed; controlled correction is required.')
            self.eligibility_date=max([getdate(origin.event_date)]+[getdate(r.posting_date) for r in origin_rows])
            self.amount = origin.tax_amount
            if self.get('agt_import'):
                from angola_tax.industrial_tax.agt_import import credit_evidence
                amount,eligible_on=credit_evidence(self,origin)
                self.amount=float(amount);self.eligibility_date=eligible_on
            self.asset_configuration = origin.account_configuration
            if origin.get('accounting_stage')=='Pending Evidence':
                profile=frappe.get_doc('Industrial Tax Mechanism Profile',origin.profile)
                self.asset_configuration=next((r.configuration for r in profile.account_roles if r.role=='Suffered Withholding Asset'),None)
                if self.get('accounting_date'):
                    self.eligibility_date=max(getdate(self.eligibility_date),getdate(self.accounting_date))
            origin_company, annual_tax, exercise = origin.company, origin.annual_tax_type, origin.fiscal_year
            identity = f'withholding:{origin.name}'
            if self.get('agt_import'):identity+=f':agt:{self.agt_import}'
        elif self.credit_kind == 'Paid Provisional':
            origin = frappe.get_doc('Industrial Tax Provisional', self.source_provisional)
            reference = frappe.get_doc('Tax Collection Reference', self.collection_reference)
            if (origin.docstatus != 1 or not origin.accounting_complete
                    or reference.status != 'Confirmed' or reference.fiscal_obligation != origin.fiscal_obligation):
                frappe.throw('Unpaid provisional determinations are not annual credits. Confirm the linked collection reference first.')
            paid_on=reference.payment_date
            if not paid_on and reference.tax_payment:
                paid_on=frappe.db.get_value('Tax Payment',reference.tax_payment,'payment_date')
            if not paid_on:frappe.throw('Confirmed provisional credit requires the effective payment date.')
            self.eligibility_date=max(getdate(paid_on),getdate(frappe.db.get_value('Journal Entry',origin.recognition_journal_entry,'posting_date')))
            self.amount = reference.amount
            self.asset_configuration = origin.asset_configuration
            origin_company, annual_tax, exercise = origin.company, origin.annual_tax_type, origin.fiscal_year
            identity = f'provisional:{origin.name}:collection:{reference.name}'
        else:
            frappe.throw('Only suffered withholding and confirmed paid provisional credits are enabled.')
        if origin_company != self.company:
            frappe.throw('Credit origin belongs to another company.')
        self.annual_tax_type, self.fiscal_year = annual_tax, str(exercise)
        self.source_key = hashlib.sha256(identity.encode()).hexdigest()
        if money(self.amount) <= 0:
            frappe.throw('Credit must be positive.')
        config = frappe.get_doc('Tax Account Configuration', self.asset_configuration)
        account = frappe.get_doc('Account', config.account)
        expected_role = 'Provisional Asset' if self.credit_kind == 'Paid Provisional' else 'Suffered Withholding Asset'
        account_number = account.account_number or account.name.split(' - ', 1)[0]
        if (config.docstatus != 1 or not config.active or config.company != self.company
                or config.ingestion_policy != 'Disabled'
                or config.account_role != expected_role
                or account.company != self.company or account.is_group or account.disabled
                or not accepts_tax_credit_account(account.root_type, account_number, expected_role)
                or account.account_currency != self.currency):
            frappe.throw('Credit requires an active configured company-currency fiscal debit account (Asset or explicitly mapped PGC 3415 / 3419 under Liability).')
        self.asset_account = config.account
        self.committed_amount = float(committed(self.name)) if not self.is_new() else 0
        self.available_amount = float(available(self.amount, self.committed_amount))

    def before_submit(self):
        if self.get('asset_recognition_journal'):
            frappe.throw('Draft credit cannot supply a generated fiscal asset journal.')
        if not self.eligibility_reviewed or not self.eligibility_evidence:
            frappe.throw('Review statutory credit eligibility and attach evidence before approval.')
        if self.credit_kind == 'Suffered Withholding':
            if not self.get('agt_import'):
                frappe.throw('Approve and select the AGT report reconciliation before submitting this credit.')
            from angola_tax.industrial_tax.agt_import import lock_taxpayer
            lock_taxpayer(self.company)
            frappe.db.sql('select name from `tabIndustrial Tax Withholding` where name=%s for update',self.source_withholding)
            self.validate()
            source=frappe.get_doc('Industrial Tax Withholding',self.source_withholding)
            used=frappe.db.sql('''select coalesce(sum(amount),0) from `tabIndustrial Tax Credit`
                where source_withholding=%s and docstatus=1 and name!=%s''',(source.name,self.name))[0][0]
            if money(used)+money(self.amount)>money(source.tax_amount):
                frappe.throw('Approved credits exceed the registered suffered withholding.')
            if source.get('accounting_stage')=='Pending Evidence':
                self.post_pending_asset_transfer(source)
        if self.credit_kind == 'Paid Provisional':
            frappe.db.sql('select name from `tabIndustrial Tax Provisional` where name=%s for update', self.source_provisional)
            frappe.db.sql('select name from `tabTax Collection Reference` where name=%s for update',self.collection_reference)
            self.validate()
            origin = frappe.get_doc('Industrial Tax Provisional', self.source_provisional)
            created = frappe.db.sql('''select coalesce(sum(amount),0) from `tabIndustrial Tax Credit`
                where source_provisional=%s and docstatus=1 and name!=%s''', (origin.name,self.name))[0][0]
            from angola_tax.payment_control import get_confirmed_amount
            paid = get_confirmed_amount(fiscal_obligation=origin.fiscal_obligation)
            if money(created) + money(self.amount) > min(money(origin.tax_amount), money(paid)):
                frappe.throw('Provisional credits exceed eligible determined and confirmed paid tax.')
        self.approved_by, self.approved_on = frappe.session.user, now()

    def post_pending_asset_transfer(self,origin):
        if self.asset_recognition_journal:
            frappe.throw('Draft credit cannot provide a generated pending-asset journal.')
        if not self.accounting_date:
            frappe.throw('Set the accounting date for transfer from pending to eligible withholding.')
        from angola_tax.industrial_tax.configuration import role_account
        profile=frappe.get_doc('Industrial Tax Mechanism Profile',origin.profile)
        pending=role_account(profile,'Pending Suffered Withholding Asset',self.accounting_date)
        eligible=role_account(profile,'Suffered Withholding Asset',self.accounting_date)
        if eligible==pending:frappe.throw('Pending and eligible withholding accounts must differ.')
        dates=[getdate(origin.event_date)]+[getdate(r.posting_date) for r in origin.source_query()]
        if getdate(self.accounting_date)<max(dates):
            frappe.throw('Asset transfer cannot precede withholding recognition.')
        frappe.db.sql('select name from `tabAccount` where name=%s for update',pending)
        balance=frappe.db.sql("""select coalesce(sum(debit-credit),0) from `tabGL Entry`
            where company=%s and account=%s and is_cancelled=0 and posting_date<=%s for update""",(self.company,pending,self.accounting_date))[0][0]
        if money(balance)<money(self.amount):frappe.throw('Pending withholding GL balance is insufficient for this transfer.')
        journal=frappe.new_doc('Journal Entry');journal.company=self.company;journal.posting_date=self.accounting_date
        journal.voucher_type='Journal Entry';journal.user_remark=f'AGT withholding evidence / {self.name} / {self.agt_import}'
        journal.append('accounts',dict(account=eligible,cost_center=self.cost_center,debit_in_account_currency=self.amount))
        journal.append('accounts',dict(account=pending,cost_center=self.cost_center,credit_in_account_currency=self.amount))
        journal.insert();journal.submit()
        self.asset_recognition_journal=journal.name


    def before_cancel(self):
        if self.get('asset_recognition_journal'):
            frappe.throw('Credit has a fiscal asset transfer; use controlled revision.')
        if committed(self.name):
            frappe.throw('Credit is reserved or consumed by an approved annual computation; controlled revision is required.')

    def before_update_after_submit(self):
        frappe.throw('Approved credit provenance and amounts are immutable.')

    @frappe.whitelist()
    def refresh_balance(self):
        self.check_permission('read')
        amount = committed(self.name)
        self.db_set({'committed_amount':float(amount),'available_amount':float(available(self.amount,amount))})
        return {'committed_amount':float(amount),'available_amount':float(available(self.amount,amount))}


def committed(credit_name, excluding=None, lock=False):
    result = frappe.db.sql('''select coalesce(sum(a.amount),0)
         from `tabIndustrial Tax Credit Allocation` a
         join `tabIndustrial Tax Computation` c on c.name=a.parent
         where a.credit=%s and c.docstatus=1 and c.name!=%s''' + (' for update' if lock else ''), (credit_name, excluding or ''))
    return money(result[0][0])


def allocations(comp, lock=False, check_live=True):
    total, seen = Decimal(0), set()
    for row in sorted(comp.credit_allocations or [],key=lambda r:r.credit):
        if row.credit in seen:
            frappe.throw('Use one allocation row per source credit.')
        seen.add(row.credit)
        if lock:
            frappe.db.sql('select name from `tabIndustrial Tax Credit` where name=%s for update',row.credit)
        credit = frappe.get_doc('Industrial Tax Credit',row.credit)
        if (credit.docstatus != 1 or credit.company != comp.company
                or credit.annual_tax_type != comp.tax_type
                or str(credit.fiscal_year) != str(getdate(comp.fiscal_period_from).year)):
            frappe.throw('Credit must be approved and match company, annual tax and eligible fiscal exercise.')
        amount = money(row.amount)
        if amount <= 0 or amount > available(credit.amount,committed(credit.name,comp.name,lock=lock)):
            frappe.throw('Credit allocation exceeds its available eligible balance.')
        if check_live:
            credit.validate()
        if not credit.eligibility_date or getdate(credit.eligibility_date)>getdate(comp.accounting_date):
            frappe.throw('Credit cannot be offset before its confirmed withholding/payment and asset recognition date.')
        row.asset_account = credit.asset_account
        row.credit_kind = credit.credit_kind
        total += amount
    try:
        return bridge(comp.tax_amount,[r.amount for r in comp.credit_allocations or []])
    except ValueError as exc:
        frappe.throw(str(exc))


def post_offsets(comp, obligation):
    allocations(comp,lock=True)
    if not comp.credit_allocations:
        return
    grouped = defaultdict(Decimal)
    for row in comp.credit_allocations:
        grouped[row.asset_account] += money(row.amount)
    for account in sorted(grouped):
        frappe.db.sql('select name from `tabAccount` where name=%s for update',account)
        balance = frappe.db.sql('''select coalesce(sum(debit-credit),0) from `tabGL Entry`
            where company=%s and account=%s and is_cancelled=0 and posting_date<=%s for update''',
            (comp.company,account,comp.accounting_date))[0][0]
        if money(balance) < grouped[account]:
            frappe.throw('Credit asset GL balance is insufficient on the offset posting date; reconcile the asset first.')
    je = frappe.new_doc('Journal Entry')
    je.company, je.posting_date, je.voucher_type = comp.company,comp.accounting_date,'Journal Entry'
    je.user_remark = f'Annual II credit offsets / {comp.name} / fiscal exercise {getdate(comp.fiscal_period_from).year}'
    je.append('accounts',dict(account=obligation.liability_account,cost_center=comp.cost_center,
              debit_in_account_currency=float(sum(grouped.values()))))
    for account,amount in sorted(grouped.items()):
        je.append('accounts',dict(account=account,cost_center=comp.cost_center,credit_in_account_currency=float(amount)))
    je.insert()
    comp.db_set('offset_journal_entry',je.name)
    je.submit()
    for row in comp.credit_allocations:
        row.db_set('offset_journal_entry',je.name)
        credit = frappe.get_doc('Industrial Tax Credit',row.credit)
        balance=committed(credit.name)
        credit.db_set({'committed_amount':float(balance),'available_amount':float(available(credit.amount,balance))})


def protect_credit_confirmation(doc, method=None):
    credits=frappe.get_all('Industrial Tax Credit',filters={'collection_reference':doc.name,'docstatus':1},fields=['amount','source_provisional'],limit_page_length=0)
    for credit in credits:
        obligation=frappe.db.get_value('Industrial Tax Provisional',credit.source_provisional,'fiscal_obligation')
        if doc.status!='Confirmed' or money(doc.amount)!=money(credit.amount) or doc.fiscal_obligation!=obligation:
            frappe.throw('Collection confirmation, amount and obligation back an approved provisional credit. Controlled reversal is required.')
