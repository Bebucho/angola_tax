"""Register reports and merge navigation. No fiscal/scheduler/notification writes."""
import json
import frappe
REPORTS=[('Posição Fiscal','Tax Fiscal Position','tax_fiscal_position'),
    ('Registo de Declarações','Tax Declaration Register','tax_declaration_register'),
    ('Cobranças e Pagamentos','Tax Payment Register','tax_payment_register'),
    ('Decisões e Contencioso','Tax Dispute Register','tax_dispute_register')]

def install():
    frappe.only_for('System Manager')
    for label,name,slug in REPORTS:frappe.reload_doc('angola_tax','report',slug,force=True)
    workspace=frappe.get_doc('Workspace','Tax Control')
    existing={(row.link_type,row.link_to) for row in workspace.links or []}
    if not any(row.type=='Card Break' and row.label=='Relatórios Fiscais' for row in workspace.links or []):
        workspace.append('links',{'type':'Card Break','label':'Relatórios Fiscais'})
    for label,name,slug in REPORTS:
        if ('Report',name) not in existing:
            workspace.append('links',{'type':'Link','label':label,'link_type':'Report','link_to':name})
    shortcuts={row.link_to for row in workspace.shortcuts or []}
    if 'Tax Fiscal Position' not in shortcuts:
        workspace.append('shortcuts',{'label':'Posição Fiscal','type':'Report','link_to':'Tax Fiscal Position','color':'Grey'})
    blocks=json.loads(workspace.content or '[]')
    if not any(b.get('data',{}).get('shortcut_name')=='Posição Fiscal' for b in blocks):
        blocks.append({'id':'tax-core-report-position','type':'shortcut','data':{'shortcut_name':'Posição Fiscal','col':4}})
    if not any(b.get('data',{}).get('card_name')=='Relatórios Fiscais' for b in blocks):
        blocks.append({'id':'tax-core-report-links','type':'card','data':{'card_name':'Relatórios Fiscais','col':12}})
    workspace.content=json.dumps(blocks);workspace.save(ignore_permissions=True)
    from frappe.modules.export_file import export_to_files
    export_to_files(record_list=[['Workspace','Tax Control']],record_module='Angola Tax')
    frappe.clear_cache()
    return {'reports_registered':len(REPORTS),'fiscal_documents_changed':0,'notifications_sent':0,'rules_activated':0}
