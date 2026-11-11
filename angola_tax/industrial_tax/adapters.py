import frappe
from frappe.utils import getdate, nowdate, flt

from angola_tax.angola_tax.doctype.fiscal_obligation.fiscal_obligation import FiscalObligation
from angola_tax.angola_tax.doctype.tax_deadline_configuration.tax_deadline_configuration import (
    TaxDeadlineConfiguration, TIMING_OFFSETS,
)
from angola_tax.industrial_tax.model import annual_due, exposure, money


TIMING_OFFSETS.update({"Fourth Following Month": 4, "Fifth Following Month": 5,
                       "Sixth Following Month": 6})


def is_ii(doc):
    return doc.get("determination_basis") == "Computation"


def source(doc):
    if doc.source_document_type not in ("Industrial Tax Computation", "Industrial Tax Provisional", "Industrial Tax Withholding Period") or not doc.source_document:
        frappe.throw("Computation-owned obligation requires its approved II source.")
    comp = frappe.get_doc(doc.source_document_type, doc.source_document)
    if (comp.docstatus != 1 or comp.company != doc.company or comp.tax_type != doc.tax_type
            or getdate(comp.fiscal_period_from) != getdate(doc.fiscal_period_from)
            or getdate(comp.fiscal_period_to) != getdate(doc.fiscal_period_to)):
        frappe.throw("II source must be approved and match company, tax and fiscal period.")
    if comp.fiscal_obligation and comp.fiscal_obligation != doc.name:
        frappe.throw("Computation already owns another obligation.")
    return comp


def resolve_amount(doc):
    settlements = frappe.get_all("Tax Settlement", filters={"fiscal_obligation": doc.name,
                        "docstatus": 1, "status": ["!=", "Cancelled"]},
                        fields=["liquidated_amount"], limit_page_length=0)
    assessed = None
    if doc.latest_tax_assessment:
        assessment = frappe.get_doc("Tax Assessment", doc.latest_tax_assessment)
        if assessment.docstatus == 1 and assessment.fiscal_obligation == doc.name:
            assessed = max(money(assessment.effective_assessed_amount) - money(doc.credit_applied_amount), money(0))
    return exposure(doc.net_cash_requirement, assessed,
                    [r.liquidated_amount for r in settlements], doc.amount_paid)


def deadline_date(doc):
    comp = source(doc)
    if comp.doctype in ("Industrial Tax Provisional", "Industrial Tax Withholding Period"):
        from angola_tax.industrial_tax.mechanisms import due_date
        return due_date(comp)
    profile = frappe.get_doc("Industrial Tax Profile", comp.profile)
    rule = frappe.get_doc("Tax Deadline Configuration", profile.deadline_configuration)
    end = getdate(doc.fiscal_period_to)
    if (rule.docstatus != 1 or not rule.active or rule.company != doc.company
            or rule.tax_type != doc.tax_type or end < getdate(rule.effective_from)
            or (rule.effective_to and end > getdate(rule.effective_to))
            or rule.legal_due_timing != "Fifth Following Month"
            or rule.legal_due_day_rule != "Last Working Day"):
        frappe.throw("Annual II deadline must be effective, submitted and fifth following month / last working day.")
    calendar = frappe.get_doc("Holiday List", rule.holiday_list)
    may_start = end.replace(year=end.year + 1, month=5, day=1)
    may_end = may_start.replace(day=31)
    if getdate(calendar.from_date) > may_start or getdate(calendar.to_date) < may_end:
        frappe.throw("Holiday List must cover the full payment/filing month in the following year.")
    return annual_due(end, 5, [getdate(r.holiday_date) for r in calendar.holidays])


