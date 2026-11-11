"""Pure invariants shared by the Frappe services and regression tests."""
import calendar
import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

VERSION = '1.0.0'

def canonical(value):
    def encode(obj):
        if isinstance(obj, (date, datetime)): return obj.isoformat()
        if isinstance(obj, Decimal): return format(obj, 'f')
        raise TypeError(type(obj).__name__)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=encode)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def occurrence(company, filing, start, end, scope='Company', event=''):
    return digest([company, filing, str(start), str(end), scope, event])

def as_date(value):
    return date.fromisoformat(str(value)[:10])

def deadline(anchor, rule, holidays=()):
    anchor = as_date(anchor)
    mode = rule.get('declaration_anchor') or 'Period End'
    if mode == 'Fixed Annual Date':
        result = date(anchor.year + int(rule.get('declaration_year_offset') or 0),
                      int(rule['declaration_month']), int(rule['due_day']))
    else:
        offset = int(rule.get('due_month_offset') or 0)
        n = anchor.year * 12 + anchor.month - 1 + offset
        year, month = divmod(n, 12); month += 1
        last = calendar.monthrange(year, month)[1]
        if rule.get('use_end_of_month'):
            result = date(year, month, last)
        elif rule.get('declaration_day_offset') is not None and mode == 'Event Date':
            result = anchor + timedelta(days=int(rule.get('declaration_day_offset') or 0))
        else:
            day = int(rule.get('due_day') or 0)
            if not 1 <= day <= last: raise ValueError('Due day does not exist in the target month.')
            result = date(year, month, day)
    if rule.get('declaration_working_day'):
        blocked = {as_date(x) for x in holidays}
        direction = -1 if rule.get('use_end_of_month') else 1
        while result.weekday() >= 5 or result in blocked:
            result += timedelta(days=direction)
    return result.isoformat()

def validate_links(company, links):
    seen = set()
    for row in links:
        key = (row['fiscal_obligation'], row['relationship'])
        if key in seen: raise ValueError('Duplicate obligation relationship.')
        if row['company'] != company: raise ValueError('Cross-company obligation link.')
        seen.add(key)

def delivery_state(events):
    """Only explicit evidence changes delivery; generated/paid never do."""
    state = 'Not Submitted'
    for event in events:
        action = event.get('action')
        if action == 'Submission': state = 'Submitted'
        elif action in ('Acknowledged', 'Accepted', 'Rejected'): state = action
    return state

def validate_delivery(action, state, occurred_on, evidence, attempt, known_attempts):
    if action not in ('Submission', 'Acknowledged', 'Accepted', 'Rejected', 'Outcome Unknown', 'Evidence Correction'):
        raise ValueError('Unsupported delivery action.')
    if not occurred_on or not evidence: raise ValueError('Occurrence time and evidence are required.')
    if action != 'Submission' and attempt not in known_attempts:
        raise ValueError('Response must reference a recorded submission attempt.')
    if state == 'Accepted' and action not in ('Evidence Correction',):
        raise ValueError('Accepted delivery cannot be rewritten; create a revision.')

def effective_version(versions):
    accepted = [v for v in versions if v['delivery_state'] == 'Accepted']
    return max(accepted, key=lambda v: v['revision'])['name'] if accepted else None

def overdue(required, due, today, events, completion='Acknowledged'):
    if not required or not due or as_date(today) <= as_date(due): return False
    if completion == 'Accepted': done = {'Accepted'}
    else: done = {'Acknowledged', 'Accepted'}
    state = delivery_state(events)
    return state not in done
