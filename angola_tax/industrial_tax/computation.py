import hashlib
import json
from decimal import Decimal

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, now

from angola_tax.industrial_tax.configuration import role_account, get_company_currency
from angola_tax.industrial_tax.model import annual, money, true_up


SOURCE_FIELDS = ("gl_entry", "posting_date", "account", "voucher_type", "voucher_no", "debit", "credit")


def fingerprint(rows):
    values = [{key: (format(money(row.get(key)).normalize(), "f")
                     if key in ("debit", "credit") else str(row.get(key) or ""))
               for key in SOURCE_FIELDS} for row in rows]
    return hashlib.sha256(json.dumps(sorted(values, key=lambda r: r["gl_entry"]),
                                     sort_keys=True).encode()).hexdigest()


class IndustrialTaxComputation(Document):
    def context(self):
        profile = frappe.get_doc("Industrial Tax Profile", self.profile)
        rule = frappe.get_doc("Industrial Tax Rule", profile.rule_version)
        if profile.company != self.company:
            frappe.throw("Profile and computation company differ.")
        start, end = getdate(self.fiscal_period_from), getdate(self.fiscal_period_to)
        if start != start.replace(month=1, day=1) or end != start.replace(month=12, day=31):
            frappe.throw("First package supports full calendar-year annual computations only.")
        for source in (profile, rule):
            if start < getdate(source.effective_from) or end > getdate(source.effective_to):
                frappe.throw("Profile/rule does not cover the whole fiscal exercise.")
        get_company_currency(self.company)
        if not profile.general_ordinary_only or self.mechanism != "Annual":
            frappe.throw("Only ordinary general-regime Annual computation is enabled.")
        if any(money(self.get(key)) != 0 for key in (
                "losses_requested", "autonomous_tax_requested")):
            frappe.throw("Loss utilisation and autonomous tax require their controlled registers; approval is blocked.")
        return profile, rule

    def validate(self):
        profile, rule = self.context()
        self.tax_type = profile.tax_type
        self.rule_version = rule.name
        self.currency = get_company_currency(self.company)
        self.period_key = hashlib.sha256(
            f"{self.company}|{self.tax_type}|{getdate(self.fiscal_period_from)}|Annual".encode()
        ).hexdigest()
        pbt = sum((money(r.credit) - money(r.debit) for r in self.source_rows or []), Decimal(0))
        result = annual(pbt, [dict(kind=r.kind, amount=r.amount,
                                  legal_reference=r.legal_reference, evidence=r.evidence)
                             for r in self.adjustments or []], rule.ordinary_rate, rule.amount_precision)
        mapping = {"pbt": "result_before_tax", "addbacks": "addbacks", "deductions": "deductions",
                   "fiscal_result": "fiscal_result", "taxable_base": "taxable_base",
                   "tax_raw": "tax_raw", "tax": "tax_amount", "rounding": "rounding_difference"}
        for source, field in mapping.items():
            self.set(field, float(result[source]))
        self.prior_recognised = float(self.estimate_total(profile)) if self.estimates else 0
        self.true_up_amount = float(true_up(self.tax_amount, self.prior_recognised))
        from angola_tax.industrial_tax.credits import allocations
        credits, cash = allocations(self)
        self.credit_applied_amount = float(credits)
        self.credits_requested = float(credits)
        self.net_cash_requirement = float(cash)

    def source_query(self, profile):
        expense = role_account(profile, "Current Tax Expense", self.fiscal_period_to)
        deferred = role_account(profile, "Deferred Tax Expense", self.fiscal_period_to, required=False)
        excluded = [expense] + ([deferred] if deferred else [])
        return frappe.db.sql("""
            select g.name as gl_entry, g.posting_date, g.account,
                   g.voucher_type, g.voucher_no, g.debit, g.credit
              from `tabGL Entry` g join `tabAccount` a on a.name = g.account
             where g.company = %(company)s and g.is_cancelled = 0
               and g.posting_date between %(start)s and %(end)s
               and coalesce(g.is_opening, 'No') != 'Yes'
               and g.voucher_type != 'Period Closing Voucher'
               and a.root_type in ('Income', 'Expense')
               and g.account not in %(excluded)s
             order by g.name
        """, dict(company=self.company, start=self.fiscal_period_from,
                  end=self.fiscal_period_to, excluded=tuple(excluded)), as_dict=True)

    def estimate_total(self, profile):
        liability = role_account(profile, "Annual Liability", self.fiscal_period_to)
        expense = role_account(profile, "Current Tax Expense", self.fiscal_period_to)
        if liability == expense:
            frappe.throw("Expense and liability accounts must differ.")
        seen, total = set(), Decimal(0)
        for row in self.estimates or []:
            if row.journal_entry in seen:
                frappe.throw("Duplicate estimate Journal Entry.")
            seen.add(row.journal_entry)
            entry = frappe.get_doc("Journal Entry", row.journal_entry)
            if entry.docstatus != 1 or entry.company != self.company or entry.is_opening == "Yes":
                frappe.throw("Estimate must be a submitted, non-opening company Journal Entry.")
            if any(r.account not in (expense, liability) for r in entry.accounts):
                frappe.throw("Adopt only pure current-tax estimate journals with the configured expense and liability accounts.")
            gl = frappe.get_all("GL Entry", filters={"voucher_type": "Journal Entry",
                                "voucher_no": entry.name, "company": self.company, "is_cancelled": 0},
                                fields=["account", "debit", "credit"], limit_page_length=0)
            liability_total = sum((money(r.credit) - money(r.debit) for r in gl if r.account == liability), Decimal(0))
            expense_total = sum((money(r.debit) - money(r.credit) for r in gl if r.account == expense), Decimal(0))
            if not gl or liability_total != expense_total:
                frappe.throw("Estimate GL does not reconcile expense to liability.")
            if not row.exercise_evidence or row.fiscal_exercise != str(getdate(self.fiscal_period_from).year):
                frappe.throw("Each estimate requires fiscal-exercise identification and evidence, including cross-year postings.")
            row.recognised_amount = float(liability_total)
            total += liability_total
        return total

    @frappe.whitelist()
    def capture_source(self):
        self.check_permission("write")
        if self.docstatus != 0:
            frappe.throw("Capture GL while Draft only.")
        profile, _ = self.context()
        rows = self.source_query(profile)
        self.set("source_rows", [])
        for row in rows:
            self.append("source_rows", row)
        self.source_hash = fingerprint(rows)
        self.source_captured_on = now()
        self.review_complete = 0
        self.prior_tax_review_complete = 0
        self.save()
        return self.source_hash

    def before_submit(self):
        profile, rule = self.context()
        if profile.docstatus != 1 or rule.docstatus != 1:
            frappe.throw("Submit and approve the profile and legal rule first.")
        if (not self.source_hash or fingerprint(self.source_rows or []) != self.source_hash
                or fingerprint(self.source_query(profile)) != self.source_hash):
            frappe.throw("GL source changed or snapshot was edited. Capture and review again.")
        if not self.review_complete or not self.prior_tax_review_complete or not self.review_evidence:
            frappe.throw("Review source, fiscal adjustments and all prior tax accruals; attach approval evidence.")
        from angola_tax.industrial_tax.credits import allocations
        allocations(self, lock=True)
        self.validate_estimate_ownership(lock=True)
        if self.estimate_total(profile) != money(self.prior_recognised):
            frappe.throw("Prior accruals changed during approval. Review again.")
        # Reject existing GL-owned obligations rather than silently overwrite history.
        if frappe.db.exists("Fiscal Obligation", {"company": self.company, "tax_type": self.tax_type,
                "fiscal_period_from": self.fiscal_period_from, "fiscal_period_to": self.fiscal_period_to,
                "docstatus": ["!=", 2]}):
            frappe.throw("An obligation already exists for this exercise. Reconcile/migrate it before activating computation ownership.")
        self.approved_by = frappe.session.user
        self.approved_on = now()

    def validate_estimate_ownership(self, lock=False):
        for row in sorted(self.estimates or [], key=lambda r: r.journal_entry):
            if lock:
                frappe.db.sql("select name from `tabJournal Entry` where name=%s for update", row.journal_entry)
            if frappe.db.exists("Industrial Tax Computation", {
                    "recognition_journal_entry": row.journal_entry, "docstatus": 1}):
                frappe.throw("A journal generated by another approved computation cannot be adopted as a fresh estimate.")
            used = frappe.db.sql("""select c.name from `tabIndustrial Tax Estimate` e
                 join `tabIndustrial Tax Computation` c on c.name=e.parent
                 where e.journal_entry=%s and c.docstatus=1 and c.name!=%s""",
                 (row.journal_entry, self.name))
            if used:
                frappe.throw("A prior-tax journal is already adopted by another submitted computation.")

    def on_submit(self):
        profile, _ = self.context()
        account = role_account(profile, "Annual Liability", self.fiscal_period_to)
        tax = frappe.get_doc("Tax Type", self.tax_type)
        obligation = frappe.get_doc(dict(doctype="Fiscal Obligation", company=self.company,
            tax_type=self.tax_type, tax_name=tax.tax_name, obligation_type="Payable",
            obligation_origin="Operational", source_module="Accounting",
            source_document_type=self.doctype, source_document=self.name,
            determination_basis="Computation", currency=self.currency, liability_account=account,
            fiscal_period_from=self.fiscal_period_from, fiscal_period_to=self.fiscal_period_to,
            responsible_user=self.responsible_user, reviewer=self.approved_by))
        obligation.insert()
        self.db_set("fiscal_obligation", obligation.name)

    @frappe.whitelist()
    def complete_accounting(self):
        self.check_permission("submit")
        if self.docstatus != 1:
            frappe.throw("Approve the computation before accounting execution.")
        frappe.db.sql("select name from `tabIndustrial Tax Computation` where name=%s for update", self.name)
        self.reload()
        if self.accounting_complete:
            return self.fiscal_obligation
        profile, _ = self.context()
        self.validate_estimate_ownership(lock=True)
        current_prior = self.estimate_total(profile)
        if current_prior != money(self.prior_recognised):
            frappe.throw("Approved prior accrual evidence has changed; controlled correction is required.")
        if fingerprint(self.source_query(profile)) != self.source_hash:
            frappe.throw("GL changed after approval. A controlled revision is required before accounting execution.")
        day = getdate(self.accounting_date)
        if day < getdate(self.fiscal_period_to):
            frappe.throw("Annual accounting date cannot precede fiscal year-end.")
        liability = role_account(profile, "Annual Liability", day)
        expense = role_account(profile, "Current Tax Expense", day)
        if liability != role_account(profile, "Annual Liability", self.fiscal_period_to):
            frappe.throw("Cross-year account changes require controlled account-transfer reconciliation.")
        amount = money(self.true_up_amount)
        if amount:
            entry = frappe.new_doc("Journal Entry")
            entry.company, entry.posting_date, entry.voucher_type = self.company, day, "Journal Entry"
            entry.user_remark = f"Imposto Industrial {self.name}; fiscal exercise {getdate(self.fiscal_period_from).year}; estimate true-up"
            entry.append("accounts", dict(account=expense, cost_center=self.cost_center,
                  debit_in_account_currency=float(max(amount, 0)), credit_in_account_currency=float(max(-amount, 0))))
            entry.append("accounts", dict(account=liability,
                  credit_in_account_currency=float(max(amount, 0)), debit_in_account_currency=float(max(-amount, 0))))
            entry.insert()
            # Ownership exists before GL ingestion; durable for daily resynchronisation.
            self.db_set("recognition_journal_entry", entry.name)
            entry.submit()
        obligation = frappe.get_doc("Fiscal Obligation", self.fiscal_obligation)
        from angola_tax.industrial_tax.adapters import adopt_estimate_movements
        adopt_estimate_movements(self, obligation, profile)
        from angola_tax.industrial_tax.credits import post_offsets
        post_offsets(self, obligation)
        self.db_set("accounting_complete", 1)
        obligation.reload()
        if obligation.docstatus == 0:
            obligation.submit()
        return obligation.name

    def before_cancel(self):
        frappe.throw("Approved annual computations require a controlled revision. Cancellation is disabled in the first package.")

    def before_update_after_submit(self):
        frappe.throw("Approved computation is immutable. Use the accounting action or a controlled revision.")