class IndustrialFiscalObligation(FiscalObligation):
    def calculate_from_movements(self):
        if not is_ii(self):
            return super().calculate_from_movements()
        comp = source(self)
        self.amount_determined_raw = comp.tax_raw
        self.amount_determined = comp.tax_amount
        self.rounding_difference = comp.rounding_difference
        self.credit_applied_amount = comp.credit_applied_amount or 0
        self.net_cash_requirement = comp.net_cash_requirement

    def calculate_outstanding_amount(self):
        if not is_ii(self):
            return super().calculate_outstanding_amount()
        effective, outstanding = resolve_amount(self)
        self.effective_obligation_amount, self.outstanding_amount = float(effective), float(outstanding)
        self.unapplied_amount = max(flt(self.amount_paid) - float(effective), 0)

    def set_statutory_due_dates(self):
        if not is_ii(self):
            return super().set_statutory_due_dates()
        due = deadline_date(self)
        if not self.original_payment_due_date:
            self.original_payment_due_date = due
        if not self.current_payment_due_date:
            self.current_payment_due_date = due

    def set_filing_due_date(self):
        if not is_ii(self):
            return super().set_filing_due_date()
        self.filing_due_date = deadline_date(self)

    def before_submit(self):
        if not is_ii(self):
            return super().before_submit()
        comp = source(self)
        if not comp.accounting_complete:
            frappe.throw("Complete approved tax accounting before submitting the obligation.")
        if any(r.classification_status == "Unclassified" for r in self.movements or []):
            frappe.throw("Review all accounting movements first.")
        self.reviewed_by = comp.approved_by
        self.reviewed_on = comp.approved_on
        self.status = ("Awaiting Liquidation" if comp.net_cash_requirement > 0 else
                       "Settled by Credits" if comp.tax_amount > 0 else "Determined")

    def calculate_accounting_reconciliation(self):
        if not is_ii(self):
            return super().calculate_accounting_reconciliation()
        comp = source(self)
        balance = sum((money(r.credit) - money(r.debit) for r in self.movements or []), money(0))
        reconciled = (comp.accounting_complete and money(self.outstanding_amount) == 0
                      and abs(balance) < money("0.01")
                      and not any(r.classification_status == "Unclassified" for r in self.movements or []))
        self.accounting_reconciled = int(bool(reconciled))
        self.reconciliation_date = (self.reconciliation_date or nowdate()) if reconciled else None

    def validate_closure(self):
        if is_ii(self) and self.status in ("Closed", "Reconciled") and not self.filing_date:
            frappe.throw("Record the required declaration/remittance evidence before closing an industrial-tax obligation.")
        return super().validate_closure()

    def calculate_deadline_status(self):
        if is_ii(self) and money(self.effective_obligation_amount) == 0:
            self.is_overdue, self.days_overdue = 0, 0
            self.effective_payment_due_date = self.current_payment_due_date or self.original_payment_due_date
            self.deadline_status = "No Payment Required"
            return
        return super().calculate_deadline_status()

    def before_cancel(self):
        if is_ii(self):
            frappe.throw("Computation-owned II obligations require controlled revision; cancellation is disabled in the first package.")
        return super().before_cancel()


class IndustrialDeadlineConfiguration(TaxDeadlineConfiguration):
    def apply_legal_rule_engine_values(self):
        super().apply_legal_rule_engine_values()
        if self.legal_due_day_rule == "Last Working Day":
            self.legal_use_end_of_month, self.legal_due_day = 1, 0

    def validate_legal_deadline(self):
        if self.legal_due_day_rule == "Last Working Day":
            if not self.holiday_list or not self.legal_reference:
                frappe.throw("Last working day requires a Holiday List and legal reference.")
        super().validate_legal_deadline()


