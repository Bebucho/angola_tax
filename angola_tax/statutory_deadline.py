"""Read the actionable statutory payment date from an obligation."""

import frappe


def get_statutory_payment_due_date(obligation):
    return (
        obligation.get("current_payment_due_date")
        or obligation.get("original_payment_due_date")
    )


def backfill_statutory_due_for_obligation(fiscal_obligation):
    """Populate the newly introduced display field on existing linked records."""
    obligation = frappe.get_doc("Fiscal Obligation", fiscal_obligation)
    due_date = get_statutory_payment_due_date(obligation)
    if not due_date:
        return 0

    updated = 0
    for doctype in ("Tax Assessment", "Tax Settlement"):
        rows = frappe.get_all(
            doctype,
            filters={
                "fiscal_obligation": obligation.name,
                "docstatus": ["!=", 2],
            },
            fields=["name", "statutory_payment_due_date"],
            limit_page_length=0,
        )
        for row in rows:
            if row.statutory_payment_due_date:
                continue
            frappe.db.set_value(
                doctype,
                row.name,
                "statutory_payment_due_date",
                due_date,
                update_modified=False,
            )
            updated += 1
    return updated
