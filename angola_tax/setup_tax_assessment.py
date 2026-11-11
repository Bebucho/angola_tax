import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def setup():
    custom_fields = {
        "Journal Entry": [
            {
                "fieldname": "tax_assessment",
                "label": "Tax Assessment",
                "fieldtype": "Link",
                "options": "Tax Assessment",
                "insert_after": "user_remark",
                "read_only": 1,
                "no_copy": 1,
            }
        ]
    }

    create_custom_fields(
        custom_fields,
        update=True,
    )

    frappe.clear_cache()

    print("Journal Entry Tax Assessment link created.")
