import frappe


def update_fiscal_obligation_statuses():
    obligations = frappe.get_all(
        "Fiscal Obligation",
        filters={
            "docstatus": ["!=", 2],
            "status": ["not in", ["Closed", "Cancelled"]],
        },
        pluck="name",
    )

    for name in obligations:
        try:
            doc = frappe.get_doc("Fiscal Obligation", name)

            doc.calculate_outstanding_amount()
            doc.calculate_filing_status()
            doc.calculate_deadline_status()
            doc.calculate_compliance_status()

            doc.db_set(
                {
                    "amount_paid": doc.amount_paid,
                    "outstanding_amount": doc.outstanding_amount,
                    "filing_status": doc.filing_status,
                    "is_filing_overdue": doc.is_filing_overdue,
                    "days_filing_overdue": doc.days_filing_overdue,
                    "effective_payment_due_date": doc.effective_payment_due_date,
                    "deadline_status": doc.deadline_status,
                    "is_overdue": doc.is_overdue,
                    "days_overdue": doc.days_overdue,
                    "compliance_status": doc.compliance_status,
                    "compliance_alert": doc.compliance_alert,
                    "compliance_message": doc.compliance_message,
                },
                update_modified=False,
            )

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Fiscal Obligation status update failed: {name}",
            )


def create_fiscal_obligation_notifications():
    obligations = frappe.get_all(
        "Fiscal Obligation",
        filters={
            "docstatus": ["!=", 2],
            "compliance_alert": 1,
            "status": ["not in", ["Closed", "Cancelled"]],
        },
        fields=[
            "name",
            "company",
            "tax_name",
            "compliance_status",
            "compliance_message",
            "responsible_user",
            "reviewer",
            "outstanding_amount",
            "effective_payment_due_date",
        ],
    )

    for row in obligations:
        recipients = []

        if row.responsible_user:
            recipients.append(row.responsible_user)

        if row.compliance_status == "Overdue" and row.reviewer:
            recipients.append(row.reviewer)

        for user in set(recipients):
            existing = frappe.db.exists(
                "ToDo",
                {
                    "reference_type": "Fiscal Obligation",
                    "reference_name": row.name,
                    "allocated_to": user,
                    "status": "Open",
                },
            )

            if existing:
                continue

            description = (
                f"{row.tax_name} - {row.company}: "
                f"{row.compliance_message or row.compliance_status}"
            )

            todo = frappe.get_doc(
                {
                    "doctype": "ToDo",
                    "allocated_to": user,
                    "description": description,
                    "reference_type": "Fiscal Obligation",
                    "reference_name": row.name,
                    "priority": (
                        "High"
                        if row.compliance_status == "Overdue"
                        else "Medium"
                    ),
                }
            )

            todo.insert(ignore_permissions=True)

    frappe.db.commit()
