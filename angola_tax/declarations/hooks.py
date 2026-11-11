def extend_hooks(doc_events, scheduler_events, override_doctype_class):
    expected = {'Tax Deadline Configuration': 'angola_tax.industrial_tax.adapters.IndustrialDeadlineConfiguration',
                'Fiscal Obligation': 'angola_tax.industrial_tax.adapters.IndustrialFiscalObligation'}
    replacement = {'Tax Deadline Configuration': 'angola_tax.declarations.configuration.DeclarationDeadlineConfiguration',
                   'Fiscal Obligation': 'angola_tax.declarations.service.DeclarationFiscalObligation'}
    for name, old in expected.items():
        current = override_doctype_class.get(name)
        if current not in (old, replacement[name]):
            raise RuntimeError('Declaration integration cannot replace an unknown override: ' + name)
        override_doctype_class[name] = replacement[name]
    def append(doctype, event, handler):
        existing = doc_events.setdefault(doctype, {}).get(event, [])
        handlers = [existing] if isinstance(existing, str) else list(existing)
        if handler not in handlers: handlers.append(handler)
        doc_events[doctype][event] = handlers
    for event in ('before_save','on_trash'): append('File', event, 'angola_tax.declarations.service.protect_file')
    for name in ('Fiscal Obligation','VAT Period Control','VAT Reconciliation'):
        for event in ('validate','before_update_after_submit'):
            append(name, event, 'angola_tax.declarations.service.protect_legacy_submission')
    daily = scheduler_events.setdefault('daily', [])
    if 'angola_tax.declarations.service.daily' not in daily: daily.append('angola_tax.declarations.service.daily')
