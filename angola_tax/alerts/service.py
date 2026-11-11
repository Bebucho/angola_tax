"""Serialized reconciliation, durable in-app delivery and controlled legacy cutover."""
from contextlib import contextmanager
import hashlib
import json
from html import escape
import frappe
from frappe.utils import now_datetime, get_datetime, nowdate
from angola_tax.alerts import adapters, model
from angola_tax.alerts.permissions import company_allowed, source_allowed

@contextmanager
def internal_write():
    old=frappe.flags.get('tax_alert_write')
    frappe.flags.tax_alert_write=True
    try: yield
    finally: frappe.flags.tax_alert_write=old

def event(doc,action,reason='',recipient=None):
    doc.append('history',dict(action=action,occurred_on=now_datetime(),actor=frappe.session.user,
        reason=reason,recipient=recipient,episode=doc.episode))

def enabled_rules(company=None):
    filters={'enabled':1}
    if company: filters['company']=company
    return [frappe.get_doc('Tax Alert Rule',n) for n in frappe.get_all('Tax Alert Rule',filters=filters,pluck='name',limit_page_length=0)]

def cutover(company):
    if not frappe.db.exists('DocType','Tax Alert Rule'):return False
    return bool(frappe.db.exists('Tax Alert Rule',{'company':company,'condition':'legacy_compliance','enabled':1}))

def reconcile_rule(rule, candidates):
    seen=set(); now=now_datetime()
    for data in candidates:
        seen.add(data['condition_key'])
        name=frappe.db.get_value('Tax Warning',{'condition_key':data['condition_key']},'name')
        if name:
            doc=frappe.get_doc('Tax Warning',name)
            old_status=doc.status
            status,action=model.transition(old_status,True)
            doc.update(data); doc.status=status; doc.last_seen=now
            if action=='Reopened':
                doc.episode+=1; doc.first_seen=now; doc.resolved_on=None
                doc.snooze_until=None; event(doc,action)
            elif old_status=='Snoozed' and (not doc.snooze_until or get_datetime(doc.snooze_until)<=now):
                doc.status='Open';doc.snooze_until=None;event(doc,'Snooze Ended')
        else:
            doc=frappe.get_doc(dict(doctype='Tax Warning',**data,status='Open',episode=1,first_seen=now,last_seen=now))
            event(doc,'Opened')
        with internal_write(): doc.save(ignore_permissions=True) if name else doc.insert(ignore_permissions=True)
    # Resolution happens only after complete successful evaluation, including dismissed/snoozed warnings.
    for name in frappe.get_all('Tax Warning',filters={'rule':rule.name,'status':['in',list(model.ACTIVE)]},pluck='name',limit_page_length=0):
        doc=frappe.get_doc('Tax Warning',name)
        if doc.condition_key not in seen:
            doc.status='Resolved';doc.resolved_on=now;doc.snooze_until=None
            event(doc,'Resolved','The source condition no longer applies.')
            with internal_write(): doc.save(ignore_permissions=True)
    frappe.db.set_value('Tax Alert Rule',rule.name,{'last_evaluated_on':now,'last_error':None},update_modified=False)

def covered_obligations(candidates):
    covered={}
    reasons={'payment_due':'payment','payment_internal_due':'payment',
        'submission_due':'delivery','rejected_submission':'delivery',
        'acknowledgement_evidence':'delivery','fiscal_variance':'correction',
        'payment_evidence':'confirmation'}
    for c in candidates:
        reason=reasons.get(c['condition'])
        if not reason:continue
        names=set()
        if c['source_doctype']=='Fiscal Obligation':names.add(c['source_name'])
        elif c['source_doctype']=='Tax Declaration':
            doc=frappe.get_doc('Tax Declaration',c['source_name'])
            names.update(r.fiscal_obligation for r in doc.obligations or [])
        elif c['source_doctype']=='Tax Payment':
            doc=frappe.get_doc('Tax Payment',c['source_name'])
            if doc.fiscal_obligation:names.add(doc.fiscal_obligation)
        for name in names:covered.setdefault(name,set()).add(reason)
    return covered

def evaluate_batch(rules):
    values={};errors={}
    for rule in rules:
        try: values[rule.name]=adapters.evaluate(rule)
        except Exception:
            errors[rule.name]=frappe.get_traceback()
    # The bridge warning is a fallback, not a second warning for a specific fiscal action.
    specific=[c for rule in rules if rule.condition!='legacy_compliance' for c in values.get(rule.name,[]) if c['kind']=='Alert']
    covered=covered_obligations(specific)
    for rule in rules:
        if rule.condition=='legacy_compliance' and rule.name in values:
            # On reader failure preserve bridge coverage; do not hide it based on partial results.
            if not errors: values[rule.name]=[c for c in values[rule.name] if not set(json.loads(c['facts_json']).get('reasons') or ['general']).issubset(covered.get(c['source_name'],set()))]
    return values,errors

