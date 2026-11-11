import frappe
from frappe.model.document import Document


class TaxPenaltySettings(Document):

    def validate(self):
        self.validate_accounts()

    def on_trash(self):
        frappe.throw(
            "Tax Penalty Settings cannot be deleted. "
            "Disable the record instead."
        )

    def validate_accounts(self):
        accounts = {
            "Payment Plan Control Account":
                self.payment_plan_control_account,
            "Tax Penalty Cost Account":
                self.tax_penalty_cost_account,
            "Tax Litigation Provision Account":
                self.tax_provision_account,
            "Tax Litigation Cost Account":
                self.tax_litigation_cost_account,
        }

        for label, account in accounts.items():
            if not account:
                continue

            values = frappe.db.get_value(
                "Account",
                account,
                ["company", "is_group"],
                as_dict=True,
            )

            if not values:
                frappe.throw(
                    f"{label} {account} does not exist."
                )

            if values.company != self.company:
                frappe.throw(
                    f"{label} must belong to company "
                    f"{self.company}."
                )

            if values.is_group:
                frappe.throw(
                    f"{label} must be a ledger account, "
                    "not a group account."
                )
