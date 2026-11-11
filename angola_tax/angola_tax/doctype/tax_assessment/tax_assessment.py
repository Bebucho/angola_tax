import math

import frappe
from frappe.model.document import Document
from frappe.utils import flt, add_days, nowdate
from angola_tax.statutory_deadline import get_statutory_payment_due_date


class TaxAssessment(Document):

    def is_operational_assessment(self):
        # Existing authority cases with this origin and Type
        # Notification retain their historical accounting route.
        return (
            self.assessment_origin == "From Fiscal Obligation"
            and self.assessment_type == "Self-Determination"
        )

    def before_validate(self):
        self.validate_not_superseded()
        """
        Inherit the source Fiscal Obligation context before
        normal Tax Assessment validation runs.
        """

        if (
            self.assessment_origin
            in ("From Fiscal Obligation", "From Tax Notification")
            and self.fiscal_obligation
        ):
            self.load_fiscal_obligation_context()

    def validate(self):
        self.calculate_legal_time_limit()
        self.validate_basic_data()
        self.validate_fiscal_obligation_eligibility()
        self.validate_assessment_type()
        self.calculate_assessment_variance()

        if (
            self.assessment_origin
            in ("From Fiscal Obligation", "From Tax Notification")
        ):
            self.load_fiscal_obligation_context()

        self.validate_decision()
        self.validate_decision_evidence()
        self.validate_notification_credit()

        self.calculate_effective_amount()
        self.apply_operational_penalty_account()
        self.validate_operational_variance()

    def apply_operational_penalty_account(self):
        penalty = flt(self.get("assessed_penalty_amount"))
        if penalty < 0:
            frappe.throw("Assessed Penalty Amount cannot be negative.")
        if not penalty:
            return
        if not self.is_operational_assessment():
            frappe.throw(
                "Assessed Penalty Amount belongs to an operational assessment."
            )
        if abs(penalty - flt(self.assessment_variance)) > 0.01:
            frappe.throw(
                "Assessed Penalty Amount must equal the Assessment Variance. "
                "A principal correction and a penalty need separate accounting."
            )
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
        self.adjustment_counterpart_account = settings[0].tax_penalty_cost_account
        if settings[0].tax_penalty_cost_center:
            self.adjustment_cost_center = settings[0].tax_penalty_cost_center
        if not self.assessment_reason:
            self.assessment_reason = "Documented tax penalty"

    def validate_notification_credit(self):
        if self.assessment_origin != "From Tax Notification":
            return

        assessed = flt(self.amount_assessed)
        credit = flt(self.get("tax_credit_applied"))
        if credit < 0:
            frappe.throw("Tax Credit Applied cannot be negative.")
        if credit > assessed:
            frappe.throw("Tax Credit Applied cannot exceed Amount Assessed.")

        balance = max(assessed - credit, 0)
        supplied_balance = self.get("net_payable_amount")
        if supplied_balance not in (None, "") and abs(flt(supplied_balance) - balance) > 0.01:
            frappe.throw("Balance must equal Amount Assessed less Tax Credit Applied.")
        self.net_payable_amount = balance

        if self.assessment_decision == "Accepted" and credit:
            if not self.tax_credit_account:
                frappe.throw("Tax Credit Account is required when a credit is applied.")
            values = frappe.db.get_value(
                "Account", self.tax_credit_account, ["company", "is_group"]
            )
            if not values or values[0] != self.company or values[1]:
                frappe.throw("Tax Credit Account must be a ledger account belonging to the Company.")


    def before_submit(self):
        if self.is_operational_assessment():
            self.validate_submitted_operational_adjustment()
            return
        if self.assessment_decision == "Pending":
            frappe.throw(
                "A Pending Tax Assessment cannot be submitted. "
                "Record either Accepted or Disputed first."
            )


    def create_submission_decision_log(self):
        if self.assessment_decision not in (
            "Accepted",
            "Disputed",
            "Agreement",
            "Cancelled by AGT",
        ):
            return

        log = frappe.new_doc(
            "Tax Assessment Decision Log"
        )

        previous = frappe.get_all(
            "Tax Assessment Decision Log",
            filters={
                "tax_assessment": self.name
            },
            fields=["sequence"],
            order_by="sequence desc",
            limit=1,
        )

        sequence = (
            int(previous[0].sequence or 0) + 1
            if previous
            else 1
        )

        if log.meta.has_field("tax_assessment"):
            log.tax_assessment = self.name

        if log.meta.has_field("sequence"):
            log.sequence = sequence

        if log.meta.has_field("recorded_at"):
            log.recorded_at = (
                frappe.utils.now_datetime()
            )

        if log.meta.has_field("recorded_by"):
            log.recorded_by = (
                frappe.session.user
            )

        if log.meta.has_field("previous_decision"):
            log.previous_decision = "Pending"

        if log.meta.has_field("new_decision"):
            log.new_decision = (
                self.assessment_decision
            )

        if log.meta.has_field("decision_date"):
            log.decision_date = (
                self.get("decision_date")
            )

        if log.meta.has_field("reference"):
            log.reference = (
                self.get("decision_reference")
            )

        if log.meta.has_field("document"):
            log.document = (
                self.get("decision_document")
            )

        if log.meta.has_field("notes"):
            log.notes = (
                self.get("decision_notes")
            )

        if log.meta.has_field("changed_by"):
            log.changed_by = (
                frappe.session.user
            )

        log.insert(
            ignore_permissions=True
        )

    def after_insert(self):
        """
        Establish the Fiscal Obligation relationship.

        Direct AGT Notification:
            creates its first Fiscal Obligation.

        Reissue / Additional / Correction:
            creates a successor Fiscal Obligation and
            supersedes the previous assessment/obligation.
        """

        successor_types = (
            "Reissue",
            "Additional",
            "Correction",
        )

        if self.assessment_type in successor_types:
            self.create_successor_obligation()
            return

        if (
            self.assessment_origin
            == "Direct AGT Assessment"
            and not self.fiscal_obligation
        ):
            obligation = (
                self.create_fiscal_obligation_from_direct_assessment()
            )

            self.db_set(
                "fiscal_obligation",
                obligation,
                update_modified=False,
            )

            self.fiscal_obligation = obligation

            self.sync_fiscal_obligation()
            return

        # Existing linked assessments continue using
        # their Fiscal Obligation.
        if self.fiscal_obligation:
            self.sync_fiscal_obligation()

    def before_cancel(self):
        frappe.throw(
            "Submitted Tax Assessments cannot be cancelled. "
            "Use a Correction, Reissue or successor Assessment "
            "to preserve the tax audit trail."
        )

    def on_trash(self):
        if not self.is_new():
            frappe.throw(
                "Tax Assessments cannot be deleted once "
                "recorded. Use the controlled successor "
                "workflow instead."
            )

    def on_submit(self):
        if self.is_operational_assessment():
            self.publish_operational_assessment()
            return
        """
        Formal submission is the accounting recognition gate.

        Accepted:
            DR Tax Penalty Cost
            CR Tax Provision

        Disputed:
            no automatic accounting entry.
            Accounting treatment belongs to the later
            litigation/provision assessment.
        """

        if self.assessment_decision == "Accepted":
            _ensure_assessment_recognition_journal_entry(
                assessment=self,
                posting_date=(
                    self.acceptance_date
                    or self.get("date_received")
                ),
            )

        elif self.assessment_decision == "Disputed":
            return

    def validate_not_superseded(self):
        """
        A superseded assessment is historical evidence
        and cannot be edited retroactively.
        """

        if self.is_new():
            return

        superseded_by = frappe.db.get_value(
            "Tax Assessment",
            self.name,
            "superseded_by",
        )

        if (
            superseded_by
            and not getattr(
                self.flags,
                "allow_superseded_update",
                False,
            )
        ):
            frappe.throw(
                f"Tax Assessment {self.name} has been "
                f"superseded by {superseded_by} and is "
                "read-only."
            )


    def create_successor_obligation(self):
        """
        Create a successor Fiscal Obligation for an authority
        Reissue, Additional Assessment or Correction.

        The successor represents the new tax exposure.

        Historical filing, payment and settlement data remain
        on the predecessor obligation and are not copied as if
        they belonged to the successor.
        """

        if self.assessment_type not in (
            "Reissue",
            "Additional",
            "Correction",
        ):
            return

        if not self.previous_assessment:
            frappe.throw(
                "Previous Assessment is required for "
                f"{self.assessment_type}."
            )

        previous_assessment = frappe.get_doc(
            "Tax Assessment",
            self.previous_assessment,
        )

        if not previous_assessment.fiscal_obligation:
            frappe.throw(
                f"Previous Assessment "
                f"{previous_assessment.name} is not linked "
                "to a Fiscal Obligation."
            )

        old_obligation = frappe.get_doc(
            "Fiscal Obligation",
            previous_assessment.fiscal_obligation,
        )

        if old_obligation.docstatus != 1:
            frappe.throw(
                "The Fiscal Obligation being superseded "
                "must be submitted."
            )

        if old_obligation.superseded_by:
            frappe.throw(
                f"Fiscal Obligation {old_obligation.name} "
                f"has already been superseded by "
                f"{old_obligation.superseded_by}."
            )

        if previous_assessment.superseded_by:
            frappe.throw(
                f"Tax Assessment "
                f"{previous_assessment.name} has already "
                f"been superseded by "
                f"{previous_assessment.superseded_by}."
            )

        assessed = flt(
            self.effective_assessed_amount
            or self.amount_assessed
            or 0
        )

        new_obligation = frappe.new_doc(
            "Fiscal Obligation"
        )

        # --------------------------------------------------
        # IDENTIFICATION / MASTER CONTEXT
        # --------------------------------------------------

        new_obligation.obligation_origin = (
            "Assessment Successor"
        )

        new_obligation.source_tax_assessment = (
            self.name
        )

        new_obligation.supersedes = (
            old_obligation.name
        )

        new_obligation.company = (
            old_obligation.company
        )

        new_obligation.estado = (
            old_obligation.estado
        )

        new_obligation.tax_name = (
            old_obligation.tax_name
        )

        new_obligation.tax_code = (
            old_obligation.tax_code
        )

        new_obligation.obligation_type = (
            old_obligation.obligation_type
        )

        new_obligation.currency = (
            old_obligation.currency
        )

        new_obligation.responsible_user = (
            self.responsible_user
            or old_obligation.responsible_user
        )

        # --------------------------------------------------
        # PERIOD
        # --------------------------------------------------

        new_obligation.fiscal_period_from = (
            old_obligation.fiscal_period_from
        )

        new_obligation.fiscal_period_to = (
            old_obligation.fiscal_period_to
        )

        if new_obligation.meta.has_field(
            "fiscal_year"
        ):
            new_obligation.fiscal_year = (
                old_obligation.fiscal_year
            )

        # --------------------------------------------------
        # DETERMINATION / SOURCE CONTEXT
        # --------------------------------------------------

        # Preserve the original system determination as
        # contextual information where one exists.
        if new_obligation.meta.has_field(
            "amount_determined"
        ):
            new_obligation.amount_determined = (
                old_obligation.amount_determined
            )

        for fieldname in (
            "tax_type",
            "liability_account",
            "receivable_account",
            "source_module",
            "source_document_type",
            "source_document",
        ):
            if (
                new_obligation.meta.has_field(fieldname)
                and old_obligation.meta.has_field(fieldname)
            ):
                new_obligation.set(
                    fieldname,
                    old_obligation.get(fieldname),
                )

        # Assessment data is applied after submission because
        # Fiscal Obligation validation may restrict assessment
        # fields while the document is Draft.
        new_obligation.insert(
            ignore_permissions=True
        )

        new_obligation.submit()

        # --------------------------------------------------
        # REGISTER NEW AUTHORITY EXPOSURE
        # --------------------------------------------------

        values = {
            "amount_assessed":
                assessed,

            "assessment_variance":
                flt(self.assessment_variance),

            "effective_obligation_amount":
                assessed,

            "amount_paid":
                0,

            "outstanding_amount":
                assessed,

            "status":
                "Under Review",
        }

        if new_obligation.meta.has_field(
            "latest_tax_assessment"
        ):
            values["latest_tax_assessment"] = (
                self.name
            )

        if new_obligation.meta.has_field(
            "assessment_document"
        ):
            values["assessment_document"] = (
                self.document
            )

        frappe.db.set_value(
            "Fiscal Obligation",
            new_obligation.name,
            values,
            update_modified=False,
        )

        new_obligation.reload()

        # --------------------------------------------------
        # FREEZE PREDECESSORS
        # --------------------------------------------------

        frappe.db.set_value(
            "Tax Assessment",
            previous_assessment.name,
            "superseded_by",
            self.name,
            update_modified=True,
        )

        frappe.db.set_value(
            "Fiscal Obligation",
            old_obligation.name,
            {
                "status":
                    "Superseded",

                "superseded_by":
                    new_obligation.name,
            },
            update_modified=True,
        )

        # --------------------------------------------------
        # MOVE CURRENT ASSESSMENT TO SUCCESSOR EXPOSURE
        # --------------------------------------------------

        self.db_set(
            "fiscal_obligation",
            new_obligation.name,
            update_modified=False,
        )

        self.fiscal_obligation = (
            new_obligation.name
        )

        return new_obligation.name


    def on_update(self):
        """
        Once the assessment has a Fiscal Obligation, keep that
        obligation synchronized as the assessment case evolves.
        """
        if self.fiscal_obligation:
            self.sync_fiscal_obligation()
        if self.is_operational_assessment():
            self.prepare_operational_adjustment()

    # ======================================================
    # BASIC VALIDATION
    # ======================================================

    def calculate_legal_time_limit(self):
        if (
            self.date_received
            and self.legal_time_limit_days is not None
        ):
            self.legal_time_limit_date = add_days(
                self.date_received,
                int(self.legal_time_limit_days),
            )
        else:
            self.legal_time_limit_date = None

    def validate_basic_data(self):
        if self.is_new() and self.assessment_origin == "Direct AGT Assessment":
            frappe.throw(
                "Create a Tax Notification first. Saving it creates "
                "the authority Fiscal Obligation."
            )

        if self.is_new() and self.assessment_origin == "From Fiscal Obligation":
            if self.assessment_type != "Self-Determination":
                frappe.throw(
                    "Start authority cases from Tax Notification; "
                    "operational Assessments use Self-Determination."
                )

        if self.is_new() and self.assessment_origin == "From Tax Notification":
            if self.assessment_type != "Notification":
                frappe.throw("Reissue and corrections are not implemented yet.")

        if not self.assessment_origin:
            frappe.throw(
                "Assessment Origin is mandatory."
            )

        if self.assessment_origin not in (
            "From Fiscal Obligation",
            "From Tax Notification",
            "Direct AGT Assessment",
        ):
            frappe.throw(
                "Invalid Assessment Origin."
            )

        if not self.company:
            frappe.throw(
                "Company is mandatory."
            )

        if not self.tax_type:
            frappe.throw(
                "Tax Type is mandatory."
            )

        # Originating AGT document is mandatory on Save.
        if not self.is_operational_assessment() and not self.get("attach"):
            frappe.throw(
                "Attach the originating assessment "
                "document before saving."
            )

        if not self.fiscal_period_from:
            frappe.throw(
                "Fiscal Period From is mandatory."
            )

        if not self.fiscal_period_to:
            frappe.throw(
                "Fiscal Period To is mandatory."
            )

        # Amount Filed is intentionally NOT mandatory here.
        #
        # It belongs to the declaration / Fiscal Obligation
        # context and may legitimately be blank for a
        # Direct AGT Assessment.

        # Direct AGT Assessment requires explicit
        # responsibility and receipt evidence.
        if (
            self.assessment_origin
            == "Direct AGT Assessment"
        ):
            if not self.responsible_user:
                frappe.throw(
                    "Responsible User is mandatory "
                    "for a Direct AGT Assessment."
                )

            if not self.date_received:
                frappe.throw(
                    "Date Received is mandatory "
                    "for a Direct AGT Assessment."
                )

        if (
            self.assessment_origin
            in ("From Fiscal Obligation", "From Tax Notification")
        ):
            if not self.fiscal_obligation:
                frappe.throw(
                    "Fiscal Obligation is mandatory when "
                    "Assessment Origin is From Fiscal Obligation."
                )

            obligation = frappe.get_doc(
                "Fiscal Obligation",
                self.fiscal_obligation,
            )

            expected_origin = None
            if self.is_operational_assessment():
                expected_origin = "Operational"
            elif self.assessment_origin == "From Tax Notification":
                expected_origin = "Tax Notification"

            if expected_origin and obligation.obligation_origin != expected_origin:
                frappe.throw(
                    f"This Assessment route requires a {expected_origin} "
                    "Fiscal Obligation."
                )

            if obligation.docstatus != 1:
                frappe.throw(
                    "Only a submitted Fiscal Obligation "
                    "can originate a Tax Assessment."
                )

            if flt(
                obligation.outstanding_amount
            ) <= 0:
                zero_balance_notification = False
                if self.assessment_origin == "From Tax Notification":
                    source = obligation.get("source_tax_notification")
                    if source and frappe.db.exists("Tax Notification", source):
                        notice = frappe.get_doc("Tax Notification", source)
                        zero_balance_notification = (
                            flt(notice.get("claimed_amount")) > 0
                            and flt(notice.get("tax_credit_applied")) >= flt(notice.get("claimed_amount"))
                        )
                if not zero_balance_notification:
                    frappe.throw(
                        "The selected Fiscal Obligation has no "
                        "outstanding amount and cannot originate "
                        "a Tax Assessment."
                    )

            if (
                obligation.company
                and self.company
                and obligation.company != self.company
            ):
                frappe.throw(
                    "The selected Fiscal Obligation belongs "
                    "to a different Company."
                )


    # ======================================================
    # ASSESSMENT LINEAGE
    # ======================================================

    def validate_fiscal_obligation_eligibility(self):
        """
        A Tax Assessment originating from an existing
        Fiscal Obligation may only use an active,
        submitted obligation with an outstanding balance.
        """

        if (
            self.assessment_origin
            not in ("From Fiscal Obligation", "From Tax Notification")
        ):
            return

        if not self.fiscal_obligation:
            # Existing validate_basic_data() already gives
            # the user-facing mandatory message.
            return

        obligation = frappe.get_doc(
            "Fiscal Obligation",
            self.fiscal_obligation,
        )

        if obligation.docstatus != 1:
            frappe.throw(
                "Only a submitted Fiscal Obligation "
                "can originate a Tax Assessment."
            )

        if (
            self.company
            and obligation.company
            and self.company != obligation.company
        ):
            frappe.throw(
                "The selected Fiscal Obligation belongs "
                "to a different Company."
            )

    def validate_assessment_type(self):
        if self.is_operational_assessment():
            if self.assessment_type != "Self-Determination":
                frappe.throw("Operational Assessment Type must be Self-Determination.")
            if self.previous_assessment:
                frappe.throw("Operational Assessment cannot have a predecessor.")
            return

        """
        Validate authority-assessment lineage.

        Tax Assessment represents an authority assessment,
        notification, reissue, additional assessment or correction.

        Nota de Liquidacao belongs to Tax Settlement,
        not Tax Assessment.
        """

        valid_types = (
            "Notification",
            "Reissue",
            "Additional",
            "Correction",
        )

        if self.assessment_type not in valid_types:
            frappe.throw(
                f"Invalid Assessment Type: "
                f"{self.assessment_type}."
            )

        # --------------------------------------------------
        # FIRST AUTHORITY ASSESSMENT
        # --------------------------------------------------

        if self.assessment_type == "Notification":

            if self.previous_assessment:
                frappe.throw(
                    "Assessment Type Notification cannot "
                    "have a Previous Assessment."
                )

            if self.fiscal_obligation:
                filters = {
                    "fiscal_obligation":
                        self.fiscal_obligation,
                }

                if self.name:
                    filters["name"] = [
                        "!=",
                        self.name,
                    ]

                existing = frappe.db.exists(
                    "Tax Assessment",
                    filters,
                )

                if existing:
                    frappe.throw(
                        "Fiscal Obligation "
                        f"{self.fiscal_obligation} already "
                        "has a Tax Assessment. Create a "
                        "Reissue, Additional or Correction "
                        "from the existing assessment."
                    )

            self.previous_assessment = None

            if self.meta.has_field(
                "previous_assessed_amount"
            ):
                self.previous_assessed_amount = 0

            return

        # --------------------------------------------------
        # SUCCESSOR AUTHORITY ASSESSMENT
        # --------------------------------------------------

        if (
            not self.is_new()
            and self.previous_assessment
        ):
            previous_name = (
                self.previous_assessment
            )
        else:
            previous_name = (
                self.previous_assessment
                or
                self.get_previous_assessment_from_obligation()
            )

        if not previous_name:
            frappe.throw(
                "A Reissue, Additional or Correction "
                "requires a Previous Assessment."
            )

        previous = frappe.get_doc(
            "Tax Assessment",
            previous_name,
        )

        if (
            previous.company
            and self.company
            and previous.company != self.company
        ):
            frappe.throw(
                "Previous Assessment belongs to a "
                "different Company."
            )

        if (
            previous.tax_type
            and self.tax_type
            and previous.tax_type != self.tax_type
        ):
            frappe.throw(
                "Previous Assessment belongs to a "
                "different Tax Type."
            )

        self.previous_assessment = previous_name

        previous_amount = flt(
            previous.effective_assessed_amount
            or previous.amount_assessed
            or 0
        )

        if self.meta.has_field(
            "previous_assessed_amount"
        ):
            self.previous_assessed_amount = (
                previous_amount
            )


    def get_previous_assessment_from_obligation(self):
        """
        Resolve the immediate predecessor automatically.

        Prefer the most recently created assessment linked
        to this Fiscal Obligation. The current document is
        excluded when editing.
        """

        if not self.fiscal_obligation:
            return None

        filters = {
            "fiscal_obligation":
                self.fiscal_obligation,
        }

        if self.name:
            filters["name"] = [
                "!=",
                self.name,
            ]

        rows = frappe.get_all(
            "Tax Assessment",
            filters=filters,
            fields=[
                "name",
                "creation",
            ],
            order_by="creation desc",
            limit=1,
        )

        if rows:
            return rows[0].name

        # Defensive fallback for obligations created directly
        # from an AGT assessment.
        meta = frappe.get_meta(
            "Fiscal Obligation"
        )

        if meta.has_field(
            "source_tax_assessment"
        ):
            source = frappe.db.get_value(
                "Fiscal Obligation",
                self.fiscal_obligation,
                "source_tax_assessment",
            )

            if (
                source
                and source != self.name
            ):
                return source

        return None


    # ======================================================
    # DECISION
    # ======================================================

    def validate_assessment_amount(self):
        raw = self.get("amount_assessed")

        if raw in (
            None,
            "",
        ):
            frappe.throw(
                "Amount Assessed is mandatory."
            )

        if flt(raw) < 0:
            frappe.throw(
                "Amount Assessed cannot be negative."
            )

    def calculate_assessment_variance(self):
        if self.is_operational_assessment():
            self.assessment_variance = (
                flt(self.amount_assessed) - flt(self.determined_amount)
            )
            return

        # OVERDUE_ASSESSMENT_VARIANCE_GUARD_V1
        #
        # An overdue existing obligation is not a
        # re-determination of the original tax principal.
        # The original outstanding principal remains
        # authoritative; therefore no Assessment variance
        # is created at this stage.
        if self.get("overdue_obligation"):
            self.assessment_variance = 0

            if self.meta.has_field(
                "previous_assessed_amount"
            ):
                self.previous_assessed_amount = 0

            return

        """
        Assessment variance measures changes inside the
        authority-assessment lineage.

        Notification has no predecessor.

        Reissue, Additional and Correction are compared
        with the previous effective assessed amount.
        """

        assessed = flt(
            self.amount_assessed
        )

        if self.assessment_type == "Notification":
            self.assessment_variance = 0

            if self.meta.has_field(
                "previous_assessed_amount"
            ):
                self.previous_assessed_amount = 0

            return

        if self.assessment_type in (
            "Reissue",
            "Additional",
            "Correction",
        ):
            if not self.previous_assessment:
                frappe.throw(
                    "Previous Assessment is required "
                    "for a successor assessment."
                )

            previous = frappe.get_doc(
                "Tax Assessment",
                self.previous_assessment,
            )

            previous_amount = flt(
                previous.effective_assessed_amount
                or previous.amount_assessed
                or 0
            )

            if self.meta.has_field(
                "previous_assessed_amount"
            ):
                self.previous_assessed_amount = (
                    previous_amount
                )

            self.assessment_variance = (
                assessed - previous_amount
            )

            return

        frappe.throw(
            f"Invalid Assessment Type: "
            f"{self.assessment_type}."
        )

    def validate_operational_variance(self):
        if not self.is_operational_assessment():
            return

        previous = self.get_doc_before_save()
        if previous and previous.get("adjustment_journal_entry"):
            je_status = frappe.db.get_value(
                "Journal Entry", previous.adjustment_journal_entry, "docstatus"
            )
            if je_status == 1 and (
                flt(previous.amount_assessed) != flt(self.amount_assessed)
                or flt(previous.get("assessed_penalty_amount"))
                != flt(self.get("assessed_penalty_amount"))
                or previous.adjustment_counterpart_account
                != self.adjustment_counterpart_account
                or previous.adjustment_cost_center
                != self.adjustment_cost_center
            ):
                frappe.throw(
                    "The submitted Assessment Adjustment Journal Entry is locked. "
                    "Do not change the assessed amount or counterpart account."
                )

        if abs(flt(self.assessment_variance)) <= 0.01:
            return

        if not self.assessment_reason:
            frappe.throw("Assessment Reason is required for a variance.")
        if not self.adjustment_counterpart_account:
            frappe.throw("Adjustment Counterpart Account is required for a variance.")
        if not self.adjustment_cost_center:
            frappe.throw("Adjustment Cost Center is required for a variance.")

        obligation = frappe.get_doc("Fiscal Obligation", self.fiscal_obligation)
        tax_account = (
            obligation.liability_account if obligation.obligation_type == "Payable"
            else obligation.receivable_account
        )
        if not tax_account:
            frappe.throw("The operational Fiscal Obligation has no Tax Account.")
        if tax_account == self.adjustment_counterpart_account:
            frappe.throw("Counterpart Account must differ from the Tax Account.")
        if frappe.db.exists("Tax Account Configuration", {
            "company": self.company,
            "account": self.adjustment_counterpart_account,
            "active": 1,
            "docstatus": 1,
        }):
            frappe.throw("Counterpart Account cannot be another configured Tax Account.")

        currency = frappe.db.get_value("Company", self.company, "default_currency")
        for name in (tax_account, self.adjustment_counterpart_account):
            account = frappe.db.get_value(
                "Account", name,
                ["company", "is_group", "account_currency"], as_dict=True,
            )
            if not account or account.company != self.company or account.is_group:
                frappe.throw(f"Choose a ledger Account belonging to {self.company}: {name}")
            if account.account_currency and account.account_currency != currency:
                frappe.throw("Assessment Adjustment accounts must use company currency.")

        center = frappe.db.get_value(
            "Cost Center", self.adjustment_cost_center,
            ["company", "is_group"], as_dict=True,
        )
        if not center or center.company != self.company or center.is_group:
            frappe.throw("Choose a ledger Cost Center belonging to the Company.")

    def prepare_operational_adjustment(self):
        if not self.name or not self.fiscal_obligation:
            return

        existing = (
            frappe.get_doc("Journal Entry", self.adjustment_journal_entry)
            if self.adjustment_journal_entry else None
        )
        variance = flt(self.assessment_variance)
        if abs(variance) <= 0.01:
            if existing:
                if existing.docstatus != 0:
                    frappe.throw("A posted Assessment adjustment cannot be removed.")
                self.db_set("adjustment_journal_entry", None, update_modified=False)
                self.adjustment_journal_entry = None
                existing.delete(ignore_permissions=True)
            return

        if existing and existing.docstatus == 1:
            return
        if existing and existing.docstatus != 0:
            frappe.throw("The Adjustment Journal Entry is not editable.")

        obligation = frappe.get_doc("Fiscal Obligation", self.fiscal_obligation)
        tax_account = (
            obligation.liability_account if obligation.obligation_type == "Payable"
            else obligation.receivable_account
        )
        je = existing or frappe.new_doc("Journal Entry")
        je.company = self.company
        if not je.posting_date:
            je.posting_date = nowdate()
        je.user_remark = (
            f"Tax Assessment {self.name} adjustment / "
            f"Fiscal Obligation {self.fiscal_obligation}"
        )
        je.set("accounts", [])
        control = je.append("accounts", {"account": tax_account})
        counterpart = je.append("accounts", {
            "account": self.adjustment_counterpart_account,
            "cost_center": self.adjustment_cost_center,
        })
        amount = abs(variance)
        increase_control = variance > 0
        if obligation.obligation_type == "Receivable":
            increase_control = not increase_control
        if increase_control:
            control.credit_in_account_currency = amount
            counterpart.debit_in_account_currency = amount
        else:
            control.debit_in_account_currency = amount
            counterpart.credit_in_account_currency = amount

        if existing:
            je.save(ignore_permissions=True)
        else:
            je.insert(ignore_permissions=True)
            self.db_set("adjustment_journal_entry", je.name, update_modified=False)
            self.adjustment_journal_entry = je.name

    def validate_submitted_operational_adjustment(self):
        variance = flt(self.assessment_variance)
        if abs(variance) <= 0.01:
            return
        if not self.adjustment_journal_entry:
            frappe.throw("Save the Assessment to prepare its Adjustment Journal Entry.")
        je = frappe.get_doc("Journal Entry", self.adjustment_journal_entry)
        if je.docstatus != 1 or je.company != self.company:
            frappe.throw("Submit the linked Adjustment Journal Entry first.")

        obligation = frappe.get_doc("Fiscal Obligation", self.fiscal_obligation)
        tax_account = (
            obligation.liability_account if obligation.obligation_type == "Payable"
            else obligation.receivable_account
        )
        rows = {row.account: row for row in je.accounts}
        if len(je.accounts) != 2 or set(rows) != {
            tax_account, self.adjustment_counterpart_account
        }:
            frappe.throw("The submitted adjustment has different ledger accounts.")
        amount = abs(variance)
        increase_control = variance > 0
        if obligation.obligation_type == "Receivable":
            increase_control = not increase_control
        control = rows[tax_account]
        counterpart = rows[self.adjustment_counterpart_account]
        if counterpart.cost_center != self.adjustment_cost_center:
            frappe.throw("The submitted adjustment has a different Cost Center.")
        debit = flt(control.debit_in_account_currency)
        credit = flt(control.credit_in_account_currency)
        other_debit = flt(counterpart.debit_in_account_currency)
        other_credit = flt(counterpart.credit_in_account_currency)
        expected = (0, amount, amount, 0) if increase_control else (
            amount, 0, 0, amount
        )
        if any(abs(a - b) > 0.01 for a, b in zip(
            (debit, credit, other_debit, other_credit), expected
        )):
            frappe.throw("The submitted Adjustment Journal Entry amount differs from Assessment variance.")

        movement_type = (
            "Adjustment Increase" if variance > 0 else "Adjustment Decrease"
        )
        if not frappe.db.exists("Fiscal Obligation Movement", {
            "parent": obligation.name,
            "voucher_type": "Journal Entry",
            "voucher_no": je.name,
            "movement_type": movement_type,
        }):
            frappe.throw(
                "The adjustment Journal Entry has not been captured on its "
                "Fiscal Obligation. Check Tax Account Configuration coverage."
            )

    def publish_operational_assessment(self):
        obligation = frappe.get_doc("Fiscal Obligation", self.fiscal_obligation)
        amount = flt(self.effective_assessed_amount)
        frappe.db.set_value(
            "Fiscal Obligation", obligation.name,
            {
                "amount_assessed": amount,
                "effective_obligation_amount": amount,
                "outstanding_amount": max(amount - flt(obligation.amount_paid), 0),
                "latest_tax_assessment": self.name,
            },
            update_modified=True,
        )


    def validate_decision_evidence(self):
        if self.is_operational_assessment():
            return

        """
        Accepted and Disputed are formal management decisions.
        Date, documentary evidence and comment are mandatory.
        """

        if self.assessment_decision not in (
            "Accepted",
            "Disputed",
        ):
            return

        missing = []

        if not self.get("decision_date"):
            missing.append("Decision Date")

        if not self.get("decision_document"):
            missing.append("Decision Document")

        if not self.get("decision_notes"):
            missing.append("Decision Comment")

        if missing:
            frappe.throw(
                "Assessment decision requires: "
                + ", ".join(missing)
            )

    def validate_decision(self):
        if self.is_operational_assessment():
            if self.assessment_decision != "Accepted":
                frappe.throw("Operational Assessment decision is system-set to Accepted.")
            if flt(self.amount_assessed) <= 0:
                frappe.throw("Operational Assessment needs a positive determined amount.")
            return

        """
        Progressive validation.

        Tax Assessment is a living case record. Fields remain
        available throughout the process, but become mandatory
        only when their business event occurs.
        """

        # ==================================================
        # UNIVERSAL ASSESSMENT EVIDENCE
        # ==================================================

        if not self.assessment_decision:
            frappe.throw(
                "Assessment Decision is mandatory."
            )

        if not self.get("assessment_reference"):
            frappe.throw(
                "Assessment Reference is mandatory."
            )

        if not self.get("attach"):
            frappe.throw(
                "Originating assessment document "
                "must be attached before saving."
            )

        if not self.get("responsible_user"):
            frappe.throw(
                "Responsible User is mandatory."
            )

        if not self.get("date_received"):
            frappe.throw(
                "Date Received is mandatory."
            )

        days = self.get(
            "legal_time_limit_days"
        )

        if days in (
            None,
            "",
        ):
            frappe.throw(
                "Legal Time Limit in Days is mandatory."
            )

        try:
            days = int(days)
        except (TypeError, ValueError):
            frappe.throw(
                "Legal Time Limit in Days must be "
                "a valid whole number."
            )

        if days < 0:
            frappe.throw(
                "Legal Time Limit in Days cannot be negative."
            )

        self.legal_time_limit_date = add_days(
            self.date_received,
            days,
        )

        # ==================================================
        # PENDING
        # ==================================================

        if self.assessment_decision == "Pending":
            if not self.action_due_date:
                frappe.throw(
                    "Further Action Due Date is mandatory "
                    "when Assessment Decision is Pending."
                )

            # Pending is intentionally incomplete.
            #
            # Do NOT require:
            # - Amount Assessed
            # - Acceptance Date
            # - Disputed Amount
            # - Agreement data
            # - RUPE / NL
            # - Provision
            # - Accounting
            return

        # ==================================================
        # ALL NON-PENDING DECISIONS REQUIRE AN ASSESSED AMOUNT
        # ==================================================

        raw_assessed = self.get(
            "amount_assessed"
        )

        if raw_assessed in (
            None,
            "",
        ):
            frappe.throw(
                "Amount Assessed is mandatory for "
                f"Assessment Decision {self.assessment_decision}."
            )

        if flt(raw_assessed) < 0:
            frappe.throw(
                "Amount Assessed cannot be negative."
            )

        # ==================================================
        # ACCEPTED
        # ==================================================

        if self.assessment_decision == "Accepted":
            if not self.get(
                "acceptance_date"
            ):
                frappe.throw(
                    "Acceptance Date is mandatory "
                    "when the assessment is Accepted."
                )

            return

        # ==================================================
        # DISPUTED
        # ==================================================

        if self.assessment_decision == "Disputed":
            disputed = self.get(
                "disputed_amount"
            )

            if disputed in (
                None,
                "",
            ):
                frappe.throw(
                    "Disputed Amount is mandatory "
                    "when the assessment is Disputed."
                )

            if flt(disputed) <= 0:
                frappe.throw(
                    "Disputed Amount must be greater than zero."
                )

            if flt(disputed) > flt(
                self.amount_assessed
            ):
                frappe.throw(
                    "Disputed Amount cannot exceed "
                    "Amount Assessed."
                )

            self.undisputed_amount = (
                flt(self.amount_assessed)
                - flt(disputed)
            )

            return

        # ==================================================
        # AGREEMENT
        # ==================================================

        if self.assessment_decision == "Agreement":
            required = {
                "Agreement Date":
                    self.get(
                        "agreement_date"
                    ),

                "Agreement Reference":
                    self.get(
                        "agreement_reference"
                    ),

                "Agreed Assessed Amount":
                    self.get(
                        "agreed_amount"
                    ),

                "Agreement Document":
                    self.get(
                        "agreement_document"
                    ),
            }

            missing = [
                label
                for label, value in required.items()
                if value in (
                    None,
                    "",
                )
            ]

            if missing:
                frappe.throw(
                    "Agreement requires: "
                    + ", ".join(missing)
                )

            if flt(
                self.agreed_amount
            ) <= 0:
                frappe.throw(
                    "Agreed Assessed Amount must "
                    "be greater than zero."
                )

            return

        frappe.throw(
            f"Invalid Assessment Decision: "
            f"{self.assessment_decision}"
        )


    def calculate_effective_amount(self):
        if self.is_operational_assessment():
            self.effective_assessed_amount = flt(self.amount_assessed)
            return
        if self.assessment_decision == "Agreement":
            if flt(self.agreed_amount):
                self.effective_assessed_amount = (
                    math.ceil(
                        flt(self.agreed_amount)
                    )
                )
            else:
                self.effective_assessed_amount = (
                    math.ceil(
                        flt(self.amount_assessed)
                    )
                )
        else:
            self.effective_assessed_amount = (
                math.ceil(
                    flt(self.amount_assessed)
                )
            )

    # ======================================================
    # EXISTING FISCAL OBLIGATION
    # ======================================================

    def load_fiscal_obligation_context(self):
        """
        Fiscal Obligation is authoritative for inherited
        assessment context.

        For assessments originating from an obligation,
        Company, Tax Type, period and currency cannot diverge
        from the source obligation.
        """

        if not self.fiscal_obligation:
            return

        obligation = frappe.get_doc(
            "Fiscal Obligation",
            self.fiscal_obligation,
        )

        # Only submitted obligations are valid sources.
        if obligation.docstatus != 1:
            frappe.throw(
                "Only a submitted Fiscal Obligation "
                "can originate a Tax Assessment."
            )

        self.statutory_payment_due_date = (
            get_statutory_payment_due_date(obligation)
        )

        # --------------------------------------------------
        # Resolve Tax Type from the obligation's tax code.
        # Tax Assessment.tax_type is a Link, therefore it
        # must contain the Tax Type document name.
        # --------------------------------------------------

        tax_type_name = None

        if obligation.tax_code:
            matches = frappe.get_all(
                "Tax Type",
                filters={
                    "company": obligation.company,
                    "tax_code": obligation.tax_code,
                    "active": 1,
                },
                pluck="name",
                limit=2,
            )

            if len(matches) > 1:
                frappe.throw(
                    "More than one active Tax Type exists "
                    f"for Tax Code {obligation.tax_code}. "
                    "Tax Type configuration must be unique."
                )

            if matches:
                tax_type_name = matches[0]

        # Defensive fallback for older obligation records.
        if (
            not tax_type_name
            and obligation.tax_name
        ):
            matches = frappe.get_all(
                "Tax Type",
                filters={
                    "company": obligation.company,
                    "tax_name": obligation.tax_name,
                    "active": 1,
                },
                pluck="name",
                limit=2,
            )

            if len(matches) > 1:
                frappe.throw(
                    "More than one active Tax Type exists "
                    f"for Tax Name {obligation.tax_name}."
                )

            if matches:
                tax_type_name = matches[0]

        if not tax_type_name:
            frappe.throw(
                "No active Tax Type could be resolved from "
                f"Fiscal Obligation {obligation.name} "
                f"(Tax Code: {obligation.tax_code or 'blank'})."
            )

        # --------------------------------------------------
        # HEREDITARY FIELDS
        # --------------------------------------------------

        self.company = obligation.company
        self.tax_type = tax_type_name
        self.fiscal_period_from = (
            obligation.fiscal_period_from
        )
        self.fiscal_period_to = (
            obligation.fiscal_period_to
        )

        if obligation.currency:
            self.currency = obligation.currency

        if self.is_operational_assessment():
            # The first saved Assessment preserves the original comparison
            # point. Its posted adjustment later changes the obligation's
            # current determination, not this Assessment variance.
            if self.is_new() or not flt(self.determined_amount):
                self.determined_amount = flt(obligation.amount_determined)
            self.assessment_decision = "Accepted"
            self.assessment_type = "Self-Determination"
            self.overdue_obligation = 0
            self.overdue_principal = 0
            self.overdue_tax_account = None
            return

        if obligation.obligation_origin == "Tax Notification":
            self.overdue_obligation = 0
            self.overdue_principal = 0
            self.overdue_tax_account = None
            return

        # OVERDUE_OBLIGATION_SNAPSHOT_V1
        # --------------------------------------------------
        # OVERDUE EXISTING OBLIGATION
        # --------------------------------------------------
        #
        # The original tax principal already exists in the
        # accounting ledger and remains authoritative.
        #
        # Tax Assessment only records that the existing
        # Fiscal Obligation has entered the authority /
        # overdue process.
        #
        is_overdue = bool(
            obligation.get("is_overdue")
            or obligation.get("deadline_status")
            == "Overdue"
        )

        self.overdue_obligation = (
            1 if is_overdue else 0
        )

        if is_overdue:
            principal = flt(
                obligation.get(
                    "outstanding_amount"
                )
                or 0
            )

            if principal <= 0:
                frappe.throw(
                    "An overdue Fiscal Obligation must "
                    "have an outstanding principal."
                )

            original_account = (
                obligation.get(
                    "liability_account"
                )
                or obligation.get(
                    "receivable_account"
                )
            )

            if not original_account:
                frappe.throw(
                    "The overdue Fiscal Obligation does "
                    "not have an original Tax Account."
                )

            self.overdue_principal = principal
            self.overdue_tax_account = (
                original_account
            )

            # The Assessment carries the existing principal
            # through the decision workflow.
            #
            # Penalty is NOT determined here. It belongs to
            # the formal liquidation / Tax Settlement stage.
            if self.get("amount_assessed") in (
                None,
                "",
                0,
            ):
                self.amount_assessed = principal

        else:
            self.overdue_principal = 0
            self.overdue_tax_account = None


    # ======================================================
    # DIRECT ASSESSMENT -> FISCAL OBLIGATION
    # ======================================================

    def create_fiscal_obligation_from_direct_assessment(self):
        """
        Create the Fiscal Obligation for a direct authority
        assessment.

        The Fiscal Obligation immediately records the full
        authority-assessed exposure.

        This does NOT make the amount payable. Formal
        collectibility is created later through Tax Settlement.
        """

        tax_type = frappe.get_doc(
            "Tax Type",
            self.tax_type,
        )

        assessed = flt(
            self.effective_assessed_amount
            or self.amount_assessed
            or 0
        )

        obligation = frappe.new_doc(
            "Fiscal Obligation"
        )

        # --------------------------------------------------
        # CONTROL / RESPONSIBILITY
        # --------------------------------------------------

        obligation.responsible_user = (
            self.responsible_user
        )

        obligation.date_received = (
            self.date_received
        )

        # --------------------------------------------------
        # ORIGIN / TRACEABILITY
        # --------------------------------------------------

        obligation.obligation_origin = (
            "Direct Assessment"
        )

        if obligation.meta.has_field(
            "source_tax_assessment"
        ):
            obligation.source_tax_assessment = (
                self.name
            )

        # --------------------------------------------------
        # MASTER CONTEXT
        # --------------------------------------------------

        obligation.company = self.company
        obligation.tax_type = self.tax_type

        if obligation.meta.has_field(
            "tax_name"
        ):
            obligation.tax_name = (
                tax_type.tax_name
            )

        obligation.obligation_type = (
            tax_type.obligation_type
        )

        obligation.fiscal_period_from = (
            self.fiscal_period_from
        )

        obligation.fiscal_period_to = (
            self.fiscal_period_to
        )

        obligation.currency = (
            self.currency
            or frappe.db.get_value(
                "Company",
                self.company,
                "default_currency",
            )
        )

        # --------------------------------------------------
        # ACCOUNT CONFIGURATION
        # --------------------------------------------------

        tax_account = self.get_tax_account()

        if (
            tax_type.obligation_type
            == "Payable"
        ):
            obligation.liability_account = (
                tax_account
            )

        elif (
            tax_type.obligation_type
            == "Receivable"
        ):
            obligation.receivable_account = (
                tax_account
            )

        # --------------------------------------------------
        # CREATE UNDERLYING EXPOSURE RECORD
        #
        # Some Fiscal Obligation assessment fields may only
        # be populated after submission, so create and submit
        # the control record first.
        # --------------------------------------------------

        obligation.status = "Determined"

        obligation.insert(
            ignore_permissions=True
        )

        obligation.submit()

        # --------------------------------------------------
        # REGISTER AUTHORITY EXPOSURE
        # --------------------------------------------------

        values = {
            "amount_assessed":
                assessed,

            "assessment_variance":
                flt(self.assessment_variance),

            "effective_obligation_amount":
                assessed,

            "amount_paid":
                0,

            "outstanding_amount":
                assessed,

            "status":
                "Under Review",
        }

        if obligation.meta.has_field(
            "latest_tax_assessment"
        ):
            values["latest_tax_assessment"] = (
                self.name
            )

        if obligation.meta.has_field(
            "assessment_document"
        ):
            values["assessment_document"] = (
                self.get("attach")
            )

        frappe.db.set_value(
            "Fiscal Obligation",
            obligation.name,
            values,
            update_modified=False,
        )

        obligation.reload()

        return obligation.name


    # ======================================================
    # KEEP OBLIGATION FOLLOWING ASSESSMENT
    # ======================================================

    def sync_fiscal_obligation(self):
        if self.is_operational_assessment() and self.fiscal_obligation:
            # Keep the GL determination authoritative, including on
            # subsequent edits of the Assessment.
            frappe.db.set_value(
                "Fiscal Obligation", self.fiscal_obligation,
                "latest_tax_assessment", self.name,
                update_modified=True,
            )
            return

        # OVERDUE_OBLIGATION_SYNC_GUARD_V1
        # --------------------------------------------------
        # Never replace the original principal/accounting
        # state of an overdue existing Fiscal Obligation.
        # --------------------------------------------------
        if (
            self.get("overdue_obligation")
            and self.fiscal_obligation
        ):
            obligation = frappe.get_doc(
                "Fiscal Obligation",
                self.fiscal_obligation,
            )

            values = {}

            if obligation.meta.has_field(
                "latest_tax_assessment"
            ):
                values[
                    "latest_tax_assessment"
                ] = self.name

            if (
                obligation.meta.has_field(
                    "assessment_document"
                )
                and self.get("attach")
            ):
                values[
                    "assessment_document"
                ] = self.get("attach")

            if values:
                frappe.db.set_value(
                    "Fiscal Obligation",
                    obligation.name,
                    values,
                    update_modified=True,
                )

            # Critical invariant:
            #
            # Do NOT change:
            # - amount_determined
            # - effective_obligation_amount
            # - outstanding_amount
            # - liability_account / receivable_account
            # - amount_assessed on the Fiscal Obligation
            return

        """
        Synchronise the authority assessment with its
        Fiscal Obligation.

        Fiscal Obligation is the exposure register.

        No filing prerequisite applies to an authority
        assessment. The assessment becomes visible as tax
        exposure immediately.

        Acceptance does NOT make the obligation directly
        payable. Formal collectibility belongs to
        Tax Settlement.

        Dispute does NOT remove the exposure. The exposure
        remains registered while Tax Litigation manages the
        procedural dispute.
        """

        if not self.fiscal_obligation:
            return

        obligation = frappe.get_doc(
            "Fiscal Obligation",
            self.fiscal_obligation,
        )

        if obligation.status == "Superseded":
            return

        amount_raw = self.get(
            "effective_assessed_amount"
        )

        if amount_raw in (
            None,
            "",
        ):
            amount_raw = self.get(
                "amount_assessed"
            )

        if amount_raw in (
            None,
            "",
        ):
            return

        amount = flt(
            amount_raw
        )

        is_notification_assessment = (
            self.assessment_origin == "From Tax Notification"
        )
        if is_notification_assessment:
            net_amount = self.get("net_payable_amount")
            credit_amount = flt(self.get("tax_credit_applied"))
            if net_amount in (None, "") or (
                flt(net_amount) == 0 and credit_amount == 0 and amount > 0
            ):
                net_amount = amount
            amount = flt(net_amount)

        values = {
            "amount_assessed":
                amount,

            "assessment_variance":
                flt(self.assessment_variance),
        }

        # --------------------------------------------------
        # LIFECYCLE OWNERSHIP
        # --------------------------------------------------
        #
        # Before formal liquidation, Assessment owns the
        # exposure amount/status reflected on the Fiscal
        # Obligation.
        #
        # Once a submitted Settlement exists, collectibility,
        # payment and outstanding balance belong to the
        # Settlement / Collection Reference / Payment Control
        # layer. Assessment must not move the obligation
        # backwards in its lifecycle.
        settlement_exists = frappe.db.exists(
            "Tax Settlement",
            {
                "fiscal_obligation":
                    obligation.name,
                "docstatus": 1,
                "status": ["!=", "Cancelled"],
            },
        )

        if not settlement_exists:
            amount_paid = flt(
                obligation.get("amount_paid")
                or 0
            )

            values.update(
                {
                    "effective_obligation_amount":
                        amount,

                    "outstanding_amount":
                        max(
                            amount - amount_paid,
                            0,
                        ),

                    "status":
                        self.get_obligation_status(),
                }
            )

        if obligation.meta.has_field("under_dispute"):
            values["under_dispute"] = 1 if self.assessment_decision == "Disputed" else 0

        # --------------------------------------------------
        # TRACEABILITY
        # --------------------------------------------------

        if obligation.meta.has_field(
            "latest_tax_assessment"
        ):
            values["latest_tax_assessment"] = (
                self.name
            )

        if obligation.meta.has_field(
            "assessment_document"
        ):
            values["assessment_document"] = (
                self.get("attach")
            )

        frappe.db.set_value(
            "Fiscal Obligation",
            obligation.name,
            values,
            update_modified=True,
        )


    def get_obligation_status(self):
        """
        Derive exposure status from the Assessment decision.

        Accepted/Agreement means the exposure is confirmed,
        but it is not yet a payable. Tax Settlement creates
        the collectible/payment layer.
        """

        decision = (
            self.assessment_decision
            or "Pending"
        )

        if decision == "Pending":
            return "Under Review"

        if decision == "Disputed":
            return "Under Review"

        if decision in (
            "Accepted",
            "Agreement",
        ):
            return "Under Review"

        if decision == "Cancelled by AGT":
            return "Closed"

        return "Under Review"


    # ======================================================
    # ACCOUNT CONFIGURATION
    # ======================================================

    def get_tax_account(self):
        rows = frappe.get_all(
            "Tax Account Configuration",
            filters={
                "company": self.company,
                "tax_type": self.tax_type,
                "active": 1,
                "docstatus": 1,
            },
            fields=[
                "account",
                "effective_from",
            ],
            order_by="effective_from desc",
            limit=1,
        )

        if not rows:
            frappe.throw(
                "No active Tax Account Configuration "
                "exists for this Company and Tax Type."
            )

        return rows[0].account

    # ======================================================
    # PERMANENCE
    # ======================================================

    def before_cancel(self):
        frappe.throw(
            "Tax Assessments cannot be cancelled."
        )

    def on_trash(self):
        frappe.throw(
            "Tax Assessments cannot be deleted."
        )


