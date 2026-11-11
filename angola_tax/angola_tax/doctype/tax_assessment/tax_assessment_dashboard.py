from frappe import _


def get_data():
    return {
        "fieldname": "tax_assessment",

        "transactions": [
            {
                "label": _("Tax Control"),
                "items": [
                    "Fiscal Obligation",
                    "Tax Assessment Decision Log",
                    "Tax Settlement",
                    "Tax Litigation",
                    "Tax Provision",
                ],
            },
        ],

        "non_standard_fieldnames": {
            "Fiscal Obligation":
                "source_tax_assessment",

            "Tax Assessment Decision Log":
                "tax_assessment",

            "Tax Settlement":
                "tax_assessment",

            "Tax Litigation":
                "tax_assessment",

            "Tax Provision":
                "tax_assessment",
        },
    }
