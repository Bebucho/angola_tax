"""Idempotent setup: no evaluation, sending or fiscal-document writes."""
import json
import frappe
from angola_tax.alerts.catalog import CATALOG,LEGACY_METHOD

def install():
    if frappe.session.user!='Administrator' and 'System Manager' not in frappe.get_roles():
        frappe.throw('System Manager is required.',frappe.PermissionError)
    if not frappe.db.exists('Role','Tax Alert Manager'):
        frappe.get_doc({'doctype':'Role','role_name':'Tax Alert Manager','desk_access':1}).insert(ignore_permissions=True)
    suggestions=0
    for company in frappe.get_all('Company',pluck='name',limit_page_length=0):
        for condition,(process,source,title) in CATALOG.items():
            if frappe.db.exists('Tax Alert Rule',{'company':company,'condition':condition}):continue
            notice=condition in ('configuration_health','authority_response')
            frappe.get_doc({'doctype':'Tax Alert Rule','rule_title':title,'company':company,
                'enabled':0,'condition':condition,'trigger_label':title,'fiscal_process':process,
                'kind':'Notice' if notice else 'Alert','severity':'Medium' if notice else 'High',
                'overdue_severity':'Critical','advance_days':7,'recurrence_hours':24,
                'escalation_hours':0 if notice else 48,'notify_in_app':0,
                'notes':'Suggested rule. Review ownership and preview before enabling; existing legal and internal dates are reused.'}).insert(ignore_permissions=True)
            suggestions+=1
    # Stop obsolete scheduler records too, not only the hook registration.
    for name in frappe.get_all('Scheduled Job Type',filters={'method':LEGACY_METHOD},pluck='name',limit_page_length=0):
        frappe.db.set_value('Scheduled Job Type',name,'stopped',1)
    if not frappe.get_meta('Tax Declaration').is_submittable:
        frappe.throw('Tax Declaration approval metadata was not migrated.')
    # Custom permissions take precedence over source permissions. Add only the missing intended submit grant.
    for role in ('Tax Approver','System Manager'):
        custom=frappe.get_all('Custom DocPerm',filters={'parent':'Tax Declaration','role':role,'permlevel':0},fields=['name','submit'],limit_page_length=0)
        for row in custom:
            if not row.submit:frappe.db.set_value('Custom DocPerm',row.name,'submit',1)
    workspace=frappe.get_doc('Workspace','Tax Control')
    entries=[('Configurar Alertas','DocType','Tax Alert Rule'),('Avisos Activos','DocType','Tax Warning'),
             ('Resumo dos Avisos','Report','Tax Warnings Summary'),('Detalhe dos Avisos','Report','Tax Active Warnings')]
    existing={(r.link_type,r.link_to) for r in workspace.links or []}
    if not any(r.type=='Card Break' and r.label=='Alertas e Avisos' for r in workspace.links or []):
        workspace.append('links',{'type':'Card Break','label':'Alertas e Avisos'})
    for label,kind,target in entries:
        if (kind,target) not in existing:
            workspace.append('links',{'type':'Link','label':label,'link_type':kind,'link_to':target})
    shortcuts={r.link_to for r in workspace.shortcuts or []}
    for label,kind,target in entries[:3]:
        if target not in shortcuts:workspace.append('shortcuts',{'label':label,'type':kind,'link_to':target,'doc_view':'List' if kind=='DocType' else None,'color':'Grey'})
    blocks=json.loads(workspace.content or '[]')
    for label,kind,target in entries[:3]:
        if not any(b.get('data',{}).get('shortcut_name')==label for b in blocks):
            blocks.append({'id':'tax-alert-'+target.lower().replace(' ','-'),'type':'shortcut','data':{'shortcut_name':label,'col':4}})
    if not any(b.get('data',{}).get('card_name')=='Alertas e Avisos' for b in blocks):
        blocks.append({'id':'tax-alert-links','type':'card','data':{'card_name':'Alertas e Avisos','col':12}})
    workspace.content=json.dumps(blocks);workspace.save(ignore_permissions=True)
    # Persist the merged workspace as source, retaining all previous sections.
    from frappe.modules.export_file import export_to_files
    export_to_files(record_list=[['Workspace','Tax Control']],record_module='Angola Tax')
    frappe.clear_cache()
    return {'disabled_rules_created':suggestions,'notifications_sent':0,'fiscal_documents_changed':0}

def prepare():
    if not frappe.db.exists('Role','Tax Alert Manager'):
        frappe.get_doc({'doctype':'Role','role_name':'Tax Alert Manager','desk_access':1}).insert(ignore_permissions=True)
    return {'roles_ready':True}
