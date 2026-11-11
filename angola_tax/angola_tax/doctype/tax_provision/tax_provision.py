import frappe
from frappe.model.document import Document
from frappe.utils import flt


class TaxProvision(Document):

    def validate(self):
        self.load_assessment_context()
        self.load_accounts()
        self.calculate_provision()

    def load_assessment_context(self):
        if not self.tax_assessment:
            return

        assessment = frappe.get_doc(
            "Tax Assessment",
            self.tax_assessment,
        )

        self.company = assessment.company
        self.tax_type = assessment.tax_type

        litigation = None
        if self.tax_litigation:
            litigation = frappe.get_doc("Tax Litigation", self.tax_litigation)

        self.exposure_amount = (
            flt(litigation.amount_under_dispute)
            if litigation
            else flt(assessment.disputed_amount
                     or assessment.effective_assessed_amount
                     or assessment.amount_assessed
                     or 0)
        )

    def load_accounts(self):
        if not self.company:
            return

        rows = frappe.get_all(
            "Tax Penalty Settings",
            filters={
                "company": self.company,
                "active": 1,
            },
            fields=[
                "tax_litigation_provision_account",
                "tax_litigation_cost_account",
            ],
            limit=1,
        )

        if not rows:
            return

        self.provision_account = (
            rows[0].tax_litigation_provision_account
        )

        self.provision_cost_account = (
            rows[0].tax_litigation_cost_account
        )

    def calculate_provision(self):
        exposure = flt(
            self.exposure_amount
        )

        decision = (
            self.provision_decision
        )

        if decision == "Full Provision":
            self.provision_amount = exposure

        elif decision == "No Provision":
            self.provision_amount = 0
        elif decision in ("Already Recognised", "Disclosure Only"):
            self.provision_amount = 0

        amount = flt(
            self.provision_amount
        )

        if amount < 0:
            frappe.throw(
                "Provision Amount cannot be negative."
            )

        if amount > exposure:
            frappe.throw(
                "Provision Amount cannot exceed "
                "Exposure Amount."
            )

        if (
            decision == "Partial Provision"
            and amount <= 0
        ):
            frappe.throw(
                "Provision Amount is mandatory "
                "for Partial Provision."
            )

        self.outstanding_provision = max(
            amount - flt(self.reversed_amount),
            0,
        )

    def before_cancel(self):
        frappe.throw(
            "Submitted Tax Provisions cannot be cancelled."
        )

    def on_trash(self):
        if self.docstatus == 1:
            frappe.throw(
                "Submitted Tax Provisions cannot be deleted."
            )
