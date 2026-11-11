"""Protect approved fiscal evidence and journals; draft and legacy work remains available."""
import frappe


def source_is_owned(doctype, name):
    if not name:return False
    if frappe.db.exists('Industrial Tax Withholding',{'event_voucher_type':doctype,'event_voucher':name,'docstatus':1}):return True
    if frappe.db.exists('Industrial Tax Withholding',{'accounting_voucher_type':doctype,'accounting_voucher':name,'docstatus':1}):return True
    if doctype in ('Sales Invoice','Purchase Invoice') and frappe.db.sql('''select w.name
        from `tabIndustrial Tax Withholding Invoice` i join `tabIndustrial Tax Withholding` w on w.name=i.parent
        where i.invoice_doctype=%s and i.invoice=%s and w.docstatus=1 limit 1''',(doctype,name)):
        return True
    if doctype=='Journal Entry':
        for typ,fields in (('Industrial Tax Computation',('recognition_journal_entry','offset_journal_entry')),
                           ('Industrial Tax Provisional',('recognition_journal_entry','existing_recognition_journal')),
                           ('Industrial Tax Credit',('asset_recognition_journal',))):
            for field in fields:
                if frappe.db.exists(typ,{field:name,'docstatus':1}):return True
        if frappe.db.sql('''select c.name from `tabIndustrial Tax Estimate` e
             join `tabIndustrial Tax Computation` c on c.name=e.parent
             where e.journal_entry=%s and c.docstatus=1 limit 1''',name):return True
    if doctype=='Sales Invoice' and frappe.db.sql('''select p.name from `tabIndustrial Tax Provisional Line` l
         join `tabIndustrial Tax Provisional` p on p.name=l.parent
         where l.sales_invoice=%s and p.docstatus=1 limit 1''',name):return True
    if frappe.db.sql('''select p.name from `tabIndustrial Tax Withholding GL Line` l
         join `tabIndustrial Tax Withholding Period` p on p.name=l.parent
         where l.voucher_type=%s and l.voucher_no=%s and p.docstatus=1 limit 1''',(doctype,name)):return True
    if frappe.db.sql('''select p.name from `tabIndustrial Tax Withholding Invoice` i
         join `tabIndustrial Tax Withholding Period` p on p.name=i.parent
         where i.invoice_doctype=%s and i.invoice=%s and p.docstatus=1 limit 1''',(doctype,name)):return True
    return False


def protect_source(doc,method=None):
    if source_is_owned(doc.doctype,doc.name):
        frappe.throw('Voucher backs approved industrial-tax evidence. Direct cancellation or amendment requires a controlled fiscal revision.')


def protect_reversal(doc,method=None):
    if doc.get('reversal_of') and source_is_owned('Journal Entry',doc.reversal_of):
        frappe.throw('Approved industrial-tax recognition, offset or evidence journal cannot be directly reversed.')


def protect_collection(doc,method=None):
    reference=doc.name if doc.doctype=='Tax Collection Reference' else doc.collection_reference
    if reference and frappe.db.exists('Industrial Tax Credit',{'collection_reference':reference,'docstatus':1}):
        frappe.throw('Confirmed payment backs an approved provisional credit; controlled reversal is required.')