def ingest_gl(config, gle):
    """Return True for II-owned rows; Legacy configurations take the old path."""
    policy = frappe.db.get_value("Tax Account Configuration", config.name, "ingestion_policy") or "Legacy"
    if policy == "Legacy":
        return False
    if policy == "Disabled":
        return True
    owner = None
    if gle.voucher_type == "Journal Entry":
        for doctype, field in (("Industrial Tax Computation", "recognition_journal_entry"),
                              ("Industrial Tax Computation", "offset_journal_entry"),
                              ("Industrial Tax Provisional", "recognition_journal_entry")):
            name = frappe.db.get_value(doctype,{field:gle.voucher_no,"docstatus":1},"name")
            if name:
                owner = frappe.get_doc(doctype,name)
                break
    if owner:
        if config.company != owner.company or config.tax_type != owner.tax_type:
            frappe.throw("II journal/configuration context mismatch.")
        obligation = frappe.get_doc("Fiscal Obligation", owner.fiscal_obligation)
        from angola_tax.gl_ingestion import add_gl_movement
        add_gl_movement(obligation, gle, config, "Reclassification", "Automatic")
        obligation.save(ignore_permissions=True)
        return True
    # Explicit payment/settlement context continues through the existing path.
    payment = frappe.flags.get("angola_tax_payment_submission")
    from angola_tax.gl_ingestion import get_control_journal_context
    if payment and payment.get("journal_entry") == gle.voucher_no:
        return False
    if get_control_journal_context(gle):
        return False
    # No inferred payable, no posting-year annual obligation, no advance-asset debt.
    return True


def adopt_estimate_movements(comp, obligation, profile):
    from angola_tax.gl_ingestion import add_gl_movement
    config_name = next(r.configuration for r in profile.account_roles if r.role == "Annual Liability")
    config = frappe.get_doc("Tax Account Configuration", config_name)
    for row in comp.estimates or []:
        entries = frappe.get_all("GL Entry", filters={"voucher_type": "Journal Entry",
            "voucher_no": row.journal_entry, "account": config.account, "is_cancelled": 0,
            "company": comp.company}, fields=["name", "posting_date", "voucher_type", "voucher_no",
            "company", "account", "debit", "credit", "remarks"], limit_page_length=0)
        for gle in entries:
            existing = frappe.db.get_value("Fiscal Obligation Movement", {"gl_entry": gle.name}, "parent")
            if existing and existing != obligation.name:
                frappe.throw("Prior estimate is already linked to another obligation. Reconcile it first.")
            if not existing:
                add_gl_movement(obligation, gle, config, "Reclassification", "Automatic")
    obligation.save()


def refresh_ii_payment(doc):
    if not is_ii(doc):
        return False
    if doc.docstatus != 1 or doc.status in ("Cancelled", "Superseded", "Closed"):
        return True
    from angola_tax.payment_control import get_confirmed_amount, get_full_payment_date
    doc.amount_paid = get_confirmed_amount(fiscal_obligation=doc.name)
    effective, outstanding = resolve_amount(doc)
    liquidated = frappe.db.exists("Tax Settlement", {"fiscal_obligation": doc.name, "docstatus": 1,
                                  "status": ["!=", "Cancelled"]})
    plan = frappe.db.get_value("Tax Payment Plan", {"fiscal_obligation": doc.name, "docstatus": 1,
                       "status": ["in", ["Active", "In Arrears", "Awaiting Reissue"]]}, "name")
    if effective == 0:
        status = ("Settled by Credits" if money(doc.credit_applied_amount) > 0
                  else "Filed" if doc.filing_date else "Determined")
    elif liquidated and outstanding <= money("0.01"):
        status = "Paid"
    elif plan:
        status = "Under Payment Plan"
    else:
        status = "Ready for Payment" if liquidated else "Awaiting Liquidation"
    paid_on = get_full_payment_date(doc.name, float(effective)) if status == "Paid" else None
    late = max((getdate(paid_on) - getdate(doc.original_payment_due_date)).days, 0) if paid_on and doc.original_payment_due_date else 0
    doc.effective_obligation_amount, doc.outstanding_amount = float(effective), float(outstanding)
    doc.calculate_accounting_reconciliation()
    frappe.db.set_value("Fiscal Obligation", doc.name, dict(amount_paid=doc.amount_paid,
        effective_obligation_amount=float(effective), outstanding_amount=float(outstanding),
        unapplied_amount=max(flt(doc.amount_paid) - float(effective), 0), status=status,
        latest_payment_plan=plan, fully_paid_on=paid_on, paid_late=int(late > 0), days_paid_late=late,
        accounting_reconciled=doc.accounting_reconciled, reconciliation_date=doc.reconciliation_date))
    return True
