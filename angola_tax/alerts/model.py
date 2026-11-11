"""Warning lifecycle only. No tax calculation or deadline creation."""
import hashlib
import json
from datetime import date, datetime, timedelta

ACTIVE = {'Open', 'Acknowledged', 'Snoozed', 'Dismissed'}

def identity(company, process, condition, source_doctype, source_name, detail=''):
    return hashlib.sha256(json.dumps([company, process, condition, source_doctype,
        source_name, detail], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def due_window(due, today, advance):
    if not due: return False
    return (date.fromisoformat(str(due)[:10]) - date.fromisoformat(str(today)[:10])).days <= int(advance or 0)

def transition(status, present):
    if present and status == 'Resolved': return 'Open', 'Reopened'
    if present and not status: return 'Open', 'Opened'
    if not present and status in ACTIVE: return 'Resolved', 'Resolved'
    return status, None

def delivery_key(warning, episode, recipient, stage, slot):
    return identity('', 'delivery', stage, warning, str(episode), recipient + ':' + str(slot))

def dispatch_slot(first_seen, now, recurrence_hours, escalation_hours, escalation=False):
    elapsed = (now - first_seen).total_seconds()/3600
    if escalation:
        if escalation_hours <= 0 or elapsed < escalation_hours: return None
        elapsed -= escalation_hours
    if recurrence_hours <= 0: return 0
    return max(0, int(elapsed // recurrence_hours))

def suppressed(status, snooze_until, now):
    return status == 'Dismissed' or (status == 'Snoozed' and snooze_until and
        datetime.fromisoformat(str(snooze_until)) > now)

def resolve_recipient_access(company_allowed, document_allowed, enabled, user_type):
    return bool(company_allowed and document_allowed and enabled and user_type == 'System User')

def legacy_reasons(doc):
    """Read the installed compliance service's priority/flags; do not recalculate them."""
    if doc.get('is_submission_overdue'): return {'delivery'}
    overdue=set()
    if doc.get('is_filing_overdue'):overdue.add('delivery')
    if doc.get('is_overdue'):overdue.add('payment')
    if overdue:return overdue
    due=set()
    if doc.get('filing_status') in ('Due Today','Due Soon'):due.add('delivery')
    if doc.get('deadline_status') in ('Due Today','Due Soon'):due.add('payment')
    if due:return due
    if doc.get('correction_required'):return {'correction'}
    if doc.get('status')=='Paid' and not doc.get('fully_paid_on'):return {'confirmation'}
    return {'general'}
