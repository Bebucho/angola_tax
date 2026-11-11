from angola_tax.alerts.reports import execute as run

def execute(filters=None):
    return run(filters,summary=True)