@frappe.whitelist()
def submit_assessment_adjustment(assessment_name):
    """Submit the linked draft JE and verify its capture on the obligation."""
    assessment = frappe.get_doc("Tax Assessment", assessment_name)
    assessment.check_permission("write")
    if assessment.docstatus != 0 or not assessment.is_operational_assessment():
        frappe.throw("Only a draft operational Assessment can submit its adjustment.")
    if abs(flt(assessment.assessment_variance)) <= 0.01:
        frappe.throw("This Assessment has no adjustment to submit.")
    if not assessment.adjustment_journal_entry:
        frappe.throw("Save the Assessment to prepare its Adjustment Journal Entry.")

    je = frappe.get_doc("Journal Entry", assessment.adjustment_journal_entry)
    je.check_permission("submit")
    if je.docstatus != 0 or je.company != assessment.company:
        frappe.throw("The linked Adjustment Journal Entry must be a draft for this Company.")

    je.submit()
    # The Journal Entry on_submit hook must attach its tax-account GL row.
    # A failure rolls back the JE submission in the same request.
    assessment.validate_submitted_operational_adjustment()
    return je.name


@frappe.whitelist()
def get_fiscal_obligation_context(fiscal_obligation):
    obligation = frappe.get_doc(
        "Fiscal Obligation",
        fiscal_obligation,
    )

    if obligation.docstatus == 2 and obligation.source_tax_notification:
        # A cancelled notification obligation can remain on the submitted
        # Tax Notification. Rebind the notification to an active replacement
        # before opening the new assessment form.
        notification = frappe.get_doc(
            "Tax Notification", obligation.source_tax_notification
        )
        if notification.docstatus != 1:
            frappe.throw(
                "The Tax Notification linked to this cancelled obligation "
                "is not submitted. Reopen the notification and correct its "
                "Fiscal Obligation link first."
            )

        replacement = frappe.db.get_value(
            "Fiscal Obligation",
            {
                "source_tax_notification": notification.name,
                "docstatus": ["!=", 2],
            },
            "name",
        )
        if replacement:
            obligation = frappe.get_doc("Fiscal Obligation", replacement)
        else:
            tax_type_doc = frappe.get_doc("Tax Type", notification.tax_type)
            related = notification.get("related_operational_obligation")
            if related and frappe.db.get_value(
                "Fiscal Obligation", related, "docstatus"
            ) == 2:
                related = None

            obligation = frappe.new_doc("Fiscal Obligation")
            obligation.obligation_origin = "Tax Notification"
            obligation.source_tax_notification = notification.name
            obligation.source_document_type = "Tax Notification"
            obligation.source_document = notification.name
            obligation.related_operational_obligation = related
            obligation.source_module = "Manual"
            obligation.company = notification.company
            obligation.tax_type = notification.tax_type
            obligation.tax_name = tax_type_doc.tax_name
            obligation.obligation_type = tax_type_doc.obligation_type
            obligation.responsible_user = notification.responsible_user
            obligation.fiscal_period_from = notification.fiscal_period_from
            obligation.fiscal_period_to = notification.fiscal_period_to
            obligation.currency = frappe.db.get_value(
                "Company", notification.company, "default_currency"
            )
            obligation.amount_assessed = notification.claimed_amount
            obligation.assessment_document = notification.notification_document
            obligation.status = "Under Review"
            obligation.insert(ignore_permissions=True)
            obligation.flags.ignore_permissions = True
            obligation.submit()

        notification.db_set(
            "fiscal_obligation", obligation.name, update_modified=False
        )

    if obligation.docstatus != 1:
        frappe.throw(
            "Only a submitted Fiscal Obligation "
            "can originate a Tax Assessment."
        )

    tax_type = None

    if obligation.tax_code:
        tax_type = frappe.db.get_value(
            "Tax Type",
            {
                "company": obligation.company,
                "tax_code": obligation.tax_code,
                "active": 1,
            },
            "name",
        )

    if not tax_type and obligation.tax_name:
        tax_type = frappe.db.get_value(
            "Tax Type",
            {
                "company": obligation.company,
                "tax_name": obligation.tax_name,
                "active": 1,
            },
            "name",
        )

    if not tax_type:
        frappe.throw(
            "No active Tax Type is configured for "
            f"{obligation.tax_name or obligation.tax_code}."
        )

    return {
        "company": obligation.company,
        "tax_type": tax_type,
        "fiscal_period_from":
            obligation.fiscal_period_from,
        "fiscal_period_to":
            obligation.fiscal_period_to,
        "currency": obligation.currency,
    }


