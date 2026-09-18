import frappe
from frappe.utils import get_first_day, get_last_day, getdate


def sync_taxes_from_gl():
    """
    Synchronise configured tax-control accounts with Fiscal Obligations.

    Tax Account Configuration defines the accounting mapping.
    Tax Type defines the statutory identity and behaviour of the tax.
    """

    configurations = frappe.get_all(
        "Tax Account Configuration",
        filters={"active": 1},
        fields=[
            "name",
            "company",
            "tax_type",
            "account",
            "determination_direction",
            "settlement_direction",
            "source_module",
            "effective_from",
            "effective_to",
        ],
    )

    for config in configurations:
        sync_configuration(config)

    frappe.db.commit()


def sync_payroll_taxes_from_gl():
    """
    Historical function retained for compatibility.
    """
    return sync_taxes_from_gl()


def sync_configuration(config):
    tax_type = get_tax_type(config.tax_type)

    filters = {
        "company": config.company,
        "account": config.account,
        "is_cancelled": 0,
    }

    if config.effective_from:
        filters["posting_date"] = [">=", config.effective_from]

    entries = frappe.get_all(
        "GL Entry",
        filters=filters,
        fields=[
            "name",
            "company",
            "posting_date",
            "account",
            "voucher_type",
            "voucher_no",
            "debit",
            "credit",
            "remarks",
        ],
        order_by="posting_date asc, creation asc",
    )

    for gle in entries:
        if not configuration_applies(
            config,
            gle.posting_date,
        ):
            continue

        if frappe.db.exists(
            "Fiscal Obligation Movement",
            {"gl_entry": gle.name},
        ):
            continue

        movement_type, classification_status = classify_gl_entry(
            gle,
            config,
        )

        if movement_type == "Payment":
            obligation = get_payment_obligation(
                gle,
                config,
            )

            if not obligation:
                frappe.log_error(
                    message=(
                        f"{gle.voucher_type} {gle.voucher_no} on account "
                        f"{gle.account} could not be automatically matched "
                        f"to a single submitted Fiscal Obligation for "
                        f"{config.tax_type}."
                    ),
                    title="Unmatched Tax Payment",
                )
                continue

        else:
            obligation = get_or_create_obligation(
                gle,
                config,
                tax_type,
            )

        add_gl_movement(
            obligation,
            gle,
            config,
            movement_type,
            classification_status,
        )

        obligation.save(ignore_permissions=True)


def get_tax_type(tax_type_name):
    """
    Load statutory tax master data.

    Tax Type is the source of truth for:
    - tax identity
    - tax name
    - obligation type
    - periodicity
    """

    if not tax_type_name:
        frappe.throw(
            "Tax Account Configuration must have a Tax Type."
        )

    return frappe.get_cached_doc(
        "Tax Type",
        tax_type_name,
    )


def configuration_applies(config, posting_date):
    posting_date = getdate(posting_date)

    if config.effective_from:
        if posting_date < getdate(config.effective_from):
            return False

    if config.effective_to:
        if posting_date > getdate(config.effective_to):
            return False

    return True


def get_or_create_obligation(
    gle,
    config,
    tax_type,
):
    period_from, period_to = get_obligation_period(
        gle.posting_date,
        tax_type.periodicity,
    )

    existing = frappe.db.get_value(
        "Fiscal Obligation",
        {
            "company": gle.company,
            "tax_type": config.tax_type,
            "fiscal_period_from": period_from,
            "fiscal_period_to": period_to,
            "docstatus": ["!=", 2],
        },
        "name",
    )

    if existing:
        return frappe.get_doc(
            "Fiscal Obligation",
            existing,
        )

    company_currency = frappe.db.get_value(
        "Company",
        gle.company,
        "default_currency",
    )

    obligation = frappe.get_doc(
        {
            "doctype": "Fiscal Obligation",
            "company": gle.company,
            "tax_type": config.tax_type,
            "tax_name": tax_type.tax_name,
            "obligation_type": tax_type.obligation_type,
            "status": "Determined",
            "responsible_user": "Administrator",
            "fiscal_period_from": period_from,
            "fiscal_period_to": period_to,
            "fiscal_year": str(period_from.year),
            "currency": company_currency,
            "source_module": (
                config.source_module
                or "Accounting"
            ),
            "liability_account": (
                config.account
                if tax_type.obligation_type == "Payable"
                else None
            ),
            "receivable_account": (
                config.account
                if tax_type.obligation_type == "Receivable"
                else None
            ),
        }
    )

    return obligation


