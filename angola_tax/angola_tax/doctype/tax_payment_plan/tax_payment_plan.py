import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate

from angola_tax.payment_control import refresh_payment_control
from angola_tax.gl_ingestion import submit_control_journal


class TaxPaymentPlan(Document):

    def validate(self):
        self.load_context()
        self.validate_plan()
        self.calculate_plan_totals()

    def before_submit(self):
        self.load_context()
        self.validate_plan()
        self.validate_approval_evidence()
        self.calculate_plan_totals()
        self.create_accounting_entry()

    def on_submit(self):
        self.db_set(
            "status",
            "Active",
            update_modified=False,
        )

        if not self.previous_plan:
            self.activate_settlement_rupes()

        if self.previous_plan:
            frappe.db.set_value(
                "Tax Payment Plan",
                self.previous_plan,
                {
                    "status": "Superseded",
                    "superseded_by": self.name,
                },
                update_modified=True,
            )

        refresh_payment_control(self.fiscal_obligation)
        self.refresh_accounting_control()

    def before_cancel(self):
        if (
            frappe.db.exists("DocType", "Tax Payment")
            and frappe.get_meta("Tax Payment").has_field(
                "payment_plan"
            )
        ):
            submitted_payments = frappe.db.count(
                "Tax Payment",
                {
                    "payment_plan": self.name,
                    "docstatus": 1,
                },
            )

            if submitted_payments:
                frappe.throw(
                    "Payment Plan cannot be cancelled while "
                    "submitted Tax Payments are linked to it."
                )

    def on_cancel(self):
        if self.opening_journal_entry:
            je = frappe.get_doc(
                "Journal Entry",
                self.opening_journal_entry,
            )

            if je.docstatus == 1:
                je.cancel()

        self.db_set(
            "status",
            "Cancelled",
            update_modified=False,
        )

        if self.previous_plan:
            previous = frappe.get_doc(
                "Tax Payment Plan",
                self.previous_plan,
            )

            if (
                previous.docstatus == 1
                and previous.superseded_by == self.name
            ):
                frappe.db.set_value(
                    "Tax Payment Plan",
                    previous.name,
                    {
                        "status": "Active",
                        "superseded_by": None,
                    },
                    update_modified=True,
                )

        refresh_payment_control(self.fiscal_obligation)

    def on_trash(self):
        frappe.throw(
            "Tax Payment Plans cannot be deleted. "
            "Cancel the document instead."
        )

    # ------------------------------------------------------
    # CONTEXT
    # ------------------------------------------------------

    def load_context(self):
        if not self.fiscal_obligation:
            return

        obligation = frappe.get_doc(
            "Fiscal Obligation",
            self.fiscal_obligation,
        )

        if obligation.docstatus != 1:
            frappe.throw(
                "Fiscal Obligation must be submitted."
            )

        if not self.tax_settlement:
            frappe.throw(
                "A submitted Tax Settlement is required "
                "before recording a Payment Plan."
            )

        settlement = frappe.get_doc(
            "Tax Settlement",
            self.tax_settlement,
        )

        if settlement.docstatus != 1:
            frappe.throw(
                "Tax Settlement must be submitted before "
                "recording a Payment Plan."
            )

        if (
            settlement.fiscal_obligation
            != obligation.name
        ):
            frappe.throw(
                "Tax Settlement and Payment Plan must "
                "refer to the same Fiscal Obligation."
            )

        self.company = obligation.company
        self.tax_type = settlement.tax_type
        self.currency = obligation.currency

        # Tax Assessment is provenance only.
        self.tax_assessment = (
            settlement.tax_assessment
            or None
        )

        self.tax_account = (
            settlement.tax_settlement_control_account
            or settlement.tax_account
            or obligation.liability_account
            or obligation.receivable_account
        )

        if not self.tax_account:
            frappe.throw(
                "No Tax Account is available for the "
                "Payment Plan."
            )

        self.load_penalty_settings()

        # The debt transferred into the Payment Plan is
        # the formally liquidated amount, not the earlier
        # determination or assessment.
        if not self.original_debt_amount:
            self.original_debt_amount = flt(
                settlement.liquidated_amount
            )

        if flt(self.original_debt_amount) <= 0:
            frappe.throw(
                "Tax Settlement must contain a Liquidated "
                "Amount greater than zero."
            )

        # Preserve the original collection references.
        if not self.original_rupe:
            self.original_rupe = settlement.rupe

        if not self.original_collection_document:
            self.original_collection_document = (
                settlement.liquidation_reference
            )

        if self.previous_plan:
            return

        if settlement.payment_arrangement != "Payment Plan":
            frappe.throw(
                "The Tax Settlement must select Payment Plan."
            )

        self.original_debt_amount = flt(settlement.liquidated_amount, 2)
        lines = settlement.collection_lines or []
        if not lines:
            frappe.throw("The Settlement has no RUPE schedule.")

        if not self.instalments:
            for number, source in enumerate(lines, 1):
                self.append("instalments", {
                    "instalment_no": number,
                    "nota_liquidacao": source.nota_liquidacao,
                    "rupe": source.rupe,
                    "due_date": source.payment_due_date,
                    "amount": source.rupe_amount,
                })

        if len(self.instalments) != len(lines):
            frappe.throw("Plan instalments must match Settlement RUPE lines.")
        for number, (row, source) in enumerate(zip(self.instalments, lines), 1):
            if (
                int(row.instalment_no or 0) != number
                or row.rupe != source.rupe
                or row.nota_liquidacao != source.nota_liquidacao
                or getdate(row.due_date) != getdate(source.payment_due_date)
                or abs(flt(row.amount) - flt(source.rupe_amount)) > 0.01
            ):
                frappe.throw(
                    f"Plan instalment {number} must match its Settlement RUPE."
                )

        self.plan_start_date = min(
            getdate(row.issue_date or row.payment_due_date) for row in lines
        )
        self.plan_end_date = max(getdate(row.payment_due_date) for row in lines)

    def load_penalty_settings(self):
        settings = frappe.get_all(
            "Tax Penalty Settings",
            filters={
                "company": self.company,
                "active": 1,
            },
            fields=[
                "name",
                "payment_plan_control_account",
                "tax_penalty_cost_account",
            ],
            limit=2,
        )

        if len(settings) != 1:
            frappe.throw(
                "Exactly one active Tax Penalty Settings is required "
                f"for company {self.company}."
            )

        settings = settings[0]

        self.tax_penalty_settings = settings.name
        self.payment_plan_control_account = (
            settings.payment_plan_control_account
        )
        self.tax_penalty_cost_account = (
            settings.tax_penalty_cost_account
        )

        if not self.payment_plan_control_account:
            frappe.throw(
                "Payment Plan Control Account is not configured "
                "in Tax Penalty Settings."
            )

        if not self.tax_penalty_cost_account:
            frappe.throw(
                "Tax Penalty Cost Account is not configured "
                "in Tax Penalty Settings."
            )

    # ------------------------------------------------------
    # VALIDATION
    # ------------------------------------------------------

    def validate_approval_evidence(self):
        if not self.plan_number or not self.plan_document:
            frappe.throw(
                "The approved AGT Payment Plan Number and document "
                "are required before submitting the Payment Plan."
            )

    def validate_plan(self):
        if not self.instalments:
            frappe.throw(
                "At least one instalment is required."
            )

        if (
            self.plan_start_date
            and self.plan_end_date
            and getdate(self.plan_start_date)
            > getdate(self.plan_end_date)
        ):
            frappe.throw(
                "Plan Start Date cannot be later than "
                "Plan End Date."
            )

        seen = set()

        for row in self.instalments:
            if row.instalment_no in seen:
                frappe.throw(
                    f"Duplicate instalment number "
                    f"{row.instalment_no}."
                )

            seen.add(row.instalment_no)

            if flt(row.amount) <= 0:
                frappe.throw(
                    f"Instalment {row.instalment_no} "
                    "must have an amount greater than zero."
                )

            row.amount = flt(row.amount, 2)

            row.outstanding_amount = max(
                flt(row.amount)
                - flt(row.amount_paid),
                0,
            )

    def calculate_plan_totals(self):
        self.number_of_instalments = len(
            self.instalments or []
        )

        self.total_plan_amount = flt(
            sum(
                flt(row.amount)
                for row in (self.instalments or [])
            ), 2
        )

        self.original_debt_amount = flt(self.original_debt_amount, 2)

        self.plan_variance = (
            flt(self.total_plan_amount)
            - flt(self.original_debt_amount)
        )

        self.scheduled_outstanding = sum(
            flt(row.outstanding_amount)
            for row in (self.instalments or [])
        )

        if (
            abs(flt(self.plan_variance)) >= 0.01
            and not self.variance_reason
        ):
            frappe.throw(
                "Variance Reason is mandatory when the "
                "Payment Plan differs from the debt being "
                "transferred."
            )

        if not self.previous_plan and abs(flt(self.plan_variance)) >= 0.01:
            frappe.throw(
                "Payment Plan total must equal the recognised Settlement "
                "amount. Document any additional penalty on the Settlement first."
            )

    def activate_settlement_rupes(self):
        """Use the original RUPE identity, now payable from plan control."""
        settlement = frappe.get_doc("Tax Settlement", self.tax_settlement)
        for row in self.instalments:
            existing = frappe.db.get_value(
                "Tax Collection Reference", {"rupe": row.rupe}, "name"
            )
            if existing:
                ref = frappe.get_doc("Tax Collection Reference", existing)
                if (
                    ref.tax_settlement != self.tax_settlement
                    or ref.fiscal_obligation != self.fiscal_obligation
                    or ref.tax_payment
                    or ref.status != "Issued"
                    or abs(flt(ref.amount) - flt(row.amount)) > 0.01
                ):
                    frappe.throw(
                        f"RUPE {row.rupe} cannot move to Payment Plan "
                        "because it differs or has payment activity."
                    )
                ref.source_type = "Payment Plan Instalment"
                ref.payment_plan = self.name
                ref.instalment_no = row.instalment_no
                ref.due_date = row.due_date
                ref.save(ignore_permissions=True)
            else:
                ref = frappe.new_doc("Tax Collection Reference")
                ref.tax_settlement = self.tax_settlement
                ref.fiscal_obligation = self.fiscal_obligation
                ref.source_type = "Payment Plan Instalment"
                ref.payment_plan = self.name
                ref.instalment_no = row.instalment_no
                ref.nota_liquidacao = row.nota_liquidacao
                ref.rupe = row.rupe
                ref.amount = row.amount
                ref.due_date = row.due_date
                ref.status = "Issued"
                ref.insert(ignore_permissions=True)

            for source in settlement.collection_lines:
                if source.rupe == row.rupe:
                    frappe.db.set_value(
                        "Tax Settlement Collection", source.name,
                        "collection_reference", ref.name,
                        update_modified=False,
                    )
                    break

    # ------------------------------------------------------
    # ACCOUNTING
    # ------------------------------------------------------

    def create_accounting_entry(self):
        if self.opening_journal_entry:
            return

        if self.previous_plan:
            self.create_reissue_journal_entry()
        else:
            self.create_initial_plan_journal_entry()

    def create_initial_plan_journal_entry(self):
        if not self.tax_account:
            frappe.throw(
                "Tax Account is required to transfer the "
                "assessed liability into the Payment Plan."
            )

        existing_debt = flt(
            self.original_debt_amount
        )

        plan_amount = flt(
            self.total_plan_amount
        )

        variance = plan_amount - existing_debt

        je = frappe.new_doc("Journal Entry")
        je.company = self.company
        je.posting_date = (
            self.plan_start_date
            or nowdate()
        )

        je.user_remark = (
            f"Tax Payment Plan {self.plan_number} "
            f"/ Fiscal Obligation "
            f"{self.fiscal_obligation}"
        )

        # Clear the existing assessed tax liability.
        tax_row = je.append("accounts", {})
        tax_row.account = self.tax_account
        tax_row.debit_in_account_currency = (
            existing_debt
        )
        tax_row.credit_in_account_currency = 0

        # Difference granted/charged by AGT with the plan.
        if variance > 0:
            penalty = je.append("accounts", {})
            penalty.account = (
                self.tax_penalty_cost_account
            )
            penalty.debit_in_account_currency = (
                variance
            )
            penalty.credit_in_account_currency = 0

        elif variance < 0:
            penalty = je.append("accounts", {})
            penalty.account = (
                self.tax_penalty_cost_account
            )
            penalty.debit_in_account_currency = 0
            penalty.credit_in_account_currency = abs(
                variance
            )

        # Establish the new plan liability.
        plan_row = je.append("accounts", {})
        plan_row.account = (
            self.payment_plan_control_account
        )
        plan_row.debit_in_account_currency = 0
        plan_row.credit_in_account_currency = (
            plan_amount
        )

        je.insert(ignore_permissions=True)
        submit_control_journal(
            je, self.fiscal_obligation, self.company, self.tax_type
        )

        self.opening_journal_entry = je.name

    def create_reissue_journal_entry(self):
        previous = frappe.get_doc(
            "Tax Payment Plan",
            self.previous_plan,
        )

        if previous.docstatus != 1:
            frappe.throw(
                "Previous Payment Plan must be submitted."
            )

        if previous.status not in (
            "Active",
            "In Arrears",
            "Awaiting Reissue",
        ):
            frappe.throw(
                "Only an active or arrears Payment Plan "
                "can be reissued."
            )

        if previous.company != self.company:
            frappe.throw(
                "Previous Payment Plan belongs to a "
                "different company."
            )

        old_outstanding = flt(
            previous.scheduled_outstanding
        )

        new_amount = flt(
            self.total_plan_amount
        )

        variance = (
            new_amount
            - old_outstanding
        )

        self.original_debt_amount = (
            old_outstanding
        )

        self.plan_variance = variance

        if (
            abs(variance) >= 0.01
            and not self.variance_reason
        ):
            frappe.throw(
                "Variance Reason is required for a "
                "reissued Payment Plan."
            )

        je = frappe.new_doc("Journal Entry")
        je.company = self.company
        je.posting_date = (
            self.plan_start_date
            or nowdate()
        )

        je.user_remark = (
            f"Reissue Tax Payment Plan "
            f"{previous.plan_number} "
            f"to {self.plan_number}"
        )

        # Zero the old plan balance.
        old_row = je.append("accounts", {})
        old_row.account = (
            previous.payment_plan_control_account
        )
        old_row.debit_in_account_currency = (
            old_outstanding
        )
        old_row.credit_in_account_currency = 0

        # Recognise increase/decrease.
        if variance > 0:
            variance_row = je.append(
                "accounts",
                {},
            )
            variance_row.account = (
                self.tax_penalty_cost_account
            )
            variance_row.debit_in_account_currency = (
                variance
            )
            variance_row.credit_in_account_currency = 0

        elif variance < 0:
            variance_row = je.append(
                "accounts",
                {},
            )
            variance_row.account = (
                self.tax_penalty_cost_account
            )
            variance_row.debit_in_account_currency = 0
            variance_row.credit_in_account_currency = abs(
                variance
            )

        # Establish replacement plan balance.
        new_row = je.append("accounts", {})
        new_row.account = (
            self.payment_plan_control_account
        )
        new_row.debit_in_account_currency = 0
        new_row.credit_in_account_currency = (
            new_amount
        )

        je.insert(ignore_permissions=True)
        submit_control_journal(
            je, self.fiscal_obligation, self.company, self.tax_type
        )

        self.opening_journal_entry = je.name

    # ------------------------------------------------------
    # CONTROL
    # ------------------------------------------------------

    def refresh_accounting_control(self):
        balance = frappe.db.sql(
            """
            SELECT COALESCE(
                SUM(credit - debit),
                0
            )
            FROM `tabGL Entry`
            WHERE company = %s
              AND account = %s
              AND is_cancelled = 0
            """,
            (
                self.company,
                self.payment_plan_control_account,
            ),
        )[0][0]

        expected = frappe.db.sql(
            """
            SELECT COALESCE(
                SUM(scheduled_outstanding),
                0
            )
            FROM `tabTax Payment Plan`
            WHERE company = %s
              AND payment_plan_control_account = %s
              AND docstatus = 1
              AND status IN (
                  'Active',
                  'In Arrears',
                  'Awaiting Reissue'
              )
            """,
            (
                self.company,
                self.payment_plan_control_account,
            ),
        )[0][0]

        balance = flt(balance)
        expected = flt(expected)

        variance = (
            balance
            - expected
        )

        frappe.db.set_value(
            "Tax Payment Plan",
            self.name,
            {
                "control_account_balance":
                    balance,
                "expected_control_balance":
                    expected,
                "gl_variance":
                    variance,
                "accounting_reconciled":
                    1
                    if abs(variance) < 0.01
                    else 0,
            },
            update_modified=False,
        )

    # ------------------------------------------------------
    # REISSUE
    # ------------------------------------------------------

    @frappe.whitelist()
    def make_reissue(self):
        if self.docstatus != 1:
            frappe.throw(
                "Only a submitted Payment Plan "
                "can be reissued."
            )

        if self.status not in (
            "Active",
            "In Arrears",
            "Awaiting Reissue",
        ):
            frappe.throw(
                "This Payment Plan cannot be reissued "
                "from its current status."
            )

        new_doc = frappe.new_doc(
            "Tax Payment Plan"
        )

        new_doc.fiscal_obligation = (
            self.fiscal_obligation
        )

        new_doc.tax_settlement = (
            self.tax_settlement
        )

        new_doc.previous_plan = self.name
        new_doc.plan_origin = "Reissue"

        new_doc.original_debt_amount = (
            self.scheduled_outstanding
            or self.total_plan_amount
        )

        new_doc.insert(
            ignore_permissions=True
        )

        return new_doc.name
