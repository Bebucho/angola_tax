import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime
from angola_tax.alerts.catalog import CATALOG
from angola_tax.alerts.model import identity
from angola_tax.alerts.permissions import company_allowed

class TaxAlertRule(Document):
    def validate(self):
        selected={v[2]:k for k,v in CATALOG.items()}.get(self.trigger_label)
        if selected: self.condition=selected
        if self.condition not in CATALOG: frappe.throw('Select a supported fiscal warning condition.')
        process=CATALOG[self.condition][0]
        self.scope_key=identity(self.company,process,self.condition,'Rule','')
        if self.fiscal_process!=process: frappe.throw('The fiscal process does not match the selected condition.')
        for field in ('advance_days','recurrence_hours','escalation_hours'):
            if float(self.get(field) or 0)<0: frappe.throw('Warning intervals cannot be negative.')
        if not company_allowed(self.company): frappe.throw('Company access is required.',frappe.PermissionError)
        if self.enabled and not (self.responsible_user or self.responsible_role):
            # Source ownership is available for some documents but never silently default to Administrator.
            frappe.throw('Choose a responsible user or role before enabling this rule.')
        if self.condition=='legacy_compliance' and self.enabled:
            other=frappe.db.exists('Tax Alert Rule',{'company':self.company,'condition':self.condition,'enabled':1,'name':['!=',self.name or '']})
            if other: frappe.throw('Only one fiscal notification replacement rule may be enabled per company.')
        if self.enabled and self.condition!='legacy_compliance' and not frappe.db.exists('Tax Alert Rule',{
                'company':self.company,'condition':'legacy_compliance','enabled':1}):
            frappe.throw('Enable the company Fiscal Action Required replacement rule first; this preserves coverage during migration.')
        old=self.get_doc_before_save()
        if old and old.enabled and not self.enabled and self.condition=='legacy_compliance' and frappe.db.exists('Tax Alert Rule',{
                'company':self.company,'enabled':1,'condition':['!=','legacy_compliance']}):
            frappe.throw('Disable the other company warning rules before restoring legacy notifications.')
        if old and any(old.get(f)!=self.get(f) for f in ('company','condition','fiscal_process')) and frappe.db.exists('Tax Warning',{'rule':self.name}):
            frappe.throw('A rule with warning history cannot change its company, process or condition. Create a new rule.')
        if self.enabled:
            duplicates=frappe.db.exists('Tax Alert Rule',{'company':self.company,'condition':self.condition,'enabled':1,'name':['!=',self.name or '']})
            if duplicates: frappe.throw('Another enabled rule already covers this company and condition.')
        for field in ('responsible_user','escalation_user'):
            user=self.get(field)
            if user and (not frappe.db.get_value('User',user,'enabled') or not company_allowed(self.company,user)):
                frappe.throw('The selected recipient must be enabled and able to access the company.')

class ProtectedWarning(Document):
    def validate(self):
        if not frappe.flags.get('tax_alert_write'):
            frappe.throw('Use warning actions; source conditions and history are maintained automatically.')
    def on_trash(self): frappe.throw('Warning and delivery history must be retained.')

class TaxWarning(ProtectedWarning): pass
class TaxWarningDelivery(ProtectedWarning): pass