def get_payment_obligation(
    gle,
    config,
):
    obligations = frappe.get_all(
        "Fiscal Obligation",
        filters={
            "company": gle.company,
            "tax_type": config.tax_type,
            "docstatus": 1,
            "outstanding_amount": [">", 0],
        },
        fields=[
            "name",
            "fiscal_period_from",
            "fiscal_period_to",
            "outstanding_amount",
        ],
        order_by="fiscal_period_to asc",
    )

    # Do not guess.
    # Automatic allocation is allowed only where exactly one submitted,
    # open obligation exists for this company and tax.
    if len(obligations) == 1:
        return frappe.get_doc(
            "Fiscal Obligation",
            obligations[0].name,
        )

    return None


def add_gl_movement(
    obligation,
    gle,
    config,
    movement_type,
    classification_status,
):
    net_amount = (
        (gle.credit or 0)
        - (gle.debit or 0)
    )

    obligation.append(
        "movements",
        {
            "posting_date": gle.posting_date,
            "voucher_type": gle.voucher_type,
            "voucher_no": gle.voucher_no,
            "account": gle.account,
            "debit": gle.debit or 0,
            "credit": gle.credit or 0,
            "net_amount": net_amount,
            "movement_type": movement_type,
            "classification_status": classification_status,
            "gl_entry": gle.name,
            "source_module": get_source_module(
                gle,
                config,
            ),
            "remarks": gle.remarks,
        },
    )


def is_payroll_accrual(gle):
    remarks = (gle.remarks or "").strip().lower()

    payroll_markers = (
        "accrual journal entry for salaries",
        "accural journal entry for salaries",
        "entrada de diário de acréscimo para salários",
    )

    return (
        gle.voucher_type == "Journal Entry"
        and any(marker in remarks for marker in payroll_markers)
    )

def classify_gl_entry(
    gle,
    config,
):
    remarks = (
        gle.remarks or ""
    ).strip()

    debit = gle.debit or 0
    credit = gle.credit or 0

    movement_direction = get_movement_direction(
        debit,
        credit,
    )

    # Settlement/payment documents can be classified directly
    # from the configured settlement direction.
    if gle.voucher_type == "Payment Entry":
        if (
            movement_direction
            == config.settlement_direction
        ):
            return "Payment", "Automatic"

        return "Unclassified", "Unclassified"

    # ERPNext/HRMS payroll accrual journals are system-generated
    # and have a known semantic meaning.
    if is_payroll_accrual(gle):
        if (
            movement_direction
            == config.determination_direction
        ):
            return "Determination", "Automatic"

        if (
            movement_direction
            == config.settlement_direction
        ):
            return "Reversal", "Automatic"

        return "Unclassified", "Unclassified"

    # Manual/other Journal Entries remain conservative.
    if gle.voucher_type == "Journal Entry":
        return "Unclassified", "Unclassified"

    # Normal system-generated accounting documents.
    if (
        movement_direction
        == config.determination_direction
    ):
        return "Determination", "Automatic"

    return "Unclassified", "Unclassified"


def get_movement_direction(
    debit,
    credit,
):
    if credit > 0 and debit == 0:
        return "Credit"

    if debit > 0 and credit == 0:
        return "Debit"

    return None


def get_source_module(
    gle,
    config,
):
    remarks = (
        gle.remarks or ""
    ).strip()

    if is_payroll_accrual(gle):
        return "Payroll"

    if gle.voucher_type == "Payment Entry":
        return "Payment"

    return (
        config.source_module
        or "Accounting"
    )


def get_obligation_period(
    posting_date,
    periodicity,
):
    posting_date = getdate(
        posting_date
    )

    if periodicity == "Monthly":
        return (
            get_first_day(posting_date),
            get_last_day(posting_date),
        )

    if periodicity == "Quarterly":
        quarter_start_month = (
            ((posting_date.month - 1) // 3)
            * 3
            + 1
        )

        period_from = posting_date.replace(
            month=quarter_start_month,
            day=1,
        )

        quarter_end_month = (
            quarter_start_month + 2
        )

        period_to = get_last_day(
            posting_date.replace(
                month=quarter_end_month,
                day=1,
            )
        )

        return (
            period_from,
            period_to,
        )

    if periodicity == "Annual":
        return (
            posting_date.replace(
                month=1,
                day=1,
            ),
            posting_date.replace(
                month=12,
                day=31,
            ),
        )

    if periodicity == "Transaction":
        return (
            posting_date,
            posting_date,
        )

    frappe.throw(
        f"Unsupported tax periodicity: "
        f"{periodicity}"
    )
