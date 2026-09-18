frappe.listview_settings["Fiscal Obligation"] = {
    add_fields: [
        "compliance_status",
        "is_overdue",
        "is_filing_overdue",
        "has_variance"
    ],

    get_indicator(doc) {
        if (doc.compliance_status === "Overdue") {
            return [__("Overdue"), "red", "compliance_status,=,Overdue"];
        }

        if (doc.compliance_status === "Action Required") {
            return [__("Action Required"), "orange", "compliance_status,=,Action Required"];
        }

        if (doc.compliance_status === "Due Soon") {
            return [__("Due Soon"), "yellow", "compliance_status,=,Due Soon"];
        }

        if (doc.compliance_status === "Under Review") {
            return [__("Under Review"), "blue", "compliance_status,=,Under Review"];
        }

        if (doc.compliance_status === "Compliant") {
            return [__("Compliant"), "green", "compliance_status,=,Compliant"];
        }

        if (doc.compliance_status === "Closed") {
            return [__("Closed"), "gray", "compliance_status,=,Closed"];
        }

        return [__("Open"), "gray"];
    }
};
