from frappe.model.document import Document
import frappe

class TaxDeclarationObligationLink(Document):
    def before_insert(self):
        frappe.throw("Edit declaration child records only through the controlled parent actions.")

    def before_save(self):
        frappe.throw("Edit declaration child records only through the controlled parent actions.")

    def on_trash(self):
        frappe.throw("Declaration links and evidence cannot be deleted directly.")
