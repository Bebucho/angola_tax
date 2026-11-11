from angola_tax.alerts.catalog import CATALOG,PHASES,LEGACY_METHOD

def extend(doc_events,scheduler_events,permission_query_conditions,has_permission):
    daily=scheduler_events.setdefault('daily',[])
    daily[:]=[x for x in daily if x!=LEGACY_METHOD]
    legacy='angola_tax.alerts.service.legacy_notifications'
    if legacy not in daily: daily.append(legacy)
    hourly=scheduler_events.setdefault('hourly',[])
    method='angola_tax.alerts.service.scheduled'
    if method not in hourly:hourly.append(method)
    for dt in sorted({v[1] for v in CATALOG.values()}|set(PHASES)|{'Tax Alert Rule','Tax Account Configuration','Tax Declaration Type','Industrial Tax Rule','Industrial Tax Profile','Industrial Tax Mechanism Profile','Industrial Tax Revenue Configuration','VAT Settings','Journal Entry','Payment Entry','Sales Invoice','Purchase Invoice','File'}):
        for event in ('on_update','on_submit','on_update_after_submit','on_cancel'):
            values=doc_events.setdefault(dt,{}).get(event,[])
            values=[values] if isinstance(values,str) else list(values)
            if 'angola_tax.alerts.service.source_event' not in values:values.append('angola_tax.alerts.service.source_event')
            doc_events[dt][event]=values
    for dt,method in [('Tax Alert Rule','rule_query'),('Tax Warning','warning_query'),('Tax Warning Delivery','delivery_query')]:
        permission_query_conditions[dt]='angola_tax.alerts.permissions.'+method
        has_permission[dt]='angola_tax.alerts.permissions.has_permission'
    permission_query_conditions['Notification Log']='angola_tax.alerts.permissions.notification_query'
    has_permission['Notification Log']='angola_tax.alerts.permissions.notification_permission'