@frappe.whitelist()
@frappe.whitelist()
def get_previous_assessment(
    fiscal_obligation,
    current_assessment=None,
):
    filters = {
        "fiscal_obligation":
            fiscal_obligation,
    }

    if current_assessment:
        filters["name"] = [
            "!=",
            current_assessment,
        ]

    rows = frappe.get_all(
        "Tax Assessment",
        filters=filters,
        fields=[
            "name",
            "amount_assessed",
            "effective_assessed_amount",
        ],
        order_by="creation desc",
        limit=1,
    )

    if rows:
        previous = rows[0]

        previous_amount = flt(
            previous.effective_assessed_amount
            or previous.amount_assessed
            or 0
        )

        return {
            "previous_assessment":
                previous.name,
            "previous_assessed_amount":
                previous_amount,
        }

    meta = frappe.get_meta(
        "Fiscal Obligation"
    )

    if meta.has_field(
        "source_tax_assessment"
    ):
        source = frappe.db.get_value(
            "Fiscal Obligation",
            fiscal_obligation,
            "source_tax_assessment",
        )

        if (
            source
            and source != current_assessment
        ):
            previous = frappe.get_doc(
                "Tax Assessment",
                source,
            )

            previous_amount = flt(
                previous.effective_assessed_amount
                or previous.amount_assessed
                or 0
            )

            return {
                "previous_assessment":
                    previous.name,
                "previous_assessed_amount":
                    previous_amount,
            }

    return None


