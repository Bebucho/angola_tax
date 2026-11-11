
"""Keep tax-generated accounting entries tied to their fiscal lifecycle."""

import frappe


TAX_JOURNAL_LINKS = (
    ("Industrial Tax Computation", "recognition_journal_entry"),
    ("Tax Payment", "journal_entry"),
    ("Tax Assessment", "adjustment_journal_entry"),
    ("Tax Assessment", "recognition_journal_entry"),
    ("Tax Settlement", "adjustment_journal_entry"),
    ("Tax Settlement", "settlement_journal_entry"),
)


def linked_tax_document(journal_entry):
    for doctype, fieldname in TAX_JOURNAL_LINKS:
        if not frappe.db.exists("DocType", doctype):
            continue
        if not frappe.get_meta(doctype).has_field(fieldname):
            continue
        linked = frappe.db.get_value(
            doctype, {fieldname: journal_entry}, "name"
        )
        if linked:
            return f"{doctype} {linked}"
    if frappe.db.exists("DocType", "Industrial Tax Estimate"):
        owners = frappe.db.sql("""select c.name from `tabIndustrial Tax Estimate` e
            join `tabIndustrial Tax Computation` c on c.name=e.parent
            where e.journal_entry=%s and c.docstatus=1 limit 1""", journal_entry)
        if owners:
            return f"Industrial Tax Computation {owners[0][0]} (adopted estimate)"
    return None


def block_tax_journal_reversal(doc, method=None):
    if not doc.get("reversal_of"):
        return
    linked = linked_tax_document(doc.reversal_of)
    if linked:
        frappe.throw(
            f"Journal Entry {doc.reversal_of} belongs to {linked}. "
            "Reverse it through a controlled tax correction workflow."
        )


def block_tax_journal_cancellation(doc, method=None):
    linked = linked_tax_document(doc.name)
    if linked:
        frappe.throw(
            f"Journal Entry {doc.name} belongs to {linked}. "
            "Cancel or correct the tax document through its controlled workflow."
        )


