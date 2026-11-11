import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate


class TaxNotification(Document):
    """An AGT claim, separate from an operational tax determination."""

    IMMUTABLE_CONTEXT = (
        "company", "tax_type", "fiscal_period_from",
        "fiscal_period_to", "claimed_amount", "notification_reference",
    )

    def validate(self):
        if (
            self.fiscal_period_from and self.fiscal_period_to
            and getdate(self.fiscal_period_from) > getdate(self.fiscal_period_to)
        ):
            frappe.throw("Fiscal Period From cannot be later than Fiscal Period To.")

        if flt(self.claimed_amount) <= 0:
            frappe.throw("Amount Assessed must be greater than zero.")

        credit = flt(self.get("tax_credit_applied"))
        if credit < 0:
            frappe.throw("Tax Credit Applied cannot be negative.")
        if credit > flt(self.claimed_amount):
            frappe.throw("Tax Credit Applied cannot exceed Amount Assessed.")
        self.balance_amount = max(flt(self.claimed_amount) - credit, 0)

        tax_type = frappe.get_doc("Tax Type", self.tax_type)
        if tax_type.company != self.company or not tax_type.active:
            frappe.throw("Choose an active Tax Type belonging to this Company.")

        duplicate = frappe.db.exists(
            "Tax Notification",
            {
                "company": self.company,
                "notification_reference": self.notification_reference,
                "name": ["!=", self.name or ""],
            },
        )
        if duplicate:
            frappe.throw(
                f"Notification Reference {self.notification_reference} "
                f"already exists for {self.company}."
            )

        previous = self.get_doc_before_save()
        if previous and previous.fiscal_obligation:
            if self.fiscal_obligation != previous.fiscal_obligation:
                frappe.throw("The linked Fiscal Obligation cannot be changed.")

            obligation_status = frappe.db.get_value(
                "Fiscal Obligation", self.fiscal_obligation, "docstatus"
            )
            if previous.docstatus == 1 or obligation_status == 1:
                for fieldname in self.IMMUTABLE_CONTEXT:
                    if self.get(fieldname) != previous.get(fieldname):
                        frappe.throw(
                            f"{self.meta.get_label(fieldname)} cannot change after "
                            "the Fiscal Obligation has been submitted."
                        )

    def on_update(self):
        """Keep the linked obligation in step while both documents are drafts."""
        if not self.fiscal_obligation or self.docstatus != 0:
            return

        obligation = frappe.get_doc("Fiscal Obligation", self.fiscal_obligation)
        if obligation.docstatus != 0:
            return

        tax_type = frappe.get_doc("Tax Type", self.tax_type)
        operational = frappe.get_all(
            "Fiscal Obligation",
            filters={
                "company": self.company,
                "tax_type": self.tax_type,
                "fiscal_period_from": self.fiscal_period_from,
                "fiscal_period_to": self.fiscal_period_to,
                "obligation_origin": "Operational",
                "docstatus": ["!=", 2],
            },
            pluck="name",
            limit=2,
        )
        related = operational[0] if len(operational) == 1 else None

        obligation.company = self.company
        obligation.tax_type = self.tax_type
        obligation.tax_name = tax_type.tax_name
        obligation.obligation_type = tax_type.obligation_type
        obligation.responsible_user = self.responsible_user
        obligation.fiscal_period_from = self.fiscal_period_from
        obligation.fiscal_period_to = self.fiscal_period_to
        obligation.related_operational_obligation = related
        obligation.currency = frappe.db.get_value(
            "Company", self.company, "default_currency"
        )
        obligation.amount_assessed = flt(self.claimed_amount)
        obligation.assessment_document = self.notification_document
        obligation.save(ignore_permissions=True)

    def after_insert(self):
        # Runs in the same transaction as the first save. A failed
        # obligation insert rolls back the notification as well.
        existing = frappe.db.get_value(
            "Fiscal Obligation",
            {"source_tax_notification": self.name},
            "name",
        )
        if existing:
            self.db_set("fiscal_obligation", existing, update_modified=False)
            return

        tax_type = frappe.get_doc("Tax Type", self.tax_type)
        operational = frappe.get_all(
            "Fiscal Obligation",
            filters={
                "company": self.company,
                "tax_type": self.tax_type,
                "fiscal_period_from": self.fiscal_period_from,
                "fiscal_period_to": self.fiscal_period_to,
                "obligation_origin": "Operational",
                "docstatus": ["!=", 2],
            },
            pluck="name",
            limit=2,
        )
        related = operational[0] if len(operational) == 1 else None
        obligation = frappe.new_doc("Fiscal Obligation")
        obligation.obligation_origin = "Tax Notification"
        obligation.source_tax_notification = self.name
        obligation.source_document_type = "Tax Notification"
        obligation.source_document = self.name
        obligation.related_operational_obligation = related
        obligation.source_module = "Manual"
        obligation.company = self.company
        obligation.tax_type = self.tax_type
        obligation.tax_name = tax_type.tax_name
        obligation.obligation_type = tax_type.obligation_type
        obligation.responsible_user = self.responsible_user
        obligation.fiscal_period_from = self.fiscal_period_from
        obligation.fiscal_period_to = self.fiscal_period_to
        obligation.currency = frappe.db.get_value(
            "Company", self.company, "default_currency"
        )
        obligation.amount_assessed = flt(self.claimed_amount)
        obligation.assessment_document = self.notification_document
        obligation.status = "Under Review"
        obligation.insert(ignore_permissions=True)

        self.db_set("fiscal_obligation", obligation.name, update_modified=False)
        self.fiscal_obligation = obligation.name
        if related:
            self.db_set(
                "related_operational_obligation", related,
                update_modified=False,
            )
            self.related_operational_obligation = related

    def before_submit(self):
        if not self.fiscal_obligation:
            frappe.throw("The notification has no Fiscal Obligation.")

        if self.decision not in ("Accepted", "Disputed"):
            frappe.throw("Choose Accept or Dispute before submitting the notification.")

        missing = []
        if not self.decision_date:
            missing.append("Decision Date")
        if not self.decision_document:
            missing.append("Decision Document")
        if not self.decision_notes:
            missing.append("Decision Notes")
        if self.legal_time_limit_days in (None, ""):
            missing.append("Legal Time Limit in Days")
        if missing:
            frappe.throw("Decision requires: " + ", ".join(missing))

        if int(self.legal_time_limit_days) < 0:
            frappe.throw("Legal Time Limit in Days cannot be negative.")

        if self.decision == "Accepted" and flt(self.tax_credit_applied):
            if not self.tax_credit_account:
                frappe.throw("Tax Credit Account is required when a credit is applied.")
            account = frappe.db.get_value(
                "Account", self.tax_credit_account, ["company", "is_group"]
            )
            if not account or account[0] != self.company or account[1]:
                frappe.throw("Tax Credit Account must be a ledger account belonging to the Company.")

        if frappe.db.get_value(
            "Fiscal Obligation", self.fiscal_obligation,
            "source_tax_notification",
        ) != self.name:
            frappe.throw("The linked Fiscal Obligation belongs to another source.")

    def on_submit(self):
        obligation = frappe.get_doc("Fiscal Obligation", self.fiscal_obligation)
        if obligation.docstatus == 0:
            obligation.flags.ignore_permissions = True
            obligation.submit()

        assessment = self.create_tax_assessment()
        self.db_set("tax_assessment", assessment.name, update_modified=False)
        self.tax_assessment = assessment.name

        if self.decision == "Disputed":
            litigation = self.create_tax_litigation(assessment)
            self.db_set("tax_litigation", litigation.name, update_modified=False)
            self.tax_litigation = litigation.name

    def create_tax_assessment(self):
        """Create the formal assessment from this notification and decision."""
        existing = frappe.db.get_value(
            "Tax Assessment", {"fiscal_obligation": self.fiscal_obligation}, "name"
        )
        if existing:
            frappe.throw(
                f"Fiscal Obligation {self.fiscal_obligation} already has Tax Assessment {existing}."
            )

        from angola_tax.angola_tax.doctype.tax_assessment.tax_assessment import (
            get_new_assessment_from_obligation,
        )

        assessment = frappe.new_doc("Tax Assessment")
        assessment.update(get_new_assessment_from_obligation(self.fiscal_obligation))
        assessment.source_tax_notification = self.name
        assessment.amount_assessed = flt(self.claimed_amount)
        assessment.tax_credit_applied = flt(self.tax_credit_applied)
        assessment.tax_credit_account = self.tax_credit_account
        assessment.net_payable_amount = flt(self.balance_amount)
        assessment.assessment_decision = self.decision
        assessment.decision_date = self.decision_date
        assessment.decision_reference = self.decision_reference
        assessment.decision_document = self.decision_document
        assessment.decision_notes = self.decision_notes
        assessment.legal_time_limit_days = int(self.legal_time_limit_days)

        if self.decision == "Accepted":
            assessment.acceptance_date = self.decision_date
            assessment.disputed_amount = 0
        else:
            assessment.disputed_amount = flt(self.claimed_amount)
            assessment.undisputed_amount = 0

        assessment.flags.ignore_permissions = True
        assessment.insert(ignore_permissions=True)
        assessment.submit()
        return assessment

    def create_tax_litigation(self, assessment):
        """Open a Tax Litigation case and its initial DAP phase."""

        existing = frappe.db.get_value(
            "Tax Litigation",
            {"tax_assessment": assessment.name},
            "name",
        )
        if existing:
            frappe.throw(
                f"Tax Litigation {existing} already exists "
                f"for this Assessment."
            )

        litigation = frappe.new_doc("Tax Litigation")
        litigation.tax_assessment = assessment.name
        litigation.fiscal_obligation = self.fiscal_obligation
        litigation.company = self.company
        litigation.tax_type = self.tax_type
        litigation.amount_under_dispute = flt(self.claimed_amount)
        litigation.current_stage = "DAP"
        litigation.status = "Open"
        litigation.litigation_notes = self.decision_notes
        litigation.insert(ignore_permissions=True)

        dap = frappe.new_doc("Tax Litigation DAP")
        dap.tax_litigation = litigation.name
        dap.tax_assessment = assessment.name
        dap.company = litigation.company
        dap.tax_type = litigation.tax_type
        dap.fiscal_obligation = litigation.fiscal_obligation
        dap.company_submission_due_date = assessment.legal_time_limit_date

        # The disputed Tax Notification is the event that opens
        # the DAP phase. Preserve the supporting decision data
        # as the initial company submission context.
        dap.submission_date = self.decision_date
        dap.submission_reference = self.decision_reference
        dap.submission_document = self.decision_document

        dap.insert(ignore_permissions=True)

        litigation.db_set(
            "current_stage",
            "DAP",
            update_modified=False,
        )
        litigation.db_set(
            "current_phase_doctype",
            dap.doctype,
            update_modified=False,
        )
        litigation.db_set(
            "current_phase",
            dap.name,
            update_modified=False,
        )

        litigation.current_stage = "DAP"
        litigation.current_phase_doctype = dap.doctype
        litigation.current_phase = dap.name

        return litigation

    def before_cancel(self):
        frappe.throw(
            "Submitted Tax Notifications cannot be cancelled. "
            "Correction and reissue controls are unfinished."
        )

    def on_trash(self):
        if self.fiscal_obligation:
            frappe.throw("A Tax Notification with a Fiscal Obligation cannot be deleted.")
