
import frappe
from frappe.utils import flt, getdate, nowdate


def refresh_payment_control(fiscal_obligation):
    """
    Rebuild tax-payment control from confirmed Collection References.

    Accounting execution is NOT payment confirmation.
    Only Collection References with status = Confirmed count as paid.
    """

    if not fiscal_obligation:
        return

    refresh_plan_instalments(fiscal_obligation)
    refresh_payment_plans(fiscal_obligation)
    refresh_settlements(fiscal_obligation)
    refresh_fiscal_obligation(fiscal_obligation)


# ==========================================================
# PAYMENT PLAN INSTALMENTS
# ==========================================================

def refresh_plan_instalments(fiscal_obligation):
    plans = frappe.get_all(
        "Tax Payment Plan",
        filters={
            "fiscal_obligation": fiscal_obligation,
            "docstatus": 1,
        },
        pluck="name",
    )

    today = getdate(nowdate())

    for plan_name in plans:
        plan = frappe.get_doc(
            "Tax Payment Plan",
            plan_name,
        )

        changed = False

        for row in plan.instalments or []:
            confirmed = get_confirmed_amount(
                payment_plan=plan.name,
                instalment_no=row.instalment_no,
            )

            amount = flt(row.amount)
            paid = min(flt(confirmed), amount)
            outstanding = max(amount - paid, 0)

            if paid <= 0:
                if (
                    row.due_date
                    and getdate(row.due_date) < today
                ):
                    payment_status = "Overdue"
                else:
                    payment_status = "Pending"

            elif outstanding > 0:
                if (
                    row.due_date
                    and getdate(row.due_date) < today
                ):
                    payment_status = "Overdue"
                else:
                    payment_status = "Partially Paid"

            else:
                payment_status = "Paid"

            if (
                abs(flt(row.amount_paid) - paid) > 0.01
                or abs(
                    flt(row.outstanding_amount)
                    - outstanding
                ) > 0.01
                or row.payment_status != payment_status
            ):
                row.db_set(
                    "amount_paid",
                    paid,
                    update_modified=False,
                )
                row.db_set(
                    "outstanding_amount",
                    outstanding,
                    update_modified=False,
                )
                row.db_set(
                    "payment_status",
                    payment_status,
                    update_modified=False,
                )
                changed = True

        if changed:
            plan.reload()


# ==========================================================
# PAYMENT PLANS
# ==========================================================

def refresh_payment_plans(fiscal_obligation):
    plans = frappe.get_all(
        "Tax Payment Plan",
        filters={
            "fiscal_obligation": fiscal_obligation,
            "docstatus": 1,
        },
        pluck="name",
    )

    today = getdate(nowdate())

    for plan_name in plans:
        plan = frappe.get_doc(
            "Tax Payment Plan",
            plan_name,
        )

        # Historical plans retain their terminal status.
        if plan.status in (
            "Superseded",
            "Cancelled",
        ):
            continue

        outstanding = sum(
            flt(row.outstanding_amount)
            for row in (plan.instalments or [])
        )

        overdue = any(
            flt(row.outstanding_amount) > 0.01
            and row.due_date
            and getdate(row.due_date) < today
            for row in (plan.instalments or [])
        )

        if outstanding <= 0.01:
            status = "Completed"

        elif plan.status == "Awaiting Reissue":
            # This status represents an explicit reissue
            # process. Do not overwrite it merely because
            # an instalment is overdue.
            status = "Awaiting Reissue"

        elif overdue:
            status = "In Arrears"

        else:
            status = "Active"

        frappe.db.set_value(
            "Tax Payment Plan",
            plan.name,
            {
                "scheduled_outstanding":
                    outstanding,
                "status": status,
            },
            update_modified=True,
        )


# ==========================================================
# TAX SETTLEMENTS
# ==========================================================

def refresh_settlements(fiscal_obligation):
    settlements = frappe.get_all(
        "Tax Settlement",
        filters={
            "fiscal_obligation": fiscal_obligation,
            "docstatus": 1,
        },
        fields=[
            "name",
            "liquidated_amount",
            "status",
        ],
    )

    for settlement in settlements:
        if settlement.status == "Cancelled":
            continue

        paid = get_confirmed_amount(
            tax_settlement=settlement.name,
        )

        liquidated = flt(
            settlement.liquidated_amount
        )

        operative_plan = frappe.get_all(
            "Tax Payment Plan",
            filters={
                "tax_settlement": settlement.name,
                "docstatus": 1,
                "status": [
                    "in",
                    [
                        "Active",
                        "In Arrears",
                        "Awaiting Reissue",
                    ],
                ],
            },
            pluck="name",
            limit=1,
        )

        if liquidated > 0 and paid >= liquidated - 0.01:
            status = "Paid"

        elif operative_plan:
            status = "Under Payment Plan"

        elif paid > 0:
            status = "Partially Paid"

        else:
            status = "Liquidated"

        frappe.db.set_value(
            "Tax Settlement",
            settlement.name,
            "status",
            status,
            update_modified=True,
        )


# ==========================================================
# FISCAL OBLIGATION
# ==========================================================

