from frappe import _


def get_data():
    return {
        "fieldname": "source_tax_notification",
        "transactions": [{"label": _("Tax"), "items": ["Fiscal Obligation"]}],
    }