@frappe.whitelist()
def get_new_assessment_from_obligation(
    fiscal_obligation,
):
    """
    Prepare a new authority Tax Assessment from an existing
    Fiscal Obligation.

    The first assessment is a Notification.

    Amount Assessed is deliberately NOT inherited from filing
    or determination data. It must represent the amount stated
    by the Tax Authority.
    """

    obligation = frappe.get_doc(
        "Fiscal Obligation",
        fiscal_obligation,
    )

    if obligation.docstatus == 2 and obligation.source_tax_notification:
        # A cancelled notification obligation can remain on the submitted
        # Tax Notification. Rebind the notification to an active replacement
        # before opening the new assessment form.
        notification = frappe.get_doc(
            "Tax Notification", obligation.source_tax_notification
        )
        if notification.docstatus != 1:
            frappe.throw(
                "The Tax Notification linked to this cancelled obligation "
                "is not submitted. Reopen the notification and correct its "
                "Fiscal Obligation link first."
            )

        replacement = frappe.db.get_value(
            "Fiscal Obligation",
            {
                "source_tax_notification": notification.name,
                "docstatus": ["!=", 2],
            },
            "name",
        )
        if replacement:
            obligation = frappe.get_doc("Fiscal Obligation", replacement)
        else:
            tax_type_doc = frappe.get_doc("Tax Type", notification.tax_type)
            related = notification.get("related_operational_obligation")
            if related and frappe.db.get_value(
                "Fiscal Obligation", related, "docstatus"
            ) == 2:
                related = None

            obligation = frappe.new_doc("Fiscal Obligation")
            obligation.obligation_origin = "Tax Notification"
            obligation.source_tax_notification = notification.name
            obligation.source_document_type = "Tax Notification"
            obligation.source_document = notification.name
            obligation.related_operational_obligation = related
            obligation.source_module = "Manual"
            obligation.company = notification.company
            obligation.tax_type = notification.tax_type
            obligation.tax_name = tax_type_doc.tax_name
            obligation.obligation_type = tax_type_doc.obligation_type
            obligation.responsible_user = notification.responsible_user
            obligation.fiscal_period_from = notification.fiscal_period_from
            obligation.fiscal_period_to = notification.fiscal_period_to
            obligation.currency = frappe.db.get_value(
                "Company", notification.company, "default_currency"
            )
            obligation.amount_assessed = notification.claimed_amount
            obligation.assessment_document = notification.notification_document
            obligation.status = "Under Review"
            obligation.insert(ignore_permissions=True)
            obligation.flags.ignore_permissions = True
            obligation.submit()

        notification.db_set(
            "fiscal_obligation", obligation.name, update_modified=False
        )

    if obligation.docstatus != 1:
        frappe.throw(
            "Only a submitted Fiscal Obligation "
            "can originate a Tax Assessment."
        )

    if obligation.obligation_origin not in ("Operational", "Tax Notification"):
        frappe.throw("Open the existing authority Assessment for this obligation.")

    # The first assessment in this lineage must be unique.
    existing = frappe.db.get_value(
        "Tax Assessment",
        {
            "fiscal_obligation":
                obligation.name,
        },
        "name",
        order_by="creation desc",
    )

    if existing:
        frappe.throw(
            f"Fiscal Obligation {obligation.name} "
            f"already has Tax Assessment {existing}. "
            "Create any Reissue, Additional or Correction "
            "from that Tax Assessment."
        )

    notification = None
    if obligation.obligation_origin == "Tax Notification":
        if not obligation.source_tax_notification:
            frappe.throw("The authority obligation has no Tax Notification.")
        notification = frappe.get_doc(
            "Tax Notification", obligation.source_tax_notification
        )
        if notification.docstatus != 1 or notification.fiscal_obligation != obligation.name:
            frappe.throw("Submit the linked Tax Notification before assessment.")

    # --------------------------------------------------
    # RESOLVE TAX TYPE
    # --------------------------------------------------

    tax_type = None

    if obligation.tax_code:
        tax_type = frappe.db.get_value(
            "Tax Type",
            {
                "company": obligation.company,
                "tax_code":
                    obligation.tax_code,
                "active": 1,
            },
            "name",
        )

    if (
        not tax_type
        and obligation.tax_name
    ):
        tax_type = frappe.db.get_value(
            "Tax Type",
            {
                "company": obligation.company,
                "tax_name":
                    obligation.tax_name,
                "active": 1,
            },
            "name",
        )

    if not tax_type:
        frappe.throw(
            "No active Tax Type could be resolved "
            f"for Fiscal Obligation {obligation.name}."
        )

    return {
        "assessment_origin":
            ("From Tax Notification" if notification
             else "From Fiscal Obligation"),

        "fiscal_obligation":
            obligation.name,

        "company":
            obligation.company,

        "tax_type":
            tax_type,

        "fiscal_period_from":
            obligation.fiscal_period_from,

        "fiscal_period_to":
            obligation.fiscal_period_to,

        "currency":
            obligation.currency,

        "statutory_payment_due_date":
            get_statutory_payment_due_date(obligation),

        "assessment_type":
            "Notification" if notification else "Self-Determination",

        # Authority amount must come from the
        # assessment/notification itself.
        "amount_assessed":
            (notification.claimed_amount if notification
             else obligation.amount_determined),

        "assessment_decision":
            "Pending" if notification else "Accepted",

        "assessment_reference":
            notification.notification_reference if notification else None,

        "attach":
            notification.notification_document if notification else None,

        "date_received":
            notification.notification_date if notification else None,

        "responsible_user":
            notification.responsible_user if notification else None,

        "previous_assessment":
            None,

        "previous_assessed_amount":
            0,
    }