def recipients(warning, rule, escalation=False):
    users=set()
    if escalation:
        if warning.escalation_user: users.add(warning.escalation_user)
    else:
        if warning.responsible_user: users.add(warning.responsible_user)
        if rule.responsible_role:
            users.update(frappe.get_all('Has Role',filters={'role':rule.responsible_role,'parenttype':'User'},pluck='parent',limit_page_length=0))
    result=[]
    for user in sorted(users):
        enabled,typ=frappe.db.get_value('User',user,['enabled','user_type']) or (0,None)
        if model.resolve_recipient_access(company_allowed(warning.company,user),
                source_allowed(warning.source_doctype,warning.source_name,warning.company,user),enabled,typ):
            # They also need read access to the warning itself for its notification link.
            if frappe.has_permission('Tax Warning','read',doc=warning,user=user): result.append(user)
    return result

def dispatch(doc,rule):
    now=now_datetime()
    if not rule.notify_in_app or doc.kind!='Alert' or doc.status not in model.ACTIVE or model.suppressed(doc.status,doc.snooze_until,now): return
    escalation_due=model.dispatch_slot(get_datetime(doc.first_seen),now,float(rule.recurrence_hours or 0),float(rule.escalation_hours or 0),True) is not None
    escalation_users=set(recipients(doc,rule,True)) if escalation_due else set()
    for escalation in (False,True):
        slot=model.dispatch_slot(get_datetime(doc.first_seen),now,float(rule.recurrence_hours or 0),float(rule.escalation_hours or 0),escalation)
        if slot is None: continue
        stage='Escalation' if escalation else 'Reminder'
        for user in recipients(doc,rule,escalation):
            # Never send escalation plus reminder to the same user in the same pass.
            if not escalation and user in escalation_users: continue
            if slot==0: stage='Escalation' if escalation else 'Initial'
            key=model.delivery_key(doc.name,doc.episode,user,stage,slot)
            if frappe.db.exists('Tax Warning Delivery',key): continue
            # Missed slots are not replayed: one current reminder replaces a backlog.
            previous=frappe.get_all('Tax Warning Delivery',filters={'warning':doc.name,'episode':doc.episode,
                'recipient':user,'stage':['in',['Escalation'] if escalation else ['Initial','Reminder']]},
                fields=['creation'],order_by='creation desc',limit_page_length=1)
            if previous and (float(rule.recurrence_hours or 0)<=0 or
                    (now-get_datetime(previous[0].creation)).total_seconds()<float(rule.recurrence_hours)*3600): continue
            with internal_write():
                delivery=frappe.get_doc(dict(doctype='Tax Warning Delivery',name=key,company=doc.company,
                    warning=doc.name,recipient=user,episode=doc.episode,stage=stage,slot=slot,channel='In App'))
                delivery.insert(ignore_permissions=True)
                from angola_tax.alerts.channels import send_in_app
                log=send_in_app(doc,user,escalation)
                delivery.notification_log=log.name;delivery.save(ignore_permissions=True)
                event(doc,'Escalated' if escalation else 'Notified',recipient=user)
                doc.save(ignore_permissions=True)

def reconcile(company=None):
    rules=enabled_rules(company)
    if not rules: return {'rules':0,'failures':0}
    values,errors=evaluate_batch(rules)
    for rule in rules:
        if rule.name in errors:
            frappe.db.set_value('Tax Alert Rule',rule.name,'last_error','Evaluation failed; existing warnings retained. See Error Log.',update_modified=False)
            frappe.log_error(errors[rule.name],'Tax warning evaluation: '+rule.name)
            continue
        savepoint='alerts_'+hashlib.sha256(rule.name.encode()).hexdigest()[:16]
        frappe.db.savepoint(savepoint)
        try: reconcile_rule(rule,values[rule.name])
        except Exception:
            frappe.db.rollback(save_point=savepoint)
            frappe.log_error(frappe.get_traceback(),'Tax warning reconciliation: '+rule.name)
            errors[rule.name]='Reconciliation failed'
            frappe.db.set_value('Tax Alert Rule',rule.name,'last_error','Reconciliation failed; existing warnings retained. See Error Log.',update_modified=False)
    for rule in rules:
        if rule.name in errors: continue
        for name in frappe.get_all('Tax Warning',filters={'rule':rule.name,'status':['in',list(model.ACTIVE)]},pluck='name',limit_page_length=0):
            savepoint='deliver_'+hashlib.sha256(name.encode()).hexdigest()[:16]
            frappe.db.savepoint(savepoint)
            try: dispatch(frappe.get_doc('Tax Warning',name),rule)
            except Exception:
                frappe.db.rollback(save_point=savepoint)
                frappe.log_error(frappe.get_traceback(),'Tax warning in-app delivery: '+name)
                frappe.db.set_value('Tax Alert Rule',rule.name,'last_error','Warning delivery failed; it will retry on the next cycle. See Error Log.',update_modified=False)
    return {'rules':len(rules),'failures':len(errors)}

