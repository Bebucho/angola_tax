import frappe


def execute():
    frappe.db.sql("update `tabTax Account Configuration` set account_role='Legacy' where coalesce(account_role, '')=''")
    frappe.db.sql("update `tabTax Account Configuration` set ingestion_policy='Legacy' where coalesce(ingestion_policy, '')=''")
    frappe.db.sql("update `tabFiscal Obligation` set determination_basis='GL' where coalesce(determination_basis, '')=''")
