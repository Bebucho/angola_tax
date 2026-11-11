frappe.query_reports["Tax Active Warnings"]={filters:[
{"fieldname": "company", "label": "Company", "fieldtype": "Link", "options": "Company"},
{"fieldname": "fiscal_process", "label": "Process", "fieldtype": "Data"},
{"fieldname": "severity", "label": "Severity", "fieldtype": "Select", "options": "\nLow\nMedium\nHigh\nCritical"},
{"fieldname": "responsible_user", "label": "Responsible User", "fieldtype": "Link", "options": "User"},
{"fieldname": "status", "label": "Status", "fieldtype": "Select", "options": "\nOpen\nAcknowledged\nSnoozed\nDismissed\nResolved"},
{"fieldname": "kind", "label": "Kind", "fieldtype": "Select", "options": "\nAlert\nNotice"},
{"fieldname": "include_resolved", "label": "Include Resolved", "fieldtype": "Check"}
]};
