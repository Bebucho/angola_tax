"""Company plus source-document access is required for every warning surface."""
from contextlib import contextmanager
import frappe

@contextmanager
def as_user(user):
    old=frappe.session.user
    try:
        frappe.set_user(user)
        yield
    finally: frappe.set_user(old)

def allowed_companies(user=None):
    user=user or frappe.session.user
    if user=='Guest':return []
    try:
        with as_user(user):
            readable=frappe.get_list('Company',pluck='name',limit_page_length=0)
    except frappe.PermissionError:
        return []
    if user=='Administrator':return readable
    # Company restrictions remain effective even when originally configured only
    # for a particular fiscal DocType. They must not open a warning/configuration leak.
    permissions=frappe.get_all('User Permission',filters={'user':user,'allow':'Company'},
        fields=['for_value','hide_descendants'],limit_page_length=0)
    if not permissions:return readable
    permitted=set()
    for row in permissions:
        permitted.add(row.for_value)
        if not row.hide_descendants:
            from frappe.utils.nestedset import get_descendants_of
            permitted.update(get_descendants_of('Company',row.for_value))
    return [name for name in readable if name in permitted]

def company_allowed(company,user=None):
    return bool(company and company in allowed_companies(user))

def source_allowed(doctype,name,company,user=None):
    from angola_tax.alerts.adapters import company_of
    user=user or frappe.session.user
    if not company_allowed(company,user): return False
    try:
        doc=frappe.get_doc(doctype,name)
        return company_of(doc)==company and bool(frappe.has_permission(doctype,'read',doc=doc,user=user))
    except frappe.DoesNotExistError: return False

def has_permission(doc,user=None,permission_type=None):
    if not company_allowed(doc.company,user): return False
    if doc.doctype=='Tax Alert Rule': return True
    if doc.doctype=='Tax Warning Delivery':
        warning=frappe.get_doc('Tax Warning',doc.warning)
        return source_allowed(warning.source_doctype,warning.source_name,doc.company,user)
    return source_allowed(doc.source_doctype,doc.source_name,doc.company,user)

def query_conditions(user=None,doctype=None):
    user=user or frappe.session.user
    doctype=doctype or 'Tax Warning'
    companies=allowed_companies(user)
    if not companies: return '1=0'
    company_sql=','.join(frappe.db.escape(c) for c in companies)
    base=f'`tab{doctype}`.company in ({company_sql})'
    if doctype=='Tax Alert Rule': return base
    if doctype=='Tax Warning Delivery':
        names=[r.name for r in frappe.get_all('Tax Warning Delivery',filters={'company':['in',companies]},fields=['name','warning','company'],limit_page_length=0)
            if has_permission(frappe.get_doc('Tax Warning Delivery',r.name),user)]
    else:
        names=[r.name for r in frappe.get_all('Tax Warning',filters={'company':['in',companies]},fields=['name','company','source_doctype','source_name'],limit_page_length=0)
            if source_allowed(r.source_doctype,r.source_name,r.company,user)]
    return base+' and '+(f'`tab{doctype}`.name in ('+','.join(frappe.db.escape(n) for n in names)+')' if names else '1=0')

def rule_query(user=None): return query_conditions(user,'Tax Alert Rule')
def warning_query(user=None): return query_conditions(user,'Tax Warning')
def delivery_query(user=None): return query_conditions(user,'Tax Warning Delivery')

# Layer over Frappe's own per-recipient notification access; leave non-tax logs unchanged.
def notification_query(user=None):
    user=user or frappe.session.user
    from frappe.desk.doctype.notification_log.notification_log import get_permission_query_conditions
    base=get_permission_query_conditions(user) or '1=1'
    names=[r.name for r in frappe.get_all('Tax Warning',fields=['name','company','source_doctype','source_name'],limit_page_length=0)
        if source_allowed(r.source_doctype,r.source_name,r.company,user)]
    allowed='`tabNotification Log`.document_name in ('+','.join(frappe.db.escape(n) for n in names)+')' if names else '1=0'
    return '('+base+") and (coalesce(`tabNotification Log`.document_type,'') != 'Tax Warning' or ("+allowed+'))'

def notification_permission(doc,user=None,permission_type=None):
    user=user or frappe.session.user
    from frappe.desk.doctype.notification_log.notification_log import has_permission as original
    if not original(doc,user=user): return False
    if doc.document_type!='Tax Warning': return True
    if not frappe.db.exists('Tax Warning',doc.document_name): return False
    return has_permission(frappe.get_doc('Tax Warning',doc.document_name),user,'read')
