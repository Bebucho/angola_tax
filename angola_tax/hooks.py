
app_name = "angola_tax"
app_title = "Angola Tax"
app_publisher = "Metagest"
app_description = "Angola tax compliance and fiscal obligation control"
app_email = "jose.sardinha@2ms.co.ao"
app_license = "gpl-3.0"

doc_events = {
    "Journal Entry": {
        "before_insert": "angola_tax.journal_entry_guard.block_tax_journal_reversal",
        "before_submit": "angola_tax.journal_entry_guard.block_tax_journal_reversal",
        "before_cancel": "angola_tax.journal_entry_guard.block_tax_journal_cancellation",
        "on_submit": "angola_tax.gl_ingestion.sync_journal_entry",
        "on_cancel": "angola_tax.gl_ingestion.cancel_journal_entry_sync",
    },
    "Payment Entry": {
        "on_submit": "angola_tax.gl_ingestion.sync_payment_entry",
        "on_cancel": "angola_tax.gl_ingestion.cancel_payment_entry_sync",
    },
}

scheduler_events = {
    "daily": [
        "angola_tax.gl_ingestion.sync_taxes_from_gl",
        "angola_tax.tasks.update_fiscal_obligation_statuses",
        "angola_tax.tasks.create_fiscal_obligation_notifications",
    ]
}



# IMPOSTO_INDUSTRIAL_ANNUAL_V1
from angola_tax.industrial_tax.hook_configuration import extend_hooks
extend_hooks(doc_events)
override_doctype_class = {
    "Tax Assessment": "angola_tax.industrial_tax.collection_adapters.IndustrialTaxAssessment",
    "Tax Settlement": "angola_tax.industrial_tax.collection_adapters.IndustrialTaxSettlement",
    "Fiscal Obligation": "angola_tax.industrial_tax.adapters.IndustrialFiscalObligation",
    "Tax Deadline Configuration": "angola_tax.industrial_tax.adapters.IndustrialDeadlineConfiguration",
}

# METAGEST_DECLARATIONS_V1
from angola_tax.declarations.hooks import extend_hooks as extend_declaration_hooks
extend_declaration_hooks(doc_events, scheduler_events, override_doctype_class)
doctype_js = globals().get('doctype_js', {})
_declaration_config_js = "declarations/configuration_form.js"
_existing_config_js = doctype_js.get("Tax Deadline Configuration", [])
if isinstance(_existing_config_js, str):
    _existing_config_js = [_existing_config_js]
if _declaration_config_js not in _existing_config_js:
    doctype_js["Tax Deadline Configuration"] = [*_existing_config_js, _declaration_config_js]

# METAGEST_TAX_ALERTS_V1 — append without replacing existing fiscal guards.
from angola_tax.alerts.hooks import extend as extend_tax_alert_hooks
permission_query_conditions = globals().get('permission_query_conditions', {})
has_permission = globals().get('has_permission', {})
extend_tax_alert_hooks(doc_events, scheduler_events, permission_query_conditions, has_permission)