def scheduled():
    if not frappe.db.exists('DocType','Tax Alert Rule'): return
    # Global lock protects all stable identities, rule reconciliation and UI actions.
    try:
        with frappe.cache().lock('angola_tax_alerts:'+frappe.local.site,timeout=1800,blocking_timeout=0):
            reconcile()
            frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(),'Tax warning scheduler; retry next cycle')

def source_event(doc,method=None):
    if not frappe.db.exists('DocType','Tax Alert Rule') or not frappe.db.exists('Tax Alert Rule',{'enabled':1}): return
    company=adapters.company_of(doc)
    if company and cutover(company):
        # Queue only after source commit. The hourly sweep also recovers missed events.
        frappe.enqueue('angola_tax.alerts.service.scheduled',queue='long',enqueue_after_commit=True)

def legacy_notifications():
    """Preserve the old ToDo channel only for companies not migrated to new warnings."""
    for row in frappe.get_all('Fiscal Obligation',filters={'docstatus':['!=',2],'compliance_alert':1,
            'status':['not in',['Closed','Cancelled']]},fields=['name','company','tax_name','compliance_status',
            'compliance_message','responsible_user','reviewer'],limit_page_length=0):
        if cutover(row.company): continue
        users={row.responsible_user}
        if row.compliance_status=='Overdue': users.add(row.reviewer)
        for user in users-{None,''}:
            if not source_allowed('Fiscal Obligation',row.name,row.company,user): continue
            if not frappe.db.get_value('User',user,'enabled'): continue
            if frappe.db.exists('ToDo',{'reference_type':'Fiscal Obligation','reference_name':row.name,'allocated_to':user,'status':'Open'}): continue
            frappe.get_doc({'doctype':'ToDo','allocated_to':user,'description':
                f'{row.tax_name} - {row.company}: {row.compliance_message or row.compliance_status}',
                'reference_type':'Fiscal Obligation','reference_name':row.name,
                'priority':'High' if row.compliance_status=='Overdue' else 'Medium'}).insert(ignore_permissions=True)
    # Scheduler owns its transaction; direct bench execution commits via Frappe CLI.

@frappe.whitelist()
def preview(rule=None,company=None,include_disabled=1):
    if not any(r in frappe.get_roles() for r in ('System Manager','Tax Alert Manager','Tax Preparer','Tax Approver','Tax Submission User')):
        frappe.throw('Fiscal warning access is required.',frappe.PermissionError)
    filters={}
    if company:
        if not company_allowed(company): frappe.throw('Company access is required.',frappe.PermissionError)
        filters['company']=company
    if rule: filters['name']=rule
    if not int(include_disabled): filters['enabled']=1
    rules=[frappe.get_doc('Tax Alert Rule',n) for n in frappe.get_list('Tax Alert Rule',filters=filters,pluck='name',limit_page_length=0)]
    values,errors=evaluate_batch(rules)
    candidates=[c for rs in values.values() for c in rs if source_allowed(c['source_doctype'],c['source_name'],c['company'])]
    return {'warnings':candidates,'errors':[{'rule':n,'message':'Could not evaluate this rule; inspect its source configuration or administrator error log.'} for n in errors],
        'notifications_sent':0,'documents_changed':0,'rule_count':len(rules)}

