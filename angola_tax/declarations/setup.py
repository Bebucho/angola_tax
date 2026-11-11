"""Add declaration metadata without recreating fiscal or account configuration."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

def field(name, kind='Data', options=None, **kw):
    value = {'fieldname':name, 'label':name.replace('_',' ').title(), 'fieldtype':kind, **kw}
    if options is not None: value['options'] = options
    return value

def install():
    if not frappe.db.exists('DocType','Tax Declaration'):
        frappe.throw('Run bench migrate to load the declaration DocTypes before setup.')
    config_fields = [
        field('declaration_section','Section Break',insert_after='company'),
        field('declaration_purpose','Select','Monetary\nDeclaration',default='Monetary',insert_after='declaration_section'),
        field('declaration_type','Link','Tax Declaration Type',insert_after='declaration_purpose',depends_on="eval:doc.declaration_purpose=='Declaration'"),
        field('declaration_applicability','Select','Required\nOptional\nNot Applicable\nNeeds Review',default='Needs Review',insert_after='declaration_type'),
        field('declaration_reason','Small Text',insert_after='declaration_applicability'),
        field('declaration_responsible_user','Link','User',insert_after='declaration_reason'),
        field('declaration_reviewer','Link','User',insert_after='declaration_responsible_user')]
    line_fields = [
        field('declaration_anchor','Select','Period End\nFixed Annual Date\nEvent Date',default='Period End',insert_after='use_end_of_month'),
        field('declaration_month','Int',insert_after='declaration_anchor'),
        field('declaration_year_offset','Int',insert_after='declaration_month'),
        field('declaration_day_offset','Int',insert_after='declaration_year_offset'),
        field('declaration_working_day','Check',insert_after='declaration_day_offset')]
    for row in config_fields:
        if row['fieldname'] not in ('declaration_section','declaration_purpose'):
            row['depends_on']="eval:doc.declaration_purpose=='Declaration'"
    create_custom_fields({'Tax Deadline Configuration':config_fields, 'Tax Deadline Line':line_fields}, update=True)
    # reqd is conditional: monetary consumers keep their existing required fields.
    meta = frappe.get_meta('Tax Deadline Configuration')
    for name in ('tax_type','tax_regime','tax_year','legal_periodicity','legal_due_timing','legal_due_day_rule'):
        if meta.has_field(name):
            make_property_setter('Tax Deadline Configuration',name,'reqd',0,'Check')
            make_property_setter('Tax Deadline Configuration',name,'mandatory_depends_on',
                "eval:doc.declaration_purpose!='Declaration'",'Data')
    make_property_setter('Tax Deadline Configuration','deadline_rules','hidden',0,'Check')
    make_property_setter('Tax Deadline Configuration','deadline_rules','depends_on',
        "eval:doc.declaration_purpose=='Declaration'",'Data')
    make_property_setter('Tax Deadline Configuration','process_schedule','depends_on',
        "eval:doc.declaration_purpose!='Declaration'",'Data')
    make_property_setter('Tax Deadline Configuration','legal_deadline_section','depends_on',
        "eval:doc.declaration_purpose!='Declaration'",'Data')
    make_property_setter('Tax Deadline Line','periodicity','options','Monthly\nQuarterly\nAnnual\nTransaction','Text')
    ensure_roles()
    # Existing config must remain usable; System Manager config approval permission is explicit.
    for role in ('Tax Preparer','Tax Approver','Tax Submission User'):
        if not frappe.db.exists('Custom DocPerm',{'parent':'Tax Deadline Configuration','role':role,'permlevel':0}):
            frappe.get_doc({'doctype':'Custom DocPerm','parent':'Tax Deadline Configuration',
                'parenttype':'DocType','parentfield':'permissions','role':role,'read':1,'report':1,'permlevel':0}).insert()
    if not frappe.db.exists('Custom DocPerm',{'parent':'Tax Deadline Configuration','role':'System Manager','submit':1}):
        frappe.get_doc({'doctype':'Custom DocPerm','parent':'Tax Deadline Configuration',
            'parenttype':'DocType','parentfield':'permissions','role':'System Manager',
            'read':1,'write':1,'create':1,'submit':1,'cancel':1,'amend':1,'report':1,'permlevel':0}).insert()
    sources = {
        'II-MODELO1':('II — Declaração Modelo 1','II Annual Portal v1','Verified','CII art.51; Lei26/20. Output: reviewed portal-entry summary, not an AGT XML import.'),
        'II-WITHHOLDING':('II — Mapa de Retenção na Fonte','II Withholding Map v1','Needs Review','AGT withholding map guide; CII art.67. Confirm filing deadline binding and nil duty.'),
        'IVA-MODELO7':('IVA — Declaração Periódica / Modelo 7','VAT Portal v1','Needs Review','CIVA arts.32/44, Lei14/23; resolve indexed 2026 calendar conflict.'),
        'SAFT-INVOICING':('SAF-T (AO) — Facturação','Unsupported','Needs Review','DP71/25 art.25; DP312/18 art.3; current electronic-invoicing interaction.'),
        'SAFT-ACQUISITIONS':('SAF-T (AO) — Aquisição de Bens e Serviços','Unsupported','Needs Review','Confirm current supplier-annex waiver under official AGT notice.'),
        'SAFT-INVENTORY':('SAF-T — Inventários','Unsupported','Verified','DP71/25 art.24(1)(d); Circular05/2026. 2025: penalty-free until15April2026.'),
        'SAFT-ACCOUNTING':('SAF-T — Contabilidade','Unsupported','Verified','DP71/25 art.24(1)(e); DE317/20; AGT notice985578:2025 optional,2026 mandatory.'),
        'IRT-MODELO2':('IRT — Declaração Anual Modelo 2','Unsupported','Needs Review','AGT notice822996,8February2024 dispenses submission; reconcile calendar2026.'),
        'IRT-REMUNERATION':('IRT — Mapa de Remunerações','Unsupported','Needs Review','Verify current AGT payroll map schema, period and deadline.'),
        'II-SIMPLIFIED':('II — Declaração Simplificada','Unsupported','Needs Review','CII art.61; resolve current portal mapping versus legacy Modelo2.'),
        'IVA-SIMPLIFIED':('IVA — Declaração Simplificada','Unsupported','Needs Review','CIVA art.69-B; verify current export schema.'),
        'IEC-MONTHLY':('IEC — Declaração Mensal','Unsupported','Needs Review','Verify domestic-production/customs scope and current AGT schema.')}
    for code,(title,adapter,state,basis) in sources.items():
        if not frappe.db.exists('Tax Declaration Type',code):
            frappe.get_doc({'doctype':'Tax Declaration Type','filing_code':code,'statutory_name':title,
                'purpose':'Information' if code.startswith('SAFT') or code=='IRT-MODELO2' else 'Tax Return',
                'adapter_key':adapter,'verification_state':state,'legal_sources':basis,
                'schema_version':'Portal summary v1' if adapter.endswith('Portal v1') else 'Reviewed template v1' if adapter=='II Withholding Map v1' else 'Statutory baseline; exporter gated',
                'completion_rule':'Acknowledged','supports_replacement':int(adapter!='Unsupported'),
                'supports_corrective':0}).insert()
    workspace = frappe.get_doc('Workspace','Tax Control')
    existing = {r.link_to for r in workspace.links or []}
    for label,name in [('Declarative Obligations','Tax Declaration'),('Declaration Types','Tax Declaration Type')]:
        if name not in existing: workspace.append('links',{'type':'Link','label':label,'link_type':'DocType','link_to':name})
    shortcuts = {r.link_to for r in workspace.shortcuts or []}
    if 'Tax Declaration' not in shortcuts:
        workspace.append('shortcuts',{'label':'Declarative Obligations','type':'DocType','link_to':'Tax Declaration','doc_view':'List','color':'Grey'})
    # A content block is needed for an existing workspace to display the shortcut.
    import json
    content = json.loads(workspace.content or '[]')
    if not any(r.get('data',{}).get('shortcut_name')=='Declarative Obligations' for r in content):
        content.append({'id':'metagest-declarations-v1','type':'shortcut','data':{'shortcut_name':'Declarative Obligations','col':3}})
    workspace.content=json.dumps(content);workspace.save()
    frappe.clear_cache()
    return {'types':len(sources),'automatic_company_rules_created':0,
            'message':'Create submitted company declaration rules only after legal/applicability review.'}

def ensure_roles():
    for name in ('Tax Preparer','Tax Approver','Tax Submission User'):
        if not frappe.db.exists('Role',name): frappe.get_doc({'doctype':'Role','role_name':name,'desk_access':1}).insert()
