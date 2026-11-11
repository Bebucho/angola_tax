"""Only in-app delivery. Direct persistence avoids Frappe's email-enabled insert hooks."""
from html import escape
import uuid
import frappe
from frappe.utils import now_datetime

def send_in_app(warning,user,escalation=False):
    now=now_datetime()
    log=frappe.get_doc({'doctype':'Notification Log','name':uuid.uuid4().hex,
        'for_user':user,'from_user':frappe.session.user,'type':'Alert','read':0,
        'subject':escape(('Escalation: ' if escalation else '')+warning.title),
        'document_type':'Tax Warning','document_name':warning.name,
        'email_content':escape(warning.explanation)+'<br>'+escape(warning.action_needed),
        'creation':now,'modified':now,'owner':frappe.session.user,'modified_by':frappe.session.user})
    # No controller after_insert, Notification rules or email queue path is executed.
    log.db_insert()
    frappe.publish_realtime('notification',{'name':log.name},user=user,after_commit=True)
    return log

def send(channel,*args,**kwargs):
    if channel=='In App': return send_in_app(*args,**kwargs)
    raise ValueError('This notification channel is not enabled.')
