from frappe import _


def get_data():
    return {
        "fieldname": "fiscal_obligation",
        "transactions": [
            {
                "label": _("Tax"),
                "items": [
                    "Tax Assessment",
                    "Tax Notification",
                    "Tax Payment Plan",
                    "Tax Payment",
                ],
            }
        ],
        "non_standard_fieldnames": {
            "Tax Notification": "fiscal_obligation",
        },
    }