# ==========================================================
# ASSESSMENT ACCOUNTING RECOGNITION
# ==========================================================

def _ensure_assessment_recognition_journal_entry(
    assessment,
    posting_date,
):
    # OVERDUE_RECOGNITION_GUARD_V1
    #
    # An overdue existing Fiscal Obligation already has
    # its original tax principal recognised in the GL.
    #
    # Assessment of that overdue obligation must NOT:
    #   - recognise the principal again;
    #   - create a tax provision for the principal;
    #   - reclassify the original tax liability.
    #
    # Incremental penalty accounting belongs to the
    # subsequent Tax Settlement / formal liquidation.
    if assessment.get("overdue_obligation"):
        return None
    """
    Recognition occurs when an authority assessment is formally
    accepted by the company.

        DR Tax Penalty Cost Account
        CR Tax Provision Account

    Receipt of the AGT assessment itself creates exposure only.
    Settlement / NL later transfers provision into settlement
    control.
    """

    if assessment.get(
        "recognition_journal_entry"
    ):
        existing = frappe.get_doc(
            "Journal Entry",
            assessment.recognition_journal_entry,
        )

        if existing.docstatus != 1:
            frappe.throw(
                "Existing Recognition Journal Entry "
                "must be submitted."
            )

        return existing.name

    is_notification_assessment = (
        assessment.assessment_origin == "From Tax Notification"
    )
    amount = flt(
        assessment.get("amount_assessed")
        if is_notification_assessment
        else (
            assessment.get("effective_assessed_amount")
            or assessment.get("amount_assessed")
            or 0
        )
    )
    credit_amount = (
        flt(assessment.get("tax_credit_applied"))
        if is_notification_assessment else 0
    )
    payable_amount = (
        flt(assessment.get("net_payable_amount"))
        if is_notification_assessment else amount
    )
    if is_notification_assessment and abs(amount - credit_amount - payable_amount) > 0.01:
        frappe.throw(
            "Accepted notification accounting must balance: Amount Assessed "
            "must equal Tax Credit Applied plus Balance."
        )

    if amount <= 0:
        frappe.throw(
            "Accepted Assessment requires a positive "
            "effective assessed amount."
        )

    if not assessment.fiscal_obligation:
        frappe.throw(
            "Accepted Assessment requires a linked "
            "Fiscal Obligation."
        )

    obligation = frappe.get_doc(
        "Fiscal Obligation",
        assessment.fiscal_obligation,
    )

    # ------------------------------------------------------
    # Company-specific Tax Penalty Settings
    # ------------------------------------------------------

    settings_names = frappe.get_all(
        "Tax Penalty Settings",
        filters={
            "company": assessment.company,
            "active": 1,
        },
        pluck="name",
    )

    if not settings_names:
        frappe.throw(
            "No active Tax Penalty Settings found for "
            f"{assessment.company}."
        )

    if len(settings_names) > 1:
        frappe.throw(
            "More than one active Tax Penalty Settings "
            f"record exists for {assessment.company}."
        )

    settings = frappe.get_doc(
        "Tax Penalty Settings",
        settings_names[0],
    )

    cost_account = settings.get(
        "tax_penalty_cost_account"
    )

    # Future fieldname first; current fieldname retained
    # until the controlled semantic rename is performed.
    provision_account = (
        settings.get(
            "tax_provision_account"
        )
        or settings.get(
            "tax_litigation_provision_account"
        )
    )

    if not cost_account:
        frappe.throw(
            "Tax Penalty Cost Account is not configured "
            "in Tax Penalty Settings."
        )

    if not provision_account:
        frappe.throw(
            "Tax Provision Account is not configured "
            "in Tax Penalty Settings."
        )

    if cost_account == provision_account:
        frappe.throw(
            "Tax Penalty Cost Account and Tax Provision "
            "Account cannot be the same account."
        )

    # ------------------------------------------------------
    # Validate account ownership
    # ------------------------------------------------------

    credit_account = (
        assessment.get("tax_credit_account")
        if is_notification_assessment and credit_amount else None
    )
    account_pairs = [
        (cost_account, "Tax Penalty Cost Account"),
        (provision_account, "Tax Provision Account"),
    ]
    if credit_account:
        account_pairs.append((credit_account, "Tax Credit Account"))

    for account_name, label in account_pairs:
        values = frappe.db.get_value(
            "Account",
            account_name,
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

        if company != assessment.company:
            frappe.throw(
                f"{label} must belong to "
                f"{assessment.company}."
            )

        if is_group:
            frappe.throw(
                f"{label} must be a ledger account."
            )

    if credit_account and credit_account in (cost_account, provision_account):
        frappe.throw("Tax Credit Account must differ from the expense and provision accounts.")

    # ------------------------------------------------------
    # Recognition JE
    # ------------------------------------------------------

    je = frappe.new_doc(
        "Journal Entry"
    )

    je.company = assessment.company
    je.posting_date = posting_date

    je.user_remark = (
        f"Recognition of accepted Tax Assessment "
        f"{assessment.name}"
        f" / Fiscal Obligation "
        f"{assessment.fiscal_obligation}"
    )

    cost_row = je.append(
        "accounts",
        {},
    )

    cost_row.account = cost_account
    cost_row.debit_in_account_currency = amount
    cost_row.credit_in_account_currency = 0

    if credit_amount:
        credit_row = je.append("accounts", {})
        credit_row.account = credit_account
        credit_row.debit_in_account_currency = 0
        credit_row.credit_in_account_currency = credit_amount

    if payable_amount:
        provision_row = je.append("accounts", {})
        provision_row.account = provision_account
        provision_row.debit_in_account_currency = 0
        provision_row.credit_in_account_currency = payable_amount

    je.insert(
        ignore_permissions=True
    )

    je.submit()

    assessment.db_set(
        "recognition_journal_entry",
        je.name,
        update_modified=False,
    )

    assessment.recognition_journal_entry = (
        je.name
    )

    return je.name


# ==========================================================
# ASSESSMENT DECISION TRANSITION API
# ==========================================================

@frappe.whitelist()
def transition_assessment(
    assessment_name,
    new_decision,
    decision_date=None,
    reference=None,
    document=None,
    notes=None,
):
    """
    Controlled transition gateway for Tax Assessment decisions.

    The Tax Assessment remains the authority-claim record.
    Fiscal Obligation remains the universal exposure register.

    Accepted:
        confirms the assessment decision but does not make
        the exposure directly payable. Tax Settlement handles
        formal liquidation.

    Disputed:
        preserves the exposure and opens / returns the related
        Tax Litigation workflow.

    Pending:
        keeps the assessment under review.

    Agreement:
        remains governed by the existing Assessment validation.
    """

    allowed = (
        "Pending",
        "Accepted",
        "Disputed",
        "Agreement",
        "Cancelled by AGT",
    )

    if new_decision in (
        "Accepted",
        "Disputed",
    ):
        missing = []

        if not decision_date:
            missing.append("Decision Date")

        if not document:
            missing.append("Document")

        if not notes:
            missing.append("Comment / Notes")

        if missing:
            frappe.throw(
                "Assessment decision requires: "
                + ", ".join(missing)
            )

    if new_decision not in allowed:
        frappe.throw(
            f"Unsupported Assessment Decision: "
            f"{new_decision}"
        )

    assessment = frappe.get_doc(
        "Tax Assessment",
        assessment_name,
    )

    if assessment.is_operational_assessment():
        frappe.throw(
            "Self-Determination does not use an authority decision. "
            "Review the Assessment and submit it directly."
        )

    if assessment.docstatus != 0:
        frappe.throw(
            "Assessment decisions can only be recorded "
            "while the Tax Assessment is in Draft."
        )

    if assessment.docstatus == 2:
        frappe.throw(
            "Cancelled Tax Assessments cannot be "
            "transitioned."
        )

    if assessment.get("superseded_by"):
        frappe.throw(
            f"Tax Assessment {assessment.name} has "
            f"been superseded by "
            f"{assessment.superseded_by}."
        )

    old_decision = (
        assessment.assessment_decision
        or "Pending"
    )

    # ------------------------------------------------------
    # Apply decision-specific transition data
    # ------------------------------------------------------

    assessment.assessment_decision = new_decision

    # ------------------------------------------------------
    # FORMAL DECISION EVIDENCE
    # ------------------------------------------------------
    # These fields must be populated BEFORE assessment.save()
    # because normal validation requires them for Accepted
    # and Disputed decisions.
    # ------------------------------------------------------

    assessment.decision_date = decision_date
    assessment.decision_reference = reference
    assessment.decision_document = document
    assessment.decision_notes = notes

    if new_decision == "Accepted":
        if not decision_date:
            frappe.throw(
                "Decision Date is required when "
                "accepting an Assessment."
            )

        assessment.acceptance_date = decision_date

    elif new_decision == "Disputed":
        if not decision_date:
            frappe.throw(
                "Decision Date is required when "
                "disputing an Assessment."
            )

        # The amount itself remains governed by the normal
        # Assessment validation. The user must have entered
        # Disputed Amount before executing this transition.

    elif new_decision == "Agreement":
        if decision_date and assessment.meta.has_field(
            "agreement_date"
        ):
            assessment.agreement_date = decision_date

        if (
            reference
            and assessment.meta.has_field(
                "agreement_reference"
            )
        ):
            assessment.agreement_reference = reference

        if (
            document
            and assessment.meta.has_field(
                "agreement_document"
            )
        ):
            assessment.agreement_document = document

    elif new_decision == "Cancelled by AGT":
        # Decision evidence remains in the assessment record.
        pass

    # ------------------------------------------------------
    # Generic decision evidence
    # ------------------------------------------------------

    if (
        reference
        and assessment.meta.has_field(
            "decision_reference"
        )
    ):
        assessment.decision_reference = reference

    if (
        document
        and assessment.meta.has_field(
            "decision_document"
        )
    ):
        assessment.decision_document = document

    if (
        notes
        and assessment.meta.has_field(
            "decision_notes"
        )
    ):
        assessment.decision_notes = notes

    # ------------------------------------------------------
    # Save through normal controller validation.
    # This runs validate_decision(), effective amount logic,
    # and normal Tax Assessment business rules.
    # ------------------------------------------------------

    assessment.flags.ignore_validate_update_after_submit = True
    assessment.flags.allow_superseded_update = False

    assessment.save(
        ignore_permissions=False
    )

    # ------------------------------------------------------
    # ACCEPTED -> IMMEDIATE ACCOUNTING RECOGNITION
    # ------------------------------------------------------
    #
    # The approved decision popup is the recognition event.
    # Submit subsequently freezes the Tax Assessment.
    #
    if new_decision == "Accepted":
        _ensure_assessment_recognition_journal_entry(
            assessment=assessment,
            posting_date=decision_date,
        )

    # Disputed intentionally creates no accounting entry.
    # Its accounting treatment belongs to the later
    # litigation / provision process.

    # ------------------------------------------------------
    # Decision audit log
    # ------------------------------------------------------

    log_name = None

    if frappe.db.exists(
        "DocType",
        "Tax Assessment Decision Log",
    ):
        log = frappe.new_doc(
            "Tax Assessment Decision Log"
        )

        # --------------------------------------------------
        # SYSTEM AUDIT METADATA
        # --------------------------------------------------

        existing_logs = frappe.get_all(
            "Tax Assessment Decision Log",
            filters={
                "tax_assessment":
                    assessment.name
            },
            fields=["name"],
        )

        next_sequence = len(existing_logs) + 1

        if log.meta.has_field(
            "sequence"
        ):
            log.sequence = next_sequence

        if log.meta.has_field(
            "recorded_at"
        ):
            log.recorded_at = frappe.utils.now_datetime()

        if log.meta.has_field(
            "recorded_by"
        ):
            log.recorded_by = frappe.session.user

        # --------------------------------------------------
        # DECISION DATA
        # --------------------------------------------------

        if log.meta.has_field(
            "tax_assessment"
        ):
            log.tax_assessment = assessment.name

        if log.meta.has_field(
            "previous_decision"
        ):
            log.previous_decision = old_decision

        if log.meta.has_field(
            "new_decision"
        ):
            log.new_decision = new_decision

        if log.meta.has_field(
            "decision_date"
        ):
            log.decision_date = decision_date

        if log.meta.has_field(
            "reference"
        ):
            log.reference = reference

        if log.meta.has_field(
            "document"
        ):
            log.document = document

        if log.meta.has_field(
            "notes"
        ):
            log.notes = notes

        if log.meta.has_field(
            "changed_by"
        ):
            log.changed_by = frappe.session.user

        log.insert(
            ignore_permissions=True
        )

        log_name = log.name

    # ------------------------------------------------------
    # DISPUTE -> TAX LITIGATION
    # ------------------------------------------------------

    litigation_name = None

    if new_decision == "Disputed":
        litigation_name = frappe.db.get_value(
            "Tax Litigation",
            {
                "tax_assessment":
                    assessment.name,
                "docstatus":
                    ["!=", 2],
            },
            "name",
        )

        if not litigation_name:
            litigation = frappe.new_doc(
                "Tax Litigation"
            )

            if litigation.meta.has_field(
                "tax_assessment"
            ):
                litigation.tax_assessment = (
                    assessment.name
                )

            if litigation.meta.has_field(
                "fiscal_obligation"
            ):
                litigation.fiscal_obligation = (
                    assessment.fiscal_obligation
                )

            if litigation.meta.has_field(
                "company"
            ):
                litigation.company = (
                    assessment.company
                )

            if litigation.meta.has_field(
                "tax_type"
            ):
                litigation.tax_type = (
                    assessment.tax_type
                )

            if litigation.meta.has_field(
                "tax_authority"
            ):
                litigation.tax_authority = (
                    assessment.tax_authority
                )

            if litigation.meta.has_field(
                "disputed_amount"
            ):
                litigation.disputed_amount = flt(
                    assessment.disputed_amount
                )

            litigation.current_stage = "DAP"
            litigation.status = "Open"
            litigation.provision_decision = "Pending"
            litigation.provision_review_status = "Pending"
            litigation.insert(
                ignore_permissions=True
            )

            dap = frappe.new_doc("Tax Litigation DAP")
            dap.tax_litigation = litigation.name
            dap.tax_assessment = assessment.name
            dap.company = assessment.company
            dap.tax_type = assessment.tax_type
            dap.fiscal_obligation = assessment.fiscal_obligation
            dap.company_submission_due_date = assessment.legal_time_limit_date
            dap.insert(ignore_permissions=True)
            litigation.db_set("current_phase_doctype", dap.doctype, update_modified=False)
            litigation.db_set("current_phase", dap.name, update_modified=False)

            litigation_name = litigation.name

        dap_name = frappe.db.get_value(
            "Tax Litigation DAP",
            {"tax_litigation": litigation_name, "docstatus": ["!=", 2]},
            "name",
        )
        if not dap_name:
            dap = frappe.new_doc("Tax Litigation DAP")
            dap.tax_litigation = litigation_name
            dap.tax_assessment = assessment.name
            dap.company = assessment.company
            dap.tax_type = assessment.tax_type
            dap.fiscal_obligation = assessment.fiscal_obligation
            dap.company_submission_due_date = assessment.legal_time_limit_date
            dap.insert(ignore_permissions=True)
            dap_name = dap.name

        current_phase = frappe.db.get_value(
            "Tax Litigation", litigation_name,
            ["current_phase_doctype", "current_phase"],
            as_dict=True,
        )
        if not current_phase or not current_phase.current_phase:
            frappe.db.set_value("Tax Litigation", litigation_name, {
                "current_stage": "DAP",
                "current_phase_doctype": "Tax Litigation DAP",
                "current_phase": dap_name,
            }, update_modified=False)

    return {
        "assessment":
            assessment.name,

        "decision":
            assessment.assessment_decision,

        "fiscal_obligation":
            assessment.fiscal_obligation,

        "decision_log":
            log_name,

        "tax_litigation":
            litigation_name,
    }
