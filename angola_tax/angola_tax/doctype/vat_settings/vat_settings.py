import frappe
from frappe.utils import getdate
from frappe.model.document import Document


class VATSettings(Document):

    def validate(self):
        self.validate_accounts()
        self.validate_purchase_classifications()

    def validate_accounts(self):
        seen = set()

        for row in self.account_mappings or []:
            if not row.active:
                continue

            if row.account in seen:
                frappe.throw(
                    f"VAT account {row.account} is mapped more than once."
                )

            seen.add(row.account)

            account_company = frappe.db.get_value(
                "Account", row.account, "company"
            )

            if account_company != self.company:
                frappe.throw(
                    f"Account {row.account} does not belong to "
                    f"company {self.company}."
                )

        if not any(
            row.active and row.modelo_7_side == "State"
            for row in self.account_mappings or []
        ):
            frappe.throw(
                "At least one active State-side VAT account is required."
            )

        if not any(
            row.active and row.modelo_7_side == "Taxpayer"
            for row in self.account_mappings or []
        ):
            frappe.throw(
                "At least one active Taxpayer-side VAT account is required."
            )

    def validate_purchase_classifications(self):
        field_map = {
            "Fixed Assets and Investments": ("16", "17"),
            "Inventory": ("18", "19"),
            "Other Consumption Goods": ("20", "21"),
            "Services": ("22", "23"),
        }

        seen_item = set()
        seen_group = set()

        for row in self.purchase_classifications or []:

            if not row.active:
                continue

            if not row.item and not row.item_group:
                frappe.throw(
                    f"Row #{row.idx}: Item Group or Item Override "
                    "is required."
                )

            if (
                row.effective_from
                and row.effective_to
                and getdate(row.effective_from)
                > getdate(row.effective_to)
            ):
                frappe.throw(
                    f"Row #{row.idx}: Effective From cannot be "
                    "later than Effective To."
                )

            fields = field_map.get(row.modelo_7_category)

            if not fields:
                frappe.throw(
                    f"Row #{row.idx}: invalid Modelo 7 Category."
                )

            row.modelo_7_base_field = fields[0]
            row.modelo_7_vat_field = fields[1]

            if row.item:
                item_group = frappe.db.get_value(
                    "Item",
                    row.item,
                    "item_group",
                )

                if row.item_group and item_group != row.item_group:
                    frappe.throw(
                        f"Row #{row.idx}: Item {row.item} belongs "
                        f"to Item Group {item_group}, not "
                        f"{row.item_group}."
                    )

                key = (
                    row.item,
                    row.effective_from or "",
                    row.effective_to or "",
                )

                if key in seen_item:
                    frappe.throw(
                        f"Duplicate active Item classification "
                        f"for {row.item}."
                    )

                seen_item.add(key)

            elif row.item_group:
                key = (
                    row.item_group,
                    row.effective_from or "",
                    row.effective_to or "",
                )

                if key in seen_group:
                    frappe.throw(
                        f"Duplicate active Item Group classification "
                        f"for {row.item_group}."
                    )

                seen_group.add(key)

