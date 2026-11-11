import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate
from angola_tax.statutory_deadline import get_statutory_payment_due_date
from angola_tax.gl_ingestion import submit_control_journal


TOLERANCE = 0.01


class TaxSettlement(Document):

    # ======================================================
    # VALIDATION — DRAFT / SAVE
    # ======================================================

    def validate(self):
        self.payment_arrangement = (
            self.payment_arrangement or "Direct Collection"
        )
        if self.payment_arrangement not in (
            "Direct Collection", "Payment Plan"
        ):
            frappe.throw("Choose a valid Payment Arrangement.")

        self.load_obligation_context()
        self.default_liquidated_amount()
        self.calculate_variance()
        self.apply_documented_penalty_account()
        self.validate_variance_draft()
        self.normalise_collection()
        self.calculate_collection_totals()
        self.derive_status()

    def default_liquidated_amount(self):
        """The submitted Assessment owns the final amount."""
        if not self.tax_assessment:
            return
        assessed = flt(self.expected_amount)
        assessed_penalty = flt(frappe.db.get_value(
            "Tax Assessment", self.tax_assessment, "assessed_penalty_amount"
        ))
        components = self.settlement_components or []
        penalty = sum(
            flt(row.amount) for row in components
            if row.component == "Penalty"
        )
        if components:
            if any(flt(row.amount) < 0 for row in components):
                frappe.throw("Settlement components cannot be negative.")
            if abs(sum(
                flt(row.amount) for row in components
                if row.component == "Principal"
            ) - (assessed - assessed_penalty)) > TOLERANCE:
                frappe.throw(
                    "The Principal component must equal the assessed "
                    "amount excluding its already recognised penalty."
                )
            if sum(row.component == "Penalty" for row in components) > 1:
                frappe.throw("Use one documented Penalty component.")
            if penalty + TOLERANCE < assessed_penalty:
                frappe.throw(
                    "The Settlement Penalty cannot be less than the "
                    "penalty already recognised on the Assessment."
                )
        new_penalty = penalty - assessed_penalty if components else 0
        final_amount = assessed + new_penalty
        if self.liquidated_amount in (None, "") or not flt(self.liquidated_amount):
            self.liquidated_amount = final_amount
        elif abs(flt(self.liquidated_amount) - final_amount) > TOLERANCE:
            frappe.throw(
                "Liquidated Amount must equal the submitted Tax "
                "Assessment plus any new documented Penalty component."
            )

    def apply_documented_penalty_account(self):
        components = self.settlement_components or []
        if not components:
            return
        penalty = sum(
            flt(row.amount) for row in components
            if row.component == "Penalty"
        )
        assessed_penalty = flt(frappe.db.get_value(
            "Tax Assessment", self.tax_assessment, "assessed_penalty_amount"
        )) if self.tax_assessment else 0
        new_penalty = penalty - assessed_penalty
        if new_penalty <= TOLERANCE:
            return
        if abs(flt(self.settlement_variance) - new_penalty) > TOLERANCE:
            frappe.throw(
                "The Settlement variance must equal the documented penalty."
            )
        if self.is_authority_settlement():
            return  # Authority accounts are loaded from Tax Penalty Settings.
        settings = frappe.get_all(
            "Tax Penalty Settings",
            filters={"company": self.company, "active": 1},
            fields=["tax_penalty_cost_account", "tax_penalty_cost_center"],
            limit=2,
        )
        if len(settings) != 1 or not settings[0].tax_penalty_cost_account:
            frappe.throw(
                "Exactly one active Tax Penalty Settings with a "
                "Penalty Cost Account is required."
            )
        self.cost_account = settings[0].tax_penalty_cost_account
        if not settings[0].tax_penalty_cost_center:
            frappe.throw("Tax Penalty Cost Center is not configured.")
        if not self.variance_reason:
            self.variance_reason = "Documented penalty on AGT liquidation"

    # ======================================================
    # FISCAL OBLIGATION
    # ======================================================


    def load_obligation_context(self):
        if not self.fiscal_obligation:
            return

        obligation = frappe.get_doc(
            "Fiscal Obligation",
            self.fiscal_obligation,
        )

        if obligation.docstatus != 1:
            frappe.throw(
                "Tax Settlement requires a submitted "
                "Fiscal Obligation."
            )

        self.statutory_payment_due_date = (
            get_statutory_payment_due_date(obligation)
        )

        if obligation.status in (
            "Closed",
            "Cancelled",
            "Superseded",
            "Paid",
        ):
            frappe.throw(
                f"Fiscal Obligation {obligation.name} "
                f"with status {obligation.status} cannot "
                "receive a Tax Settlement."
            )

        self.company = obligation.company

        self.tax_type = self.resolve_tax_type(
            obligation
        )

        if not self.tax_type:
            frappe.throw(
                "Fiscal Obligation does not resolve to "
                "a valid Tax Type."
            )

        if obligation.meta.has_field(
            "tax_authority"
        ):
            self.tax_authority = (
                obligation.get("tax_authority")
                or self.tax_authority
            )

        # =================================================
        # AUTHORITY ASSESSMENT ROUTE
        # =================================================

        if not self.tax_assessment and self.is_new():
            frappe.throw(
                "Create and submit a Tax Assessment before Tax Settlement."
            )

        if self.tax_assessment:
            assessment = frappe.get_doc("Tax Assessment", self.tax_assessment)
            if assessment.docstatus != 1 or assessment.fiscal_obligation != obligation.name:
                frappe.throw("Tax Settlement requires a submitted linked Assessment.")
            if (
                assessment.assessment_origin == "From Fiscal Obligation"
                and assessment.assessment_type == "Self-Determination"
            ):
                if obligation.obligation_origin != "Operational":
                    frappe.throw("Operational Assessment requires an operational obligation.")
            else:
                self.load_authority_settlement_context(obligation)
                return

        # =================================================
        # REGULAR SELF-DETERMINED ROUTE
        # =================================================

        self.settlement_basis = "Self-Determined"

        self.tax_account = obligation.get(
            "liability_account"
        )

        if not self.tax_account:
            frappe.throw(
                "Fiscal Obligation does not have a "
                "Liability Account. Regular Tax Settlement "
                "requires a Tax Control Account."
            )

        self.expected_amount = flt(
            obligation.get(
                "effective_obligation_amount"
            )
            or obligation.get(
                "amount_determined"
            )
            or 0
        )

        if self.tax_assessment:
            self.expected_amount = flt(
                assessment.effective_assessed_amount
                or assessment.amount_assessed
                or 0
            )

        if self.expected_amount <= 0:
            frappe.throw(
                "Tax Settlement requires a positive "
                "assessed or determined amount."
            )

        # Authority accounts are intentionally absent
        # from the regular route.
        self.tax_penalty_cost_account = None
        self.tax_provision_account = None
        self.tax_settlement_control_account = None


    # ======================================================
    # VARIANCE
    # ======================================================

    def calculate_variance(self):
        if self.liquidated_amount in (
            None,
            "",
        ):
            self.settlement_variance = 0
            self.has_variance = 0
            return

        self.settlement_variance = (
            flt(self.liquidated_amount)
            - flt(self.expected_amount)
        )

        self.has_variance = (
            1
            if abs(
                flt(self.settlement_variance)
            ) > TOLERANCE
            else 0
        )


    def validate_variance_draft(self):
        """
        Draft remains editable/incomplete.

        Strict variance accounting controls apply only
        when the Settlement is submitted.
        """
        return



    def validate_variance_submit(self):
        if not self.has_variance:
            return

        if not self.variance_reason:
            frappe.throw(
                "Variance Reason is mandatory when "
                "Liquidated Amount differs from "
                "Expected Amount."
            )

        if self.is_authority_settlement():
            required = {
                "Tax Penalty Cost Account":
                    self.tax_penalty_cost_account,
                "Tax Provision Account":
                    self.tax_provision_account,
                "Tax Settlement Control Account":
                    self.tax_settlement_control_account,
            }

            missing = [
                label
                for label, value in required.items()
                if not value
            ]

            if missing:
                frappe.throw(
                    "Authority Settlement accounting "
                    "configuration is incomplete: "
                    + ", ".join(missing)
                )

            return

        # Regular/self-determined route.
        if not self.tax_account:
            frappe.throw(
                "Tax Control Account is required for "
                "regular Settlement variance."
            )

        if not self.cost_account:
            frappe.throw(
                "Cost Account is required when a regular "
                "Tax Settlement contains a variance."
            )


    def validate_cost_account(self):
        if self.cost_account == self.tax_account:
            frappe.throw(
                "Cost Account cannot be the Tax "
                "Control Account."
            )

        account = frappe.get_doc(
            "Account",
            self.cost_account,
        )

        if account.company != self.company:
            frappe.throw(
                "Cost Account must belong to the "
                "Settlement Company."
            )

        if account.is_group:
            frappe.throw(
                "Cost Account must be a ledger account, "
                "not a group account."
            )

    # ======================================================
    # COLLECTION — DRAFT
    # ======================================================

    def normalise_collection(self):
        if not self.collection_mode:
            self.collection_mode = "Single RUPE"

        if self.collection_mode not in (
            "Single RUPE",
            "Multiple RUPEs",
        ):
            frappe.throw(
                "Collection Mode must be Single RUPE "
                "or Multiple RUPEs."
            )

        rows = self.collection_lines or []

        if (
            self.collection_mode == "Single RUPE"
            and len(rows) == 1
            and flt(self.liquidated_amount) > 0
        ):
            rows[0].rupe_amount = flt(
                self.liquidated_amount
            )

        # Draft rows may be incomplete, but any entered
        # amount cannot be negative and entered RUPEs
        # cannot duplicate each other.
        seen_rupe = set()

        for row in rows:
            if (
                row.rupe_amount not in (None, "")
                and flt(row.rupe_amount) < 0
            ):
                frappe.throw(
                    f"Row {row.idx}: RUPE Amount "
                    "cannot be negative."
                )

            if row.rupe:
                row.rupe = str(row.rupe).strip()

                if row.rupe:
                    if row.rupe in seen_rupe:
                        frappe.throw(
                            f"RUPE {row.rupe} is "
                            "duplicated in this Settlement."
                        )

                    seen_rupe.add(row.rupe)

    def calculate_collection_totals(self):
        self.collection_total = sum(
            flt(row.rupe_amount)
            for row in (
                self.collection_lines or []
            )
        )

        self.collection_variance = (
            flt(self.collection_total)
            - flt(self.liquidated_amount)
        )

    # ======================================================
    # STRICT SUBMIT VALIDATION
    # ======================================================

    def validate_collection_submit(self):
        rows = self.collection_lines or []

        if not rows:
            frappe.throw(
                "At least one Tax Collection row is "
                "required before submission."
            )

        if (
            self.collection_mode == "Single RUPE"
            and len(rows) != 1
        ):
            frappe.throw(
                "Single RUPE mode requires exactly "
                "one Tax Collection row."
            )

        if self.payment_arrangement != "Payment Plan":
            if not self.statutory_payment_due_date:
                frappe.throw(
                    "A statutory payment due date is required for "
                    "Direct Collection."
                )
            legal_due = getdate(self.statutory_payment_due_date)
            penalty = sum(
                flt(row.amount) for row in (self.settlement_components or [])
                if row.component == "Penalty"
            )
            if not self.settlement_components and self.tax_assessment:
                penalty = flt(frappe.db.get_value(
                    "Tax Assessment", self.tax_assessment,
                    "assessed_penalty_amount",
                ))
            for row in rows:
                if row.payment_due_date and getdate(row.payment_due_date) > legal_due:
                    if penalty <= 0 or not row.issue_date or getdate(row.issue_date) <= legal_due:
                        frappe.throw(
                            f"Row {row.idx}: A RUPE due after the statutory "
                            "date requires a documented late Penalty issued "
                            "after that date, or an approved Payment Plan."
                        )

        seen_rupe = set()

        for row in rows:
            if not row.nota_liquidacao:
                frappe.throw(
                    f"Row {row.idx}: Nota de Liquidação "
                    "is mandatory."
                )

            if not row.rupe:
                frappe.throw(
                    f"Row {row.idx}: RUPE is mandatory."
                )

            row.rupe = str(row.rupe).strip()

            if not row.rupe:
                frappe.throw(
                    f"Row {row.idx}: RUPE is mandatory."
                )

            if row.rupe in seen_rupe:
                frappe.throw(
                    f"RUPE {row.rupe} is duplicated "
                    "in this Settlement."
                )

            seen_rupe.add(row.rupe)

            if flt(row.rupe_amount) <= 0:
                frappe.throw(
                    f"Row {row.idx}: RUPE Amount must "
                    "be greater than zero."
                )

            if not row.issue_date:
                frappe.throw(
                    f"Row {row.idx}: Issue Date is "
                    "mandatory."
                )

            if not row.payment_due_date:
                frappe.throw(
                    f"Row {row.idx}: Payment Due Date "
                    "is mandatory."
                )

            existing = frappe.db.get_value(
                "Tax Collection Reference",
                {"rupe": row.rupe},
                "name",
            )

            if (
                existing
                and existing
                != row.collection_reference
            ):
                frappe.throw(
                    f"RUPE {row.rupe} already exists "
                    "in Tax Collection Reference "
                    f"{existing}."
                )

        # One Nota de Liquidação document may contain
        # multiple RUPEs. The supporting document therefore
        # belongs to the Tax Settlement header, not each
        # collection row.
        attachment = self.get(
            "nota_de_liquidacao_attachement"
        )

        if not attachment:
            frappe.throw(
                "Nota de Liquidação attachment is mandatory "
                "before submitting the Tax Settlement."
            )

        self.calculate_collection_totals()

        if self.tax_assessment and (
            abs(flt(self.collection_total) - flt(self.liquidated_amount))
            > TOLERANCE
        ):
            frappe.throw(
                "Total RUPE Amount must equal the assessed amount "
                "plus any documented penalty before submission."
            )

        if (
            abs(
                flt(self.collection_variance)
            ) > TOLERANCE
        ):
            frappe.throw(
                "Tax Collection total must equal the "
                "Liquidated Amount before submission. "
                f"Difference: "
                f"{flt(self.collection_variance)}"
            )

    # ======================================================
    # STATUS
    # ======================================================

    def derive_status(self):
        if self.docstatus == 0:
            self.status = "Awaiting Liquidation"

    # ======================================================
    # SUBMIT
    # ======================================================

    def before_submit(self):
        self.validate_variance_submit()
        self.validate_collection_submit()
        self.status = "Liquidated"


    def on_submit(self):
        # -------------------------------------------------
        # 1. Adjust the existing liability/provision
        #    to the final NL amount.
        # -------------------------------------------------

        if self.has_variance:
            self.create_adjustment_journal_entry()

        # -------------------------------------------------
        # 2. Authority Assessment only:
        #
        #    DR Tax Provision
        #    CR Tax Settlement Control
        #
        #    for the COMPLETE final liquidated amount.
        # -------------------------------------------------

        if self.is_authority_settlement():
            self.create_settlement_journal_entry()

        # -------------------------------------------------
        # 3. Treasury collection layer
        # -------------------------------------------------

        self.create_collection_references()
        self.update_fiscal_obligation()


    # ======================================================
    # VARIANCE JOURNAL ENTRY
    # ======================================================


    def create_adjustment_journal_entry(self):
        variance = flt(
            self.settlement_variance
        )

        if abs(variance) <= TOLERANCE:
            return

        if self.adjustment_journal_entry:
            je = frappe.get_doc(
                "Journal Entry",
                self.adjustment_journal_entry,
            )

            if je.docstatus != 1:
                frappe.throw(
                    "Existing Settlement Adjustment "
                    "Journal Entry must be submitted."
                )

            return

        posting_date = (
            self.get_liquidation_posting_date()
        )

        # =================================================
        # AUTHORITY ASSESSMENT VARIANCE
        # =================================================
        #
        # Final NL > recognised provision:
        #
        #   DR Tax Penalty Cost
        #   CR Tax Provision
        #
        # Final NL < recognised provision:
        #
        #   DR Tax Provision
        #   CR Tax Penalty Cost
        #
        # =================================================

        if self.is_authority_settlement():
            liability_account = (
                self.tax_provision_account
            )

            cost_account = (
                self.tax_penalty_cost_account
            )

            remark = (
                f"Authority Tax Settlement {self.name} "
                f"variance / Tax Assessment "
                f"{self.tax_assessment}"
            )

        # =================================================
        # REGULAR SELF-DETERMINED VARIANCE
        # =================================================
        #
        # Preserve the existing accounting rule:
        #
        #   Cost <-> Tax Control Account
        #
        # =================================================

        else:
            liability_account = (
                self.tax_account
            )

            cost_account = (
                self.cost_account
            )

            remark = (
                f"Tax Settlement {self.name} variance "
                f"/ Fiscal Obligation "
                f"{self.fiscal_obligation}"
            )

        if not liability_account:
            frappe.throw(
                "Variance liability/control account "
                "could not be resolved."
            )

        if not cost_account:
            frappe.throw(
                "Variance cost account could not "
                "be resolved."
            )

        je = frappe.new_doc(
            "Journal Entry"
        )

        je.company = self.company
        je.posting_date = posting_date
        je.user_remark = remark

        liability_row = je.append(
            "accounts",
            {},
        )

        liability_row.account = (
            liability_account
        )

        cost_row = je.append(
            "accounts",
            {},
        )

        cost_row.account = (
            cost_account
        )

        penalty_rows = [
            row for row in (self.settlement_components or [])
            if row.component == "Penalty"
        ]
        if penalty_rows:
            center = frappe.db.get_value(
                "Tax Penalty Settings",
                {"company": self.company, "active": 1},
                "tax_penalty_cost_center",
            )
            if not center:
                frappe.throw("Tax Penalty Cost Center is not configured.")
            cost_row.cost_center = center

        if variance > 0:
            # Final NL exceeds expected amount.
            #
            # DR Cost
            # CR Liability / Provision

            cost_row.debit_in_account_currency = (
                variance
            )
            cost_row.credit_in_account_currency = 0

            liability_row.debit_in_account_currency = 0
            liability_row.credit_in_account_currency = (
                variance
            )

        else:
            amount = abs(
                variance
            )

            # Final NL is below expected amount.
            #
            # DR Liability / Provision
            # CR Cost

            liability_row.debit_in_account_currency = (
                amount
            )
            liability_row.credit_in_account_currency = 0

            cost_row.debit_in_account_currency = 0
            cost_row.credit_in_account_currency = (
                amount
            )

        je.insert(
            ignore_permissions=True
        )

        submit_control_journal(
            je, self.fiscal_obligation, self.company, self.tax_type
        )

        self.db_set(
            "adjustment_journal_entry",
            je.name,
            update_modified=False,
        )

        self.adjustment_journal_entry = (
            je.name
        )


    # ======================================================
    # COLLECTION REFERENCES
    # ======================================================


    # =====================================================
    # AUTHORITY ASSESSMENT SETTLEMENT
    # =====================================================

    def is_authority_settlement(self):
        return bool(
            self.tax_assessment
            and self.settlement_basis in (
                "Accepted Assessment",
                "Agreement",
            )
        )


    def load_authority_settlement_context(
        self,
        obligation,
    ):
        assessment = frappe.get_doc(
            "Tax Assessment",
            self.tax_assessment,
        )

        if assessment.docstatus != 1:
            frappe.throw(
                "Authority Tax Settlement requires a "
                "submitted Tax Assessment."
            )

        if (
            assessment.fiscal_obligation
            != obligation.name
        ):
            frappe.throw(
                "Tax Assessment and Tax Settlement "
                "must refer to the same Fiscal Obligation."
            )

        if assessment.company != self.company:
            frappe.throw(
                "Tax Assessment Company does not match "
                "the Fiscal Obligation."
            )

        decision = (
            assessment.assessment_decision
        )

        if decision == "Accepted":
            self.settlement_basis = (
                "Accepted Assessment"
            )
            if assessment.assessment_origin == "From Tax Notification":
                net_amount = assessment.get("net_payable_amount")
                if net_amount in (None, "") or (
                    flt(net_amount) == 0
                    and flt(assessment.get("tax_credit_applied")) == 0
                ):
                    net_amount = (
                        assessment.effective_assessed_amount
                        or assessment.amount_assessed
                        or 0
                    )
                self.expected_amount = flt(net_amount)
            else:
                self.expected_amount = flt(
                    assessment.effective_assessed_amount
                    or assessment.amount_assessed
                    or 0
                )

        elif decision == "Agreement":
            self.settlement_basis = (
                "Agreement"
            )

            self.expected_amount = flt(
                assessment.agreed_amount
                or assessment.effective_assessed_amount
                or assessment.amount_assessed
                or 0
            )

        else:
            frappe.throw(
                "This accounting route only accepts "
                "submitted Accepted or Agreement "
                "Tax Assessments."
            )

        if self.expected_amount <= 0:
            frappe.throw(
                "Authority Tax Settlement requires "
                "a positive expected assessed amount."
            )

        # -------------------------------------------------
        # A provision must already exist.
        #
        # Assessment recognition:
        #
        #   DR Tax Penalty Cost
        #   CR Tax Provision
        # -------------------------------------------------

        recognition_je = (
            assessment.get(
                "recognition_journal_entry"
            )
        )

        if not recognition_je:
            frappe.throw(
                "The linked Tax Assessment does not have "
                "a Recognition Journal Entry. The "
                "assessment must be recognised before "
                "formal liquidation."
            )

        recognition = frappe.get_doc(
            "Journal Entry",
            recognition_je,
        )

        if recognition.docstatus != 1:
            frappe.throw(
                "The Tax Assessment Recognition Journal "
                "Entry must be submitted."
            )

        if recognition.company != self.company:
            frappe.throw(
                "Recognition Journal Entry belongs to "
                "a different company."
            )

        self.load_authority_accounts()

        # The original operational tax account is not the
        # payable account for this route.
        self.tax_account = None

        # Cost Account is manually selected only for the
        # regular/self-determined variance route.
        self.cost_account = None


    def load_authority_accounts(self):
        rows = frappe.get_all(
            "Tax Penalty Settings",
            filters={
                "company": self.company,
                "active": 1,
            },
            fields=[
                "name",
                "tax_penalty_cost_account",
                "tax_provision_account",
                "tax_settlement_control_account",
            ],
            limit=2,
        )

        if not rows:
            frappe.throw(
                "No active Tax Penalty Settings exists "
                f"for company {self.company}."
            )

        if len(rows) > 1:
            frappe.throw(
                "More than one active Tax Penalty Settings "
                f"record exists for company {self.company}."
            )

        settings = rows[0]

        self.tax_penalty_cost_account = (
            settings.tax_penalty_cost_account
        )

        self.tax_provision_account = (
            settings.tax_provision_account
        )

        self.tax_settlement_control_account = (
            settings.tax_settlement_control_account
        )

        for account, label in (
            (
                self.tax_penalty_cost_account,
                "Tax Penalty Cost Account",
            ),
            (
                self.tax_provision_account,
                "Tax Provision Account",
            ),
            (
                self.tax_settlement_control_account,
                "Tax Settlement Control Account",
            ),
        ):
            self.validate_authority_account(
                account,
                label,
            )

        if (
            self.tax_provision_account
            == self.tax_settlement_control_account
        ):
            frappe.throw(
                "Tax Provision Account and Tax Settlement "
                "Control Account cannot be the same account."
            )


    def validate_authority_account(
        self,
        account,
        label,
    ):
        if not account:
            frappe.throw(
                f"{label} is not configured."
            )

        values = frappe.db.get_value(
            "Account",
            account,
            [
                "company",
                "is_group",
            ],
        )

        if not values:
            frappe.throw(
                f"{label} does not exist."
            )

        company, is_group = values

        if company != self.company:
            frappe.throw(
                f"{label} must belong to "
                f"{self.company}."
            )

        if is_group:
            frappe.throw(
                f"{label} must be a ledger account."
            )


    def get_liquidation_posting_date(self):
        issue_dates = [
            row.issue_date
            for row in (
                self.collection_lines or []
            )
            if row.issue_date
        ]

        if not issue_dates:
            frappe.throw(
                "Liquidation Issue Date is required "
                "for Settlement accounting."
            )

        return min(
            issue_dates
        )


    def create_settlement_journal_entry(self):
        """
        Formal AGT liquidation converts the recognised
        provision into the collectible Settlement liability.

            DR Tax Provision
            CR Tax Settlement Control

        Always for the COMPLETE final liquidated amount.
        """

        if not self.is_authority_settlement():
            return

        if self.settlement_journal_entry:
            existing = frappe.get_doc(
                "Journal Entry",
                self.settlement_journal_entry,
            )

            if existing.docstatus != 1:
                frappe.throw(
                    "Existing Settlement Journal Entry "
                    "must be submitted."
                )

            return

        amount = flt(
            self.liquidated_amount
        )

        if amount <= 0:
            frappe.throw(
                "Liquidated Amount must be greater than "
                "zero before authority Settlement "
                "accounting can be created."
            )

        if not self.tax_provision_account:
            frappe.throw(
                "Tax Provision Account is required."
            )

        if not self.tax_settlement_control_account:
            frappe.throw(
                "Tax Settlement Control Account is required."
            )

        je = frappe.new_doc(
            "Journal Entry"
        )

        je.company = self.company

        je.posting_date = (
            self.get_liquidation_posting_date()
        )

        je.user_remark = (
            f"Formal Tax Settlement {self.name} "
            f"/ Tax Assessment {self.tax_assessment} "
            f"/ Fiscal Obligation "
            f"{self.fiscal_obligation}"
        )

        provision_row = je.append(
            "accounts",
            {},
        )

        provision_row.account = (
            self.tax_provision_account
        )

        provision_row.debit_in_account_currency = (
            amount
        )

        provision_row.credit_in_account_currency = 0

        settlement_row = je.append(
            "accounts",
            {},
        )

        settlement_row.account = (
            self.tax_settlement_control_account
        )

        settlement_row.debit_in_account_currency = 0

        settlement_row.credit_in_account_currency = (
            amount
        )

        je.insert(
            ignore_permissions=True
        )

        submit_control_journal(
            je, self.fiscal_obligation, self.company, self.tax_type
        )

        self.db_set(
            "settlement_journal_entry",
            je.name,
            update_modified=False,
        )

        self.settlement_journal_entry = (
            je.name
        )

    def create_collection_references(self):
        if self.payment_arrangement == "Payment Plan":
            # The Plan will issue references against its control account.
            return
        for row in self.collection_lines:
            if row.collection_reference:
                existing = frappe.get_doc(
                    "Tax Collection Reference",
                    row.collection_reference,
                )

                if (
                    existing.tax_settlement
                    != self.name
                    or existing.rupe != row.rupe
                ):
                    frappe.throw(
                        "Stored Tax Collection Reference "
                        "does not match this Settlement."
                    )

                continue

            ref = frappe.new_doc(
                "Tax Collection Reference"
            )

            ref.fiscal_obligation = (
                self.fiscal_obligation
            )
            ref.tax_settlement = self.name
            ref.source_type = "Tax Settlement"
            ref.nota_liquidacao = (
                row.nota_liquidacao
            )
            ref.rupe = row.rupe
            ref.amount = flt(
                row.rupe_amount
            )
            ref.issue_date = row.issue_date
            ref.due_date = (
                row.payment_due_date
            )
            ref.status = "Issued"

            ref.insert(
                ignore_permissions=True
            )

            frappe.db.set_value(
                "Tax Settlement Collection",
                row.name,
                "collection_reference",
                ref.name,
                update_modified=False,
            )

            row.collection_reference = ref.name

    # ======================================================
    # FISCAL OBLIGATION ROLL-UP
    # ======================================================

    def update_fiscal_obligation(self):
        obligation = frappe.get_doc(
            "Fiscal Obligation",
            self.fiscal_obligation,
        )

        obligation.db_set(
            "latest_tax_settlement",
            self.name,
            update_modified=False,
        )

        obligation.db_set(
            "effective_obligation_amount",
            flt(self.liquidated_amount),
            update_modified=False,
        )

        obligation.db_set(
            "outstanding_amount",
            max(
                flt(self.liquidated_amount)
                - flt(obligation.amount_paid),
                0,
            ),
            update_modified=False,
        )

        obligation.db_set(
            "status",
            "Liquidated",
            update_modified=False,
        )

    # ======================================================
    # TAX TYPE
    # ======================================================

    def resolve_tax_type(
        self,
        obligation,
    ):
        if (
            obligation.meta.has_field(
                "tax_type"
            )
            and obligation.get("tax_type")
        ):
            return obligation.get("tax_type")

        if obligation.get("tax_code"):
            tax_type = frappe.db.get_value(
                "Tax Type",
                {
                    "company": obligation.company,
                    "tax_code":
                        obligation.get(
                            "tax_code"
                        ),
                    "active": 1,
                },
                "name",
            )

            if tax_type:
                return tax_type

        if obligation.get("tax_name"):
            return frappe.db.get_value(
                "Tax Type",
                {
                    "company": obligation.company,
                    "tax_name":
                        obligation.get(
                            "tax_name"
                        ),
                    "active": 1,
                },
                "name",
            )

        return None

    # ======================================================
    # PROTECTION
    # ======================================================

    def before_cancel(self):
        frappe.throw(
            "Submitted Tax Settlements cannot be "
            "cancelled."
        )

    def on_trash(self):
        if self.docstatus == 1:
            frappe.throw(
                "Submitted Tax Settlements cannot be "
                "deleted."
            )
