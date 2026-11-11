import frappe
from frappe.model.document import Document


PHASE_DOCTYPES = {
    "Tax Litigation DAP",
    "Tax Litigation Reclamacao Administrativa",
    "Tax Litigation Recurso Hierarquico",
    "Tax Litigation Impugnacao Judicial",
}


class TaxTechnicalMeeting(Document):
    def validate(self):
        if self.reference_doctype not in PHASE_DOCTYPES:
            frappe.throw("Technical Meetings must be linked to a litigation phase.")
        if not frappe.db.exists(self.reference_doctype, self.reference_name):
            frappe.throw("The selected litigation phase does not exist.")
