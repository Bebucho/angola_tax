# Copyright (c) 2026, Metagest and contributors
# For license information, please see license.txt

import frappe
import json
from frappe.model.document import Document


class TaxType(Document):

    def before_naming(self):
        self.tax_code = (self.tax_code or "").strip().upper()
        self.tax_name = " ".join((self.tax_name or "").split())

    def autoname(self):
        # Preserve existing IDs. New IDs are stable across imports and
        # distinct when companies use the same statutory tax code.
        from hashlib import sha256

        identity = json.dumps(
            [self.company, self.tax_code],
            ensure_ascii=False,
        )
        digest = sha256(identity.encode("utf-8")).hexdigest()[:16]
        self.name = f"{self.tax_code[:100]}-{digest}"

    def validate(self):
        self.tax_code = (self.tax_code or "").strip().upper()
        self.tax_name = " ".join((self.tax_name or "").split())

        if not self.company or not self.tax_code or not self.tax_name:
            return

        for other in frappe.get_all(
            "Tax Type",
            filters={"company": self.company},
            fields=["name", "tax_code", "tax_name"],
            limit_page_length=0,
        ):
            if other.name == self.name:
                continue

            if (other.tax_code or "").strip().casefold() == self.tax_code.casefold():
                frappe.throw(
                    f"Tax Code {self.tax_code} already exists "
                    f"for Company {self.company}."
                )

            if " ".join((other.tax_name or "").split()).casefold() == self.tax_name.casefold():
                frappe.throw(
                    f"Tax Name {self.tax_name} already exists "
                    f"for Company {self.company}."
                )