@frappe.whitelist()
def act(name,action,reason='',until=None):
    if action not in ('Acknowledge','Snooze','Dismiss','Resume'): frappe.throw('Unsupported warning action.')
    if action in ('Snooze','Dismiss') and not str(reason).strip(): frappe.throw('Record a reason.')
    with frappe.cache().lock('angola_tax_alerts:'+frappe.local.site,timeout=1800,blocking_timeout=5):
        doc=frappe.get_doc('Tax Warning',name);doc.check_permission('read')
        if doc.kind!='Alert' or doc.status=='Resolved': frappe.throw('This warning has no active acknowledgement action.')
        roles=frappe.get_roles()
        allowed=(frappe.session.user==doc.responsible_user or doc.responsible_role in roles or
            any(r in roles for r in ('System Manager','Tax Alert Manager')))
        if not allowed: frappe.throw('Only the responsible person, responsible role or warning manager may act.',frappe.PermissionError)
        if action=='Snooze':
            if not until or get_datetime(until)<=now_datetime(): frappe.throw('Choose a future reminder time.')
            doc.snooze_until=until
        else: doc.snooze_until=None
        doc.status={'Acknowledge':'Acknowledged','Snooze':'Snoozed','Dismiss':'Dismissed','Resume':'Open'}[action]
        event(doc,action,reason)
        with internal_write():doc.save(ignore_permissions=True)
        return doc.status

@frappe.whitelist()
def summary(company=None):
    filters={'status':['in',list(model.ACTIVE)]}
    if company: filters['company']=company
    rows=frappe.get_list('Tax Warning',filters=filters,fields=['kind','severity','status'],limit_page_length=0)
    counts={'alerts':0,'notices':0,'critical':0,'snoozed':0,'dismissed':0}
    for row in rows:
        counts['alerts' if row.kind=='Alert' else 'notices']+=1
        if row.severity=='Critical':counts['critical']+=1
        if row.status=='Snoozed':counts['snoozed']+=1
        if row.status=='Dismissed':counts['dismissed']+=1
    rules=frappe.get_list('Tax Alert Rule',filters={'enabled':1,**({'company':company} if company else {})},fields=['name','last_evaluated_on','last_error'],limit_page_length=0)
    return {'counts':counts,'rules':rules}

def require_manager():
    if not any(r in frappe.get_roles() for r in ('System Manager','Tax Alert Manager')):
        frappe.throw('Warning manager access is required.',frappe.PermissionError)

@frappe.whitelist()
def legacy_tasks(company):
    require_manager()
    if not company_allowed(company):frappe.throw('Company access required.',frappe.PermissionError)
    result=[]
    for name in frappe.get_list('ToDo',filters={'reference_type':'Fiscal Obligation','status':'Open'},pluck='name',limit_page_length=0):
        todo=frappe.get_doc('ToDo',name)
        if not source_allowed('Fiscal Obligation',todo.reference_name,company):continue
        replacements=frappe.get_list('Tax Warning',filters={'company':company,'status':['in',list(model.ACTIVE)]},fields=['name','source_doctype','source_name'],limit_page_length=0)
        warning_names=[]
        for warning in replacements:
            if warning.source_doctype=='Fiscal Obligation' and warning.source_name==todo.reference_name:warning_names.append(warning.name)
            elif warning.source_doctype=='Tax Declaration':
                declaration=frappe.get_doc('Tax Declaration',warning.source_name)
                if any(r.fiscal_obligation==todo.reference_name for r in declaration.obligations or []):warning_names.append(warning.name)
        result.append({'name':todo.name,'allocated_to':todo.allocated_to,'description':todo.description,
            'fiscal_obligation':todo.reference_name,'replacement_warnings':warning_names})
    return result

@frappe.whitelist()
def retire_legacy_tasks(company,todo_names,reason):
    require_manager()
    if not str(reason).strip():frappe.throw('Record a migration reason.')
    if not cutover(company):frappe.throw('Enable the company fiscal replacement rule before retiring legacy tasks.')
    names=json.loads(todo_names) if isinstance(todo_names,str) else todo_names
    if not isinstance(names,list) or not names:frappe.throw('Select the legacy tasks to retire.')
    with frappe.cache().lock('angola_tax_alerts:'+frappe.local.site,timeout=1800,blocking_timeout=5):
        candidates={r['name']:r for r in legacy_tasks(company)}
        for name in names:
            if name not in candidates or not candidates[name]['replacement_warnings']:
                frappe.throw('Every selected task must have a readable replacement warning. Nothing has been retired.')
        for name in names:
            todo=frappe.get_doc('ToDo',name)
            todo.check_permission('write')
        for name in names:
            row=candidates[name];todo=frappe.get_doc('ToDo',name)
            todo.status='Cancelled';todo.save()
            todo.add_comment('Comment','Replaced by fiscal warnings '+', '.join(row['replacement_warnings'])+'. Reason: '+reason)
            for warning_name in row['replacement_warnings']:
                warning=frappe.get_doc('Tax Warning',warning_name)
                event(warning,'Legacy Task Retired',reason+'; previous task: '+name)
                with internal_write():warning.save(ignore_permissions=True)
    return {'retired_tasks':names,'fiscal_documents_changed':0}
