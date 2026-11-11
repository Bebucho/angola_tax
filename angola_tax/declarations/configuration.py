"""Extend the installed Industrial Tax deadline override without replacing it."""
import frappe
from frappe.model.document import Document
from frappe.utils import getdate
from angola_tax.industrial_tax.adapters import IndustrialDeadlineConfiguration
from angola_tax.declarations.model import deadline

class DeclarationDeadlineConfiguration(IndustrialDeadlineConfiguration):
    def validate(self):
        if self.get('declaration_purpose') != 'Declaration':
            return super().validate()
        if not self.declaration_type or not self.company or not self.effective_from:
            frappe.throw('Declaration configuration needs Company, Declaration Type and Effective From.')
        # Legacy monetary deadline selectors filter by tax_type. Keep declaration rules
        # out of that candidate set; the filing catalogue owns the tax family identity.
        self.tax_type = None
        typ = frappe.get_doc('Tax Declaration Type', self.declaration_type)
        if self.declaration_applicability == 'Required' and typ.verification_state != 'Verified':
            frappe.throw('Unverified filing types cannot be configured as Required.')
        if not self.declaration_reason:
            frappe.throw('Record the legal applicability/deadline basis.')
        if self.effective_to and getdate(self.effective_to) < getdate(self.effective_from):
            frappe.throw('Effective To precedes Effective From.')
        rules = self.get('deadline_rules') or []
        if len(rules) != 1 or rules[0].deadline_type != 'Submission':
            frappe.throw('Declaration configuration requires exactly one Submission deadline.')
        rule = rules[0]
        if rule.periodicity not in ('Monthly', 'Quarterly', 'Annual', 'Transaction'):
            frappe.throw('Select declaration periodicity.')
        if rule.declaration_working_day and not self.get('holiday_list'):
            frappe.throw('Working-day rules require an Angola Holiday List.')
        if rule.declaration_anchor == 'Event Date' and rule.periodicity != 'Transaction':
            frappe.throw('Event Date requires Transaction periodicity.')
        if rule.declaration_anchor == 'Fixed Annual Date' and rule.periodicity != 'Annual':
            frappe.throw('Fixed Annual Date requires Annual periodicity.')
        if self.declaration_applicability in ('Required', 'Optional'):
            deadline('2026-12-31', rule.as_dict(), holidays(self))
        others = frappe.get_all('Tax Deadline Configuration', filters={
            'company': self.company, 'declaration_purpose': 'Declaration',
            'declaration_type': self.declaration_type, 'active': 1,
            'docstatus': 1, 'name': ['!=', self.name or '']},
            fields=['name', 'effective_from', 'effective_to'])
        for row in others:
            if getdate(self.effective_from) <= getdate(row.effective_to or '9999-12-31') and getdate(row.effective_from) <= getdate(self.effective_to or '9999-12-31'):
                frappe.throw(f'Overlapping declaration configuration: {row.name}.')

    def before_cancel(self):
        if self.get('declaration_purpose') == 'Declaration' and frappe.db.exists('Tax Declaration', {'deadline_configuration': self.name}):
            frappe.throw('Referenced deadline configuration must be superseded with a new effective period, not cancelled.')

def holidays(config):
    if not config.get('holiday_list'): return []
    return frappe.get_all('Holiday', filters={'parent': config.holiday_list}, pluck='holiday_date', limit_page_length=0)

def resolve(config, start, end, event_date=None):
    if config.docstatus != 1 or not config.active or config.declaration_purpose != 'Declaration':
        frappe.throw('Select an active submitted declaration configuration.')
    when = getdate(event_date or end)
    if when < getdate(config.effective_from) or (config.effective_to and when > getdate(config.effective_to)):
        frappe.throw('Configuration is not effective for the source period/event.')
    rule = config.deadline_rules[0]
    anchor = event_date if rule.declaration_anchor == 'Event Date' else end
    if not anchor: frappe.throw('The declaration deadline requires an event date.')
    legal = deadline(anchor, rule.as_dict(), holidays(config))
    return {'legal_due_date': legal, 'effective_due_date': legal,
            'applicability': config.declaration_applicability,
            'rule_snapshot': config.as_dict(), 'periodicity': rule.periodicity}

class TaxDeclarationType(Document):
    def validate(self):
        if self.verification_state == 'Verified' and (not self.legal_sources or not self.schema_version):
            frappe.throw('Verified filing needs legal sources and an explicit schema version.')
        if self.has_value_changed('adapter_key') and frappe.db.exists('Tax Declaration', {'declaration_type': self.name, 'docstatus': 1}):
            frappe.throw('Approved packages reference this adapter. Introduce a versioned filing type to change it.')
