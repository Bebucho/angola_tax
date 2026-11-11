import frappe
from frappe.model.document import Document


class TaxAssessmentDecisionLog(Document):

    def before_save(self):
        if not self.is_new():
            frappe.throw(
                "Tax Assessment Decision Log is immutable."
            )

    def on_trash(self):
        frappe.throw(
            "Tax Assessment Decision Log records cannot be deleted."
        )
