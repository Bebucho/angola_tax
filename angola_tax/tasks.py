import frappe

from angola_tax.payment_control import refresh_payment_control


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
            refresh_payment_control(name)

            doc = frappe.get_doc("Fiscal Obligation", name)

            if (
                doc.obligation_origin == "Operational"
                and not doc.original_payment_due_date
            ):
                doc.set_statutory_due_dates()

            doc.set_process_schedule_dates()
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
                    "original_payment_due_date": doc.original_payment_due_date,
                    "current_payment_due_date": doc.current_payment_due_date,
                    "effective_payment_due_date": doc.effective_payment_due_date,

                    "determination_target_date":
                        doc.determination_target_date,
                    "determination_critical_date":
                        doc.determination_critical_date,

                    "liquidation_target_date":
                        doc.liquidation_target_date,
                    "liquidation_critical_date":
                        doc.liquidation_critical_date,

                    "payment_target_date":
                        doc.payment_target_date,
                    "payment_critical_date":
                        doc.payment_critical_date,

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
    # Keep the public method as a guarded delegate, including already queued old jobs.
    from angola_tax.alerts.service import legacy_notifications
    return legacy_notifications()
