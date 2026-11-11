from frappe import _


def get_data():
    return {
        "fieldname": "tax_type",
        "transactions": [
            {
                "label": _("Configuration"),
                "items": ["Tax Account Configuration", "Tax Deadline Configuration"],
            }
        ],
    }