def refresh_fiscal_obligation(fiscal_obligation):
    obligation = frappe.get_doc(
        "Fiscal Obligation",
        fiscal_obligation,
    )

    from angola_tax.industrial_tax.adapters import refresh_ii_payment
    if refresh_ii_payment(obligation):
        return

    if obligation.status in (
        "Cancelled",
        "Superseded",
    ):
        return

    confirmed_paid = get_confirmed_amount(
        fiscal_obligation=fiscal_obligation,
    )

    # Once formal liquidation exists, Settlement becomes
    # authoritative for the collectible amount.
    #
    # Earlier determination / filing / assessment amounts
    # remain part of the audit trail but must not override
    # the final collectible amount.
    settlements = frappe.get_all(
        "Tax Settlement",
        filters={
            "fiscal_obligation":
                fiscal_obligation,
            "docstatus": 1,
            "status": ["!=", "Cancelled"],
        },
        fields=["liquidated_amount"],
    )

    liquidated_total = sum(
        flt(row.liquidated_amount)
        for row in settlements
    )

    if settlements:
        effective_amount = liquidated_total
    else:
        # Pre-liquidation exposure only.
        effective_amount = max(
            flt(obligation.amount_assessed),
            flt(obligation.amount_filed),
            flt(obligation.amount_determined),
        )

    outstanding = max(
        effective_amount - confirmed_paid,
        0,
    )

    operative_plan = frappe.get_all(
        "Tax Payment Plan",
        filters={
            "fiscal_obligation":
                fiscal_obligation,
            "docstatus": 1,
            "status": [
                "in",
                [
                    "Active",
                    "In Arrears",
                    "Awaiting Reissue",
                ],
            ],
        },
        pluck="name",
        order_by="modified desc",
        limit=1,
    )

    if outstanding <= 0.01 and effective_amount > 0:
        status = "Paid"

    elif operative_plan:
        status = "Under Payment Plan"

    else:
        status = "Ready for Payment"

    fully_paid_on = None
    days_paid_late = 0
    if status == "Paid":
        fully_paid_on = get_full_payment_date(
            fiscal_obligation, effective_amount
        )
        if fully_paid_on and obligation.original_payment_due_date:
            days_paid_late = max(
                (getdate(fully_paid_on)
                 - getdate(obligation.original_payment_due_date)).days,
                0,
            )

    frappe.db.set_value(
        "Fiscal Obligation",
        fiscal_obligation,
        {
            "amount_paid": confirmed_paid,
            "outstanding_amount": outstanding,
            "latest_payment_plan":
                operative_plan[0]
                if operative_plan
                else None,
            "status": status,
            "fully_paid_on": fully_paid_on,
            "paid_late": int(days_paid_late > 0),
            "days_paid_late": days_paid_late,
        },
        update_modified=True,
    )


# ==========================================================
# CONFIRMED PAYMENT SOURCE
# ==========================================================

def get_full_payment_date(fiscal_obligation, amount_due):
    """Date confirmed collections first covered the full collectible amount."""
    references = frappe.get_all(
        "Tax Collection Reference",
        filters={
            "fiscal_obligation": fiscal_obligation,
            "status": "Confirmed",
        },
        fields=["name", "amount", "payment_date", "tax_payment"],
        limit_page_length=0,
    )
    if sum(flt(row.amount) for row in references) < flt(amount_due) - 0.01:
        return None

    dated = []
    for row in references:
        payment_date = row.payment_date
        if not payment_date and row.tax_payment:
            payment_date = frappe.db.get_value(
                "Tax Payment", row.tax_payment, "payment_date"
            )
        if not payment_date:
            # Never assert on-time compliance without an actual payment date.
            return None
        dated.append((getdate(payment_date), row.name, flt(row.amount)))

    total = 0
    for payment_date, _name, amount in sorted(dated):
        total += amount
        if total >= flt(amount_due) - 0.01:
            return payment_date

    return None

def get_confirmed_amount(
    fiscal_obligation=None,
    tax_settlement=None,
    payment_plan=None,
    instalment_no=None,
):
    filters = {
        "status": "Confirmed",
    }

    if fiscal_obligation:
        filters["fiscal_obligation"] = (
            fiscal_obligation
        )

    if tax_settlement:
        filters["tax_settlement"] = (
            tax_settlement
        )

    if payment_plan:
        filters["payment_plan"] = payment_plan

    if instalment_no is not None:
        filters["instalment_no"] = (
            instalment_no
        )

    result = frappe.db.sql(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM `tabTax Collection Reference`
        WHERE status = 'Confirmed'
          AND (%s IS NULL OR fiscal_obligation = %s)
          AND (%s IS NULL OR tax_settlement = %s)
          AND (%s IS NULL OR payment_plan = %s)
          AND (%s IS NULL OR instalment_no = %s)
        """,
        (
            fiscal_obligation,
            fiscal_obligation,
            tax_settlement,
            tax_settlement,
            payment_plan,
            payment_plan,
            instalment_no,
            instalment_no,
        ),
    )

    return flt(result[0][0] if result else 0)


