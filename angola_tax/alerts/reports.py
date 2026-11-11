import frappe
from frappe.utils import now_datetime,get_datetime
from angola_tax.alerts.permissions import company_allowed

def execute(filters=None,summary=False):
    filters=frappe._dict(filters or {})
    if filters.company and not company_allowed(filters.company): frappe.throw('Company access required.',frappe.PermissionError)
    where={}
    for key in ('company','fiscal_process','severity','responsible_user','status','kind'):
        if filters.get(key):where[key]=filters[key]
    if not filters.get('include_resolved') and not filters.get('status'):where['status']=['!=','Resolved']
    rows=frappe.get_list('Tax Warning',filters=where,fields=['name','company','fiscal_process','kind','severity','status',
        'title','responsible_user','due_date','explanation','action_needed','source_doctype','source_name','first_seen','last_seen','rule'],limit_page_length=0)
    if summary:
        groups={}
        for row in rows:
            key=tuple(row[k] for k in ('company','fiscal_process','kind','severity','status'))
            groups[key]=groups.get(key,0)+1
        data=[dict(zip(('company','fiscal_process','kind','severity','status'),key),warnings=value) for key,value in sorted(groups.items())]
        columns=[dict(fieldname=n,label=n.replace('_',' ').title(),fieldtype='Link' if n=='company' else 'Data',options='Company' if n=='company' else None,width=160) for n in ('company','fiscal_process','kind','severity','status')]+[dict(fieldname='warnings',label='Warnings',fieldtype='Int',width=100)]
        from angola_tax.alerts.service import summary as health
        rules=health(filters.company)['rules']
        stale=[r for r in rules if r.last_error or not r.last_evaluated_on or (now_datetime()-get_datetime(r.last_evaluated_on)).total_seconds()>7200]
        message='Enabled rules needing evaluation/review: '+str(len(stale))+'. Acknowledged, snoozed and dismissed alerts remain unresolved.'
        return columns,data,message
    columns=[dict(fieldname='name',label='Warning',fieldtype='Link',options='Tax Warning',width=120)]
    for name in ('company','fiscal_process','kind','severity','status','title','responsible_user','due_date','explanation','action_needed','source_doctype','source_name'):
        kind,options=('Link','Company') if name=='company' else ('Link','User') if name=='responsible_user' else ('Date',None) if name=='due_date' else ('Dynamic Link','source_doctype') if name=='source_name' else ('Data',None)
        columns.append(dict(fieldname=name,label=name.replace('_',' ').title(),fieldtype=kind,options=options,width=250 if name in ('explanation','action_needed','title') else 150))
    return columns,rows
