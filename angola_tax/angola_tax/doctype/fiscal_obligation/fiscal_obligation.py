import frappe
from frappe.model.document import Document
from frappe.utils import (
    add_months,
    date_diff,
    get_last_day,
    getdate,
    nowdate,
)


class FiscalObligation(Document):

    def validate(self):
        self.validate_fiscal_period()
        self.set_fiscal_year()
        self.set_statutory_due_dates()
        self.set_submission_due_date()
        self.set_filing_due_date()
        self.process_movement_reviews()
        self.calculate_from_movements()
        self.validate_amounts()
        self.validate_filing()
        self.validate_assessment()
        self.calculate_variances()
        self.validate_variance_reason()
        self.calculate_outstanding_amount()
        self.calculate_accounting_reconciliation()
        self.calculate_submission_status()
        self.calculate_filing_status()
        self.calculate_deadline_status()
        self.calculate_compliance_status()
        self.validate_closure()

    def validate_fiscal_period(self):
        if (
            self.fiscal_period_from
            and self.fiscal_period_to
            and getdate(self.fiscal_period_from) > getdate(self.fiscal_period_to)
        ):
            frappe.throw(
                "Fiscal Period From cannot be later than Fiscal Period To."
            )

    def set_fiscal_year(self):
        if self.fiscal_period_to:
            self.fiscal_year = str(getdate(self.fiscal_period_to).year)
        elif self.fiscal_period_from:
            self.fiscal_year = str(getdate(self.fiscal_period_from).year)

    def set_statutory_due_dates(self):

        rule = self.get_deadline_rule("Payment")

        if not rule:
            return

        period_end = getdate(self.fiscal_period_to)

        due_month = add_months(
            period_end,
            rule.due_month_offset or 0,
        )

        if rule.use_end_of_month:
            due_date = get_last_day(due_month)

        elif rule.due_day:
            last_day = get_last_day(due_month).day
            due_day = min(rule.due_day, last_day)
            due_date = due_month.replace(day=due_day)

        else:
            return

        if not self.original_payment_due_date:
            self.original_payment_due_date = due_date

        if not self.current_payment_due_date:
            self.current_payment_due_date = due_date

    def calculate_from_movements(self):
        if not self.movements:
            return

        determined = 0
        paid = 0

        for row in self.movements:
            debit = row.debit or 0
            credit = row.credit or 0

            if row.movement_type == "Determination":
                determined += credit - debit
            elif row.movement_type == "Assessment":
                determined += (row.credit or 0) - (row.debit or 0)
            elif row.movement_type == "Adjustment Increase":
                determined += abs(credit - debit)

            elif row.movement_type == "Adjustment Decrease":
                determined -= abs(credit - debit)

            elif row.movement_type == "Reversal":
                determined -= abs(credit - debit)

            elif row.movement_type == "Payment":
                paid += debit - credit

        self.amount_determined = max(determined, 0)
        self.amount_paid = max(paid, 0)

    def validate_amounts(self):
        for fieldname in (
            "amount_determined",
            "amount_filed",
            "amount_assessed",
        ):
            value = self.get(fieldname) or 0

            if value < 0:
                frappe.throw(
                    f"{self.meta.get_label(fieldname)} cannot be negative."
                )

        if self.amount_filed and not self.amount_determined:
            frappe.throw(
                "Amount Determined is required before Amount Filed."
            )

        if self.amount_assessed and not self.amount_filed:
            frappe.throw(
                "Amount Filed is required before Amount Assessed."
            )

    def calculate_variances(self):
        self.filing_variance = 0
        self.assessment_variance = 0
        self.has_variance = 0

        filing_done = bool(
            self.filing_date
            or self.status in (
                "Filed",
                "Awaiting Assessment",
                "Ready for Payment",
                "Paid",
                "Reconciled",
                "Closed",
            )
        )

        if filing_done:
            determined = self.amount_determined or 0
            filed = self.amount_filed or 0
            self.filing_variance = filed - determined

        assessment_done = bool(
            self.nota_lancamento_reference
            or self.nota_lancamento_date
            or self.status in (
                "Ready for Payment",
                "Paid",
                "Reconciled",
                "Closed",
            )
        )

        if assessment_done:
            filed = self.amount_filed or 0
            assessed = self.amount_assessed or 0
            self.assessment_variance = assessed - filed

        self.has_variance = 1 if (
            self.filing_variance != 0
            or self.assessment_variance != 0
        ) else 0

    def validate_variance_reason(self):
        if self.has_variance and not self.variance_reason:
            frappe.throw(
                "Variance Reason is required when an actual filing or assessment variance exists."
            )

    def calculate_outstanding_amount(self):
        if not self.movements:
            self.amount_paid = sum(
                (row.amount or 0) for row in (self.payments or [])
            )

        assessment_exists = bool(
            self.nota_lancamento_reference
            or self.nota_lancamento_date
            or self.status in (
                "Ready for Payment",
                "Paid",
                "Reconciled",
                "Closed",
            )
        )

        filing_exists = bool(
            self.filing_date
            or self.status in (
                "Filed",
                "Awaiting Assessment",
                "Ready for Payment",
                "Paid",
                "Reconciled",
                "Closed",
            )
        )

        if assessment_exists:
            base_amount = self.amount_assessed or 0
        elif filing_exists:
            base_amount = self.amount_filed or 0
        else:
            base_amount = self.amount_determined or 0

        self.effective_obligation_amount = base_amount

        self.outstanding_amount = max(
            base_amount - (self.amount_paid or 0),
            0,
        )

    def calculate_filing_status(self):
        self.is_filing_overdue = 0
        self.days_filing_overdue = 0

        if self.filing_date:
            self.filing_status = "Filed"
            return

        if not self.filing_due_date:
            self.filing_status = "No Deadline"
            return

        today = getdate(nowdate())
        due_date = getdate(self.filing_due_date)

        days_remaining = date_diff(due_date, today)

        if days_remaining < 0:
            self.is_filing_overdue = 1
            self.days_filing_overdue = abs(days_remaining)
            self.filing_status = "Overdue"

        elif days_remaining == 0:
            self.filing_status = "Due Today"

        elif days_remaining <= 5:
            self.filing_status = "Due Soon"

        else:
            self.filing_status = "Not Due"

    def calculate_deadline_status(self):
        self.is_overdue = 0
        self.days_overdue = 0

        due_date = (
            self.rupe_payment_limit_date
            or self.current_payment_due_date
            or self.original_payment_due_date
        )

        self.effective_payment_due_date = due_date

        if (self.outstanding_amount or 0) <= 0:
            self.deadline_status = "Paid"
            return

        if not due_date:
            self.deadline_status = "No Deadline"
            return

        today = getdate(nowdate())
        due_date = getdate(due_date)

        days_remaining = date_diff(due_date, today)

        if days_remaining < 0:
            self.is_overdue = 1
            self.days_overdue = abs(days_remaining)
            self.deadline_status = "Overdue"

        elif days_remaining == 0:
            self.deadline_status = "Due Today"

        elif days_remaining <= 5:
            self.deadline_status = "Due Soon"

        else:
            self.deadline_status = "On Time"

    def calculate_compliance_status(self):
        self.compliance_alert = 0
        self.compliance_message = ""

        if self.is_submission_overdue:
            self.compliance_status = "Overdue"
            self.compliance_alert = 1
            self.compliance_message = (
                f"Fiscal obligation submission overdue by "
                f"{self.days_submission_overdue} day(s)."
            )
            return

        if self.status in ("Closed", "Cancelled"):
            self.compliance_status = "Closed"
            return

        if self.is_filing_overdue or self.is_overdue:
            self.compliance_status = "Overdue"
            self.compliance_alert = 1

            issues = []

            if self.is_filing_overdue:
                issues.append(
                    f"Filing overdue by {self.days_filing_overdue} day(s)"
                )

            if self.is_overdue:
                issues.append(
                    f"Payment overdue by {self.days_overdue} day(s)"
                )

            self.compliance_message = "; ".join(issues)
            return

        if (
            self.filing_status == "Due Today"
            or self.deadline_status == "Due Today"
        ):
            self.compliance_status = "Action Required"
            self.compliance_alert = 1
            self.compliance_message = (
                "Tax obligation requires action today."
            )
            return

        if (
            self.filing_status == "Due Soon"
            or self.deadline_status == "Due Soon"
        ):
            self.compliance_status = "Due Soon"
            self.compliance_alert = 1
            self.compliance_message = (
                "Tax obligation is approaching its deadline."
            )
            return

        if self.correction_required:
            self.compliance_status = "Action Required"
            self.compliance_alert = 1
            self.compliance_message = "Correction is required."
            return

        if self.status in ("Under Review", "Approved"):
            self.compliance_status = "Under Review"
            return

        self.compliance_status = "Compliant"

    def set_submission_due_date(self):
        rule = self.get_deadline_rule("Submission")

        if not rule:
            return

        period_end = getdate(self.fiscal_period_to)

        due_month = add_months(
            period_end,
            rule.due_month_offset or 0,
        )

        if rule.use_end_of_month:
            self.submission_due_date = get_last_day(due_month)

        elif rule.due_day:
            last_day = get_last_day(due_month).day
            due_day = min(rule.due_day, last_day)
            self.submission_due_date = due_month.replace(day=due_day)


    def calculate_submission_status(self):
        self.is_submission_overdue = 0
        self.days_submission_overdue = 0

        if self.docstatus == 1:
            self.submission_status = "Submitted"
            return

        if not self.submission_due_date:
            self.submission_status = "No Deadline"
            return

        today = getdate(nowdate())
        due_date = getdate(self.submission_due_date)

        days_remaining = date_diff(due_date, today)

        if days_remaining < 0:
            self.is_submission_overdue = 1
            self.days_submission_overdue = abs(days_remaining)
            self.submission_status = "Overdue"

        elif days_remaining == 0:
            self.submission_status = "Due Today"

        elif days_remaining <= 3:
            self.submission_status = "Due Soon"

        else:
            self.submission_status = "Not Due"


    def get_deadline_rule(self, deadline_type):
        if not self.fiscal_period_to or not self.tax_code or not self.estado:
            return None

        period_end = getdate(self.fiscal_period_to)

        configurations = frappe.get_all(
            "Tax Deadline Configuration",
            filters={
                "tax_code": self.tax_code,
                "estado": self.estado,
                "active": 1,
                "effective_from": ["<=", period_end],
            },
            fields=[
                "name",
                "effective_from",
                "effective_to",
            ],
            order_by="effective_from desc",
        )

        for config in configurations:
            if config.effective_to and getdate(config.effective_to) < period_end:
                continue

            doc = frappe.get_doc(
                "Tax Deadline Configuration",
                config.name,
            )

            for rule in doc.deadline_rules:
                if rule.deadline_type == deadline_type:
                    return rule

        return None

    def before_cancel(self):
        matched_payments = [
            row for row in (self.movements or [])
            if row.movement_type == "Payment"
        ]

        if matched_payments:
            frappe.throw(
                "Cannot cancel this Fiscal Obligation while payment movements are matched. "
                "Unmatch or reverse the payment allocation first."
            )

        if self.accounting_reconciled:
            frappe.throw(
                "Cannot cancel a reconciled Fiscal Obligation. "
                "Reverse the reconciliation first."
            )

    def before_update_after_submit(self):
        self.process_movement_reviews()
        self.calculate_from_movements()
        self.validate_filing()
        self.validate_assessment()
        self.calculate_variances()
        self.validate_variance_reason()
        self.calculate_outstanding_amount()
        self.calculate_accounting_reconciliation()
        self.calculate_submission_status()
        self.calculate_filing_status()
        self.calculate_deadline_status()
        self.calculate_compliance_status()
        self.validate_closure()

    def set_filing_due_date(self):
        rule = self.get_deadline_rule("Filing")

        if not rule:
            return

        period_end = getdate(self.fiscal_period_to)

        due_month = add_months(
            period_end,
            rule.due_month_offset or 0,
        )

        if rule.use_end_of_month:
            self.filing_due_date = get_last_day(due_month)

        elif rule.due_day:
            last_day = get_last_day(due_month).day
            due_day = min(rule.due_day, last_day)
            self.filing_due_date = due_month.replace(day=due_day)


    def before_submit(self):
        if not self.movements:
            frappe.throw(
                "Fiscal Obligation cannot be submitted without accounting movements."
            )

        if (self.amount_determined or 0) <= 0:
            frappe.throw(
                "Amount Determined must be greater than zero before submission."
            )

        unclassified = [
            row for row in self.movements
            if row.classification_status == "Unclassified"
            or row.movement_type == "Unclassified"
        ]

        if unclassified:
            frappe.throw(
                f"{len(unclassified)} accounting movement(s) remain unclassified. "
                "Review them before submitting the Fiscal Obligation."
            )

        self.reviewed_by = frappe.session.user
        self.reviewed_on = frappe.utils.now()


    def validate_filing(self):
        filing_started = bool(
            self.amount_filed
            or self.filing_date
            or self.declaration_reference
        )

        if not filing_started:
            return

        if self.docstatus != 1:
            frappe.throw(
                "Fiscal Obligation must be submitted before filing information can be recorded."
            )

        if not self.filing_date:
            frappe.throw(
                "Filing Date is required when recording a tax filing."
            )

        if not self.declaration_reference:
            frappe.throw(
                "Declaration Reference is required when recording a tax filing."
            )


    def validate_assessment(self):
        assessment_started = bool(
            self.nota_lancamento_reference
            or self.nota_lancamento_date
            or self.amount_assessed
            or self.rupe
            or self.rupe_payment_limit_date
        )

        if not assessment_started:
            return

        if self.docstatus != 1:
            frappe.throw(
                "Fiscal Obligation must be submitted before assessment information can be recorded."
            )

        if not self.filing_date:
            frappe.throw(
                "The tax filing must be recorded before the assessment / Nota de Lançamento."
            )

        if not self.nota_lancamento_reference:
            frappe.throw(
                "Nota de Lançamento Reference is required."
            )

        if not self.nota_lancamento_date:
            frappe.throw(
                "Nota de Lançamento Date is required."
            )

        if not self.rupe:
            frappe.throw(
                "RUPE is required for the assessment."
            )

        if not self.rupe_payment_limit_date:
            frappe.throw(
                "RUPE Payment Limit Date is required."
            )

    def validate_closure(self):
        if self.status not in ("Reconciled", "Closed"):
            return

        unclassified = [
            row for row in (self.movements or [])
            if row.classification_status == "Unclassified"
            or row.movement_type == "Unclassified"
        ]

        if unclassified:
            frappe.throw(
                "Cannot reconcile or close while accounting movements remain unclassified."
            )

        if (self.outstanding_amount or 0) != 0:
            frappe.throw(
                "Cannot reconcile or close while an outstanding amount remains."
            )

        if not self.filing_date:
            frappe.throw(
                "Cannot reconcile or close before the tax filing is recorded."
            )

        if not self.nota_lancamento_reference:
            frappe.throw(
                "Cannot reconcile or close before the Nota de Lançamento is recorded."
            )

        if self.has_variance and self.correction_required:
            frappe.throw(
                "Cannot reconcile or close while a tax variance still requires correction."
            )

        if not self.accounting_reconciled:
            frappe.throw(
                "Accounting reconciliation must be completed before closing the obligation."
            )


    def calculate_accounting_reconciliation(self):
        unclassified = [
            row for row in (self.movements or [])
            if row.classification_status == "Unclassified"
            or row.movement_type == "Unclassified"
        ]

        if unclassified:
            self.accounting_reconciled = 0
            self.reconciliation_date = None
            return

        if (self.outstanding_amount or 0) != 0:
            self.accounting_reconciled = 0
            self.reconciliation_date = None
            return

        if not self.movements:
            self.accounting_reconciled = 0
            self.reconciliation_date = None
            return

        self.accounting_reconciled = 1

        if not self.reconciliation_date:
            self.reconciliation_date = nowdate()

    def process_movement_reviews(self):
        for row in self.movements or []:
            # Automatic movements remain untouched.
            if row.classification_status == "Automatic":
                continue

            # Still unresolved.
            if not row.movement_type or row.movement_type == "Unclassified":
                row.classification_status = "Unclassified"
                row.reviewed_by = None
                row.reviewed_on = None
                continue

            # A human has assigned a fiscal meaning.
            row.classification_status = "Reviewed"

            if not row.reviewed_by:
                row.reviewed_by = frappe.session.user

            if not row.reviewed_on:
                row.reviewed_on = frappe.utils.now()
