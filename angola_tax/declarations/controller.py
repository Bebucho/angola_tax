import json
import uuid
import frappe
from frappe.model.document import Document
from frappe.utils import getdate, now_datetime, nowdate
from angola_tax.declarations import model, providers
from angola_tax.declarations.configuration import resolve

FROZEN_EXCLUDED = {'modified', 'modified_by', '_user_tags', '_comments', '_assign', '_liked_by',
    'delivery_state', 'freshness', 'last_checked_on', 'is_overdue', 'days_overdue',
    'effective_accepted_version', 'chain_state', 'is_latest_version', 'events'}

def require_role(*roles):
    if frappe.session.user != 'Administrator' and not set(frappe.get_roles()).intersection(set(roles) | {'System Manager'}):
        frappe.throw('Your role is not authorised for this declaration action.', frappe.PermissionError)

def event_facts(rows):
    omitted = {'creation', 'modified', 'modified_by', 'owner', 'parent', 'parenttype', 'parentfield', 'docstatus'}
    return [{k: v for k, v in r.as_dict().items() if k not in omitted} for r in rows or []]

def file_bytes(url, declaration):
    # Frappe may register the same physical URL against several Attach fields.
    files = frappe.get_all('File', filters={'file_url': url,
        'attached_to_doctype': 'Tax Declaration', 'attached_to_name': declaration.name},
        pluck='name', order_by='name', limit_page_length=0)
    if not files:
        frappe.throw('Select a private File attached to this declaration.')
    payload = None
    for name in files:
        file = frappe.get_doc('File', name)
        file.check_permission('read')
        if not file.is_private:
            frappe.throw('Declaration evidence and packages must be private.')
        content = file.get_content()
        if isinstance(content, str):
            content = content.encode('utf-8')
        if not isinstance(content, bytes):
            frappe.throw('Declaration file content is not readable.')
        if payload is None:
            payload = content
        elif payload != content:
            frappe.throw('File aliases disagree on declaration content.')
    return payload

def bytes_hash(content):
    import hashlib
    return hashlib.sha256(content).hexdigest()

def store_file(doc, label, content):
    existing = frappe.db.get_value('File', {'file_name': label, 'attached_to_doctype':doc.doctype,
        'attached_to_name':doc.name, 'is_private':1}, 'file_url')
    if existing:
        if bytes_hash(file_bytes(existing,doc)) != bytes_hash(content): frappe.throw('Existing archive file checksum mismatch.')
        return existing
    file = frappe.get_doc({'doctype': 'File', 'file_name': label, 'content': content,
        'is_private': 1, 'attached_to_doctype': doc.doctype, 'attached_to_name': doc.name})
    file.insert()
    return file.file_url

def package(doc, collected, config):
    typ = frappe.get_doc('Tax Declaration Type', doc.declaration_type)
    rule = resolve(config, doc.period_start, doc.period_end, doc.event_date)
    return {'format': 'Metagest Declaration Snapshot v1', 'engine_version': model.VERSION,
        'company': doc.company, 'company_nif': collected['manifest']['company']['tax_id'],
        'filing': doc.declaration_type, 'adapter': typ.adapter_key, 'schema': typ.schema_version,
        'period_start': str(doc.period_start)[:10], 'period_end': str(doc.period_end)[:10],
        'scope': doc.statutory_scope or 'Company', 'event_reference': doc.event_reference or '',
        'kind': doc.submission_kind, 'revision': doc.revision,
        'replaces_reference': doc.replaces_reference or '',
        'configuration': providers.semantic(rule['rule_snapshot']),
        'deadline': {'legal_due_date':rule['legal_due_date'],
            'effective_due_date':str(doc.override_due_date)[:10] if doc.override_due_date else rule['legal_due_date'],
            'penalty_free_until':str(doc.penalty_free_until)[:10] if doc.penalty_free_until else None,
            'override_reference':doc.override_reference or '',
            'override_evidence_hash':bytes_hash(file_bytes(doc.override_evidence,doc)) if doc.override_evidence else None},
        'completion_rule': typ.completion_rule, 'values': collected['values'],
        'manifest_hash': collected['manifest_hash']}

