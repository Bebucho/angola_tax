def extend_hooks(events):
    additions = {
        "Tax Collection Reference": {"validate":"angola_tax.industrial_tax.credits.protect_credit_confirmation", "before_cancel":"angola_tax.industrial_tax.guards.protect_collection"},
        "Tax Payment": {"before_cancel":"angola_tax.industrial_tax.guards.protect_collection"},
        "Tax Account Configuration": {"validate": "angola_tax.industrial_tax.configuration.validate_account_configuration"},
        "Tax Assessment": {"validate": "angola_tax.industrial_tax.configuration.protect_ii_assessment"},
        "Tax Settlement": {"validate": "angola_tax.industrial_tax.configuration.protect_ii_settlement"},
    }
    for doctype, mappings in additions.items():
        for event, handler in mappings.items():
            existing = events.setdefault(doctype, {}).get(event)
            events[doctype][event] = ([existing] if isinstance(existing, str) else list(existing or [])) + [handler]

    for doctype in ("Journal Entry","Payment Entry","Sales Invoice","Purchase Invoice"):
        mapping=events.setdefault(doctype,{})
        for event in ("before_cancel", "before_update_after_submit"):
            old=mapping.get(event)
            mapping[event]=([old] if isinstance(old,str) else list(old or []))+["angola_tax.industrial_tax.guards.protect_source"]
    for event in ("before_insert","before_submit"):
        old=events["Journal Entry"].get(event)
        events["Journal Entry"][event]=([old] if isinstance(old,str) else list(old or []))+["angola_tax.industrial_tax.guards.protect_reversal"]
