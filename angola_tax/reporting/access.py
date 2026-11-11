"""Read-only access boundaries shared by the fiscal reports."""
import frappe
from frappe.utils import getdate,nowdate
from angola_tax.alerts.permissions import company_allowed,source_allowed

class Reader:
    def __init__(self,filters=None):
        self.filters=frappe._dict(filters or {})
        for flag in ('include_closed','include_drafts','outstanding_only','all_versions','include_missing','include_inactive'):
            if flag in self.filters:
                value=self.filters[flag]
                if value not in (None,'',0,'0',False,1,'1',True):frappe.throw('Parâmetro de selecção inválido: '+flag)
                self.filters[flag]=int(value or 0)
        self.company=self.filters.company
        if not self.company:frappe.throw('Seleccione a empresa.')
        if not company_allowed(self.company):frappe.throw('Sem acesso à empresa seleccionada.',frappe.PermissionError)
        start,end=self.filters.from_date,self.filters.to_date
        if start and end and getdate(start)>getdate(end):frappe.throw('A data inicial não pode ser posterior à data final.')
        self.cache={};self.restricted_links=0
    def can_read_type(self,doctype):return bool(frappe.has_permission(doctype,'read'))
    def documents(self,doctype,filters=None,required=True):
        if not self.can_read_type(doctype):
            if required:frappe.throw('Sem permissão de leitura para '+doctype+'.',frappe.PermissionError)
            return []
        meta=frappe.get_meta(doctype);where=dict(filters or {})
        if meta.has_field('company'):where['company']=self.company
        where.setdefault('docstatus',['!=',2])
        docs=[]
        for name in frappe.get_list(doctype,filters=where,pluck='name',limit_page_length=0):
            doc=self.linked(doctype,name)
            if doc is not None:docs.append(doc)
        return docs
    def linked(self,doctype,name):
        if not name:return None
        key=(doctype,name)
        if key not in self.cache:
            if not source_allowed(doctype,name,self.company):
                self.cache[key]=None;self.restricted_links+=1
            else:self.cache[key]=frappe.get_doc(doctype,name)
        return self.cache[key]
    def shared(self,doctype,name):
        if not name:return None
        key=(doctype,name)
        if key not in self.cache:
            try:
                doc=frappe.get_doc(doctype,name)
                self.cache[key]=doc if frappe.has_permission(doctype,'read',doc=doc) else None
            except frappe.DoesNotExistError:self.cache[key]=None
        return self.cache[key]
    def matches_tax(self,value):return not self.filters.tax_type or value==self.filters.tax_type
    def matches_status(self,value):return not self.filters.status or value==self.filters.status
    def matches_owner(self,value):return not self.filters.responsible_user or value==self.filters.responsible_user
    def period_matches(self,start,end):
        if not self.filters.from_date and not self.filters.to_date:return True
        if not start or not end:return False
        return ((not self.filters.from_date or getdate(end)>=getdate(self.filters.from_date)) and
                (not self.filters.to_date or getdate(start)<=getdate(self.filters.to_date)))
    def date_matches(self,value):
        if not self.filters.from_date and not self.filters.to_date:return True
        if not value:return False
        return ((not self.filters.from_date or getdate(value)>=getdate(self.filters.from_date)) and
                (not self.filters.to_date or getdate(value)<=getdate(self.filters.to_date)))
    def evidence(self,url,doctype,name):
        if not url or self.linked(doctype,name) is None:return None
        # Return a readable File link, not an unrestricted attachment URL.
        if not self.can_read_type('File'):
            self.restricted_links+=1
            return None
        for file_name in frappe.get_list('File',filters={'file_url':url,'attached_to_doctype':doctype,
                'attached_to_name':name},pluck='name',limit_page_length=0):
            file=frappe.get_doc('File',file_name)
            if frappe.has_permission('File','read',doc=file):return file_name
        return None
    def note(self,text):
        return 'Resultados limitados aos documentos que pode consultar. '+text+(' Alguns documentos relacionados não são apresentados por falta de acesso.' if self.restricted_links else '')

def column(name,label,kind='Data',options=None,width=140):
    result={'fieldname':name,'label':label,'fieldtype':kind,'width':width}
    if options is not None:result['options']=options
    return result