class TaxDeclaration(Document):
    def autoname(self):
        if not self.revision: self.revision = 1
        key = model.occurrence(self.company, self.declaration_type, self.period_start,
            self.period_end, self.statutory_scope or 'Company', self.event_reference or '')
        self.name = 'DECL-' + key[:24] + '-' + str(self.revision)

    def validate(self):
        if getdate(self.period_end) < getdate(self.period_start):
            frappe.throw('Period End precedes Period Start.')
        if self.submission_kind == 'Original':
            if self.previous_version or self.revision != 1:
                frappe.throw('Original declaration must be revision 1 without a predecessor.')
            self.root_declaration = self.name
        else:
            previous = frappe.get_doc('Tax Declaration', self.previous_version)
            previous.check_permission('read')
            if previous.occurrence_key != model.occurrence(self.company, self.declaration_type,
                    self.period_start, self.period_end, self.statutory_scope or 'Company', self.event_reference or ''):
                frappe.throw('Revision must preserve company, filing, scope and period.')
            if self.root_declaration != previous.root_declaration or self.revision != previous.revision + 1:
                frappe.throw('Revision lineage is invalid.')
            if not self.correction_reason: frappe.throw('Record the correction reason.')
            typ = frappe.get_doc('Tax Declaration Type', self.declaration_type)
            allowed = typ.supports_replacement if self.submission_kind == 'Replacement' else typ.supports_corrective
            if not allowed: frappe.throw('This filing does not support the selected correction mode.')
        self.occurrence_key = model.occurrence(self.company, self.declaration_type, self.period_start,
            self.period_end, self.statutory_scope or 'Company', self.event_reference or '')
        self.original_key = self.occurrence_key if self.submission_kind == 'Original' else None
        self.revision_key = model.digest([self.occurrence_key, self.revision])
        if not self.root_declaration: self.root_declaration = self.name
        config = frappe.get_doc('Tax Deadline Configuration', self.deadline_configuration)
        config.check_permission('read')
        if config.company != self.company or config.declaration_type != self.declaration_type:
            frappe.throw('Deadline configuration does not match Company and filing.')
        if self.docstatus == 0:
            rule = resolve(config, self.period_start, self.period_end, self.event_date)
            self.applicability = rule['applicability']
            self.legal_due_date = rule['legal_due_date']
            # One documented, occurrence-specific override; never overwrites legal_due_date.
            self.effective_due_date = self.override_due_date or self.legal_due_date
            if self.override_due_date and (not self.override_reference or not self.override_evidence):
                frappe.throw('Official deadline override needs reference and private evidence.')
            if self.penalty_free_until and (not self.override_reference or not self.override_evidence):
                frappe.throw('A penalty-free concession requires its own official reference and evidence.')
        links = []
        for row in self.obligations or []:
            obligation = frappe.get_doc('Fiscal Obligation', row.fiscal_obligation)
            obligation.check_permission('read')
            if obligation.docstatus == 2: frappe.throw('Cannot link a cancelled fiscal obligation.')
            links.append(dict(fiscal_obligation=obligation.name, relationship=row.relationship, company=obligation.company))
        try: model.validate_links(self.company, links)
        except ValueError as exc: frappe.throw(str(exc))
        self.delivery_state = model.delivery_state([r.as_dict() for r in self.events or []])
        self.compute_overdue()
        if self.is_new() and self.events:
            frappe.throw('Create the declaration before recording any package or delivery events.')
        old = self.get_doc_before_save()
        if self.docstatus == 0 and not frappe.flags.get('declaration_action'):
            require_role('Tax Preparer', 'Tax Approver')
        if old and not frappe.flags.get('declaration_action'):
            if model.canonical(event_facts(self.events)) != model.canonical(event_facts(old.events)):
                frappe.throw('Use declaration actions to append evidence events.')
            if self.preparation_state != old.preparation_state:
                frappe.throw('Use Prepare/Approve to change preparation status.')

    def compute_overdue(self):
        typ = frappe.get_doc('Tax Declaration Type', self.declaration_type)
        completion = typ.completion_rule
        if self.get('snapshot_file') and self.docstatus == 1:
            completion = json.loads(file_bytes(self.snapshot_file, self)).get('completion_rule', completion)
        self.is_overdue = int(model.overdue(self.applicability == 'Required', self.effective_due_date,
            nowdate(), [r.as_dict() for r in self.events or []], completion))
        self.days_overdue = max((getdate(nowdate()) - getdate(self.effective_due_date)).days, 0) if self.is_overdue else 0

    def before_submit(self):
        require_role('Tax Approver')
        if self.applicability not in ('Required', 'Optional'):
            frappe.throw('Resolve filing applicability before approval.')
        typ = frappe.get_doc('Tax Declaration Type', self.declaration_type)
        if typ.verification_state != 'Verified': frappe.throw('Resolve the legal/schema verification before approval.')
        if self.preparation_state != 'Prepared': frappe.throw('Prepare the declaration package first.')
        self.verify_package()
        self.preparation_state = 'Approved'; self.approved_by = frappe.session.user
        self.approved_on = now_datetime()
        self.append_event('Approved', self.approved_on, event_key=model.digest([self.name, 'approved']))

    def on_submit(self):
        from angola_tax.declarations.service import refresh_chain
        refresh_chain(self.root_declaration)

    def before_update_after_submit(self):
        old = self.get_doc_before_save()
        if not old: frappe.throw('Cannot verify approved declaration history.')
        for field in self.meta.fields:
            key = field.fieldname
            if key in FROZEN_EXCLUDED or field.fieldtype in ('Section Break', 'Column Break', 'Tab Break'): continue
            if model.canonical(providers.semantic(self.get(key))) != model.canonical(providers.semantic(old.get(key))):
                frappe.throw(f'Approved declaration field {key} is immutable. Create a revision.')
        previous_events = event_facts(old.events)
        current_events = event_facts(self.events)
        if model.canonical(current_events[:len(previous_events)]) != model.canonical(previous_events):
            frappe.throw('Existing declaration evidence cannot be edited or deleted.')
        if not frappe.flags.get('declaration_action'):
            frappe.throw('Use the controlled declaration actions after approval.')
        self.verify_files()

    def before_cancel(self): frappe.throw('Preserve approved declaration evidence. Create a linked revision.')
    def on_trash(self):
        if self.events or self.docstatus != 0:
            frappe.throw('Declaration history must be retained.')

    def append_event(self, action, occurred_on, event_key=None, **fields):
        key = event_key or uuid.uuid4().hex
        if any(r.event_key == key for r in self.events or []): return False
        self.append('events', dict(action=action, occurred_on=occurred_on, recorded_on=now_datetime(),
            actor=frappe.session.user, event_key=key, **fields))
        return True

    def verify_files(self):
        for field, hashfield in (('snapshot_file', 'snapshot_hash'), ('manifest_file', 'manifest_hash'), ('generated_file', 'generated_hash')):
            if not self.get(field) or bytes_hash(file_bytes(self.get(field), self)) != self.get(hashfield):
                frappe.throw(f'Declaration package integrity failed: {field}.')

    def verify_package(self):
        self.verify_files()
        collected = providers.collect(self)
        config = frappe.get_doc('Tax Deadline Configuration', self.deadline_configuration)
        expected = package(self, collected, config)
        frozen = json.loads(file_bytes(self.snapshot_file, self))
        if model.canonical(expected) != model.canonical(frozen):
            frappe.throw('Sources, filing or configuration changed since preparation. Prepare a new package/version.')
        if model.canonical(collected['manifest']) != file_bytes(self.manifest_file, self).decode():
            frappe.throw('Declaration source manifest changed.')
        self.freshness = 'Current'; self.last_checked_on = now_datetime()

    @frappe.whitelist()
    def prepare(self):
        require_role('Tax Preparer', 'Tax Approver'); self.check_permission('write')
        lock_and_reload(self)
        if self.docstatus != 0: frappe.throw('Prepare a Draft version; approved packages are immutable.')
        if self.is_new(): frappe.throw('Save the scheduled declaration first.')
        self.validate()
        typ = frappe.get_doc('Tax Declaration Type', self.declaration_type)
        if typ.verification_state != 'Verified' or self.applicability not in ('Required', 'Optional'):
            frappe.throw('Resolve legal/schema verification and applicability first.')
        collected = providers.collect(self)
        if collected['source_obligation'] and not any(r.fiscal_obligation == collected['source_obligation'] for r in self.obligations or []):
            self.append('obligations', {'fiscal_obligation': collected['source_obligation'], 'relationship': 'Reported Liability'})
        snapshot = package(self, collected, frappe.get_doc('Tax Deadline Configuration', self.deadline_configuration))
        payload = model.canonical(snapshot).encode(); manifest = model.canonical(collected['manifest']).encode()
        ext, generated = providers.export(snapshot)
        self.snapshot_file = store_file(self, f'{self.name}_snapshot_{bytes_hash(payload)[:12]}.json', payload)
        self.manifest_file = store_file(self, f'{self.name}_sources_{bytes_hash(manifest)[:12]}.json', manifest)
        label = 'AGT_Map' if ext == 'xlsx' else 'Portal_Entry_Summary'
        self.generated_file = store_file(self, f'{self.name}_{label}_{bytes_hash(generated)[:12]}.{ext}', generated)
        self.snapshot_hash, self.manifest_hash, self.generated_hash = map(bytes_hash, (payload, manifest, generated))
        self.preparation_state = 'Prepared'; self.prepared_by = frappe.session.user; self.prepared_on = now_datetime()
        self.freshness = 'Current'; self.last_checked_on = now_datetime()
        self.append_event('File Generated', self.prepared_on, evidence=self.generated_file,
            evidence_hash=self.generated_hash, reason=label)
        controlled_save(self)
        return {'file_url': self.generated_file, 'state': self.preparation_state, 'format': label}

    @frappe.whitelist()
    def check_sources(self):
        self.check_permission('read'); require_role('Tax Preparer', 'Tax Approver', 'Tax Submission User')
        lock_and_reload(self)
        try:
            self.verify_package(); self.freshness = 'Current'
        except frappe.PermissionError: raise
        except Exception as exc:
            self.freshness = 'Sources Changed'
            self.append_event('Source Changed', now_datetime(), reason=str(exc)[:2000],
                event_key=model.digest([self.name, 'changed', str(exc)[:2000]]))
        self.last_checked_on = now_datetime(); controlled_save(self)
        return self.freshness

    @frappe.whitelist()
    def record_delivery(self, action, occurred_on, evidence, external_reference='', attempt_id='', reason=''):
        require_role('Tax Submission User'); self.check_permission('read')
        lock_and_reload(self)
        if self.docstatus != 1: frappe.throw('Approve the frozen package before recording external delivery.')
        if getdate(occurred_on) > getdate(nowdate()): frappe.throw('Actual delivery date cannot be in the future.')
        content = file_bytes(evidence, self)
        if evidence in (self.generated_file,self.snapshot_file,self.manifest_file) or bytes_hash(content) in (self.generated_hash,self.snapshot_hash,self.manifest_hash):
            frappe.throw('A generated package is not submission evidence. Attach the actual portal receipt or response.')
        key = model.digest([self.name, action, attempt_id, external_reference, str(occurred_on), bytes_hash(content)])
        if any(r.event_key == key for r in self.events or []): return self.delivery_state
        attempts = {r.attempt_id for r in self.events or [] if r.action == 'Submission'}
        if action == 'Submission':
            if not external_reference or not attempt_id:
                frappe.throw('Submission needs a portal reference and a stable attempt ID.')
            if self.submission_kind != 'Original' and not self.replaces_reference:
                frappe.throw('Record the external declaration being replaced/corrected.')
        try: model.validate_delivery(action, self.delivery_state, occurred_on, evidence, attempt_id, attempts)
        except ValueError as exc: frappe.throw(str(exc))
        if action == 'Submission' and attempt_id in attempts:
            frappe.throw('Attempt ID already exists with different evidence. Append a correction event.')
        if action != 'Submission':
            submitted = next(r for r in self.events if r.action == 'Submission' and r.attempt_id == attempt_id)
            if str(occurred_on) < str(submitted.occurred_on): frappe.throw('Response precedes submission.')
            if action == 'Acknowledged' and any(r.attempt_id==attempt_id and r.action=='Rejected' for r in self.events):
                frappe.throw('A rejected attempt cannot revert to receipt-only status. Record its authoritative correction/outcome.')
        # Preserve real portal delivery even if its source changed before evidence was recorded.
        try: self.verify_package(); self.freshness = 'Current'
        except frappe.PermissionError: raise
        except Exception as exc:
            self.freshness = 'Sources Changed'
            self.append_event('Source Changed', now_datetime(), reason=str(exc)[:2000])
        self.append_event(action, occurred_on, event_key=key, attempt_id=attempt_id,
            external_reference=external_reference, evidence=evidence, evidence_hash=bytes_hash(content), reason=reason)
        self.delivery_state = model.delivery_state([r.as_dict() for r in self.events])
        self.compute_overdue(); controlled_save(self)
        from angola_tax.declarations.service import refresh_chain
        refresh_chain(self.root_declaration)
        return self.delivery_state

    @frappe.whitelist()
    def create_revision(self, kind='Replacement', reason=''):
        require_role('Tax Preparer', 'Tax Approver'); self.check_permission('read')
        lock_and_reload(self)
        frappe.db.sql('select name from `tabTax Declaration` where name=%s for update', self.root_declaration)
        if not reason or kind not in ('Replacement', 'Corrective'): frappe.throw('Select a revision kind and reason.')
        latest = frappe.get_all('Tax Declaration', filters={'root_declaration': self.root_declaration},
            fields=['name', 'revision'], order_by='revision desc', limit_page_length=1)[0]
        if latest.name != self.name: frappe.throw('Create the revision from the latest chain version.')
        doc = frappe.get_doc({'doctype': 'Tax Declaration', 'company': self.company,
            'declaration_type': self.declaration_type, 'period_start': self.period_start,
            'period_end': self.period_end, 'fiscal_year': self.fiscal_year,
            'statutory_scope': self.statutory_scope, 'event_reference': self.event_reference,
            'event_date': self.event_date, 'deadline_configuration': self.deadline_configuration,
            'responsible_user': self.responsible_user, 'reviewer': self.reviewer,
            'source_doctype': self.source_doctype, 'source_name': self.source_name,
            'root_declaration': self.root_declaration, 'previous_version': self.name,
            'revision': self.revision + 1, 'submission_kind': kind, 'correction_reason': reason,
            'replaces_reference': next((r.external_reference for r in reversed(self.events or []) if r.action == 'Submission'), self.replaces_reference),
            'obligations': [{'fiscal_obligation': r.fiscal_obligation, 'relationship': r.relationship} for r in self.obligations or []]})
        doc.insert()
        from angola_tax.declarations.service import refresh_chain
        refresh_chain(self.root_declaration)
        return doc.name

def controlled_save(doc):
    old = frappe.flags.get('declaration_action'); frappe.flags.declaration_action = True
    try:
        # Role authorisation is checked by each action. Delivery-only role needs no generic write rights.
        doc.save(ignore_permissions=True)
    finally: frappe.flags.declaration_action = old

def lock_and_reload(doc):
    frappe.db.sql('select name from `tabTax Declaration` where name=%s for update', doc.name)
    doc.reload()
