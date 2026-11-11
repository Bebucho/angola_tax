
import frappe
from frappe.utils import flt, get_first_day, get_last_day, getdate
from frappe.utils import getdate, add_days


def submit_control_journal(je, obligation, company, tax_type):
    """Keep a tax control transfer on its source obligation during GL posting."""
    previous = frappe.flags.get("angola_tax_control_journal")
    frappe.flags.angola_tax_control_journal = {
        "journal_entry": je.name,
        "fiscal_obligation": obligation,
        "company": company,
        "tax_type": tax_type,
    }
    try:
        je.submit()
    finally:
        if previous is None:
            frappe.flags.pop("angola_tax_control_journal", None)
        else:
            frappe.flags.angola_tax_control_journal = previous


def submit_vat_closing_journal(
    je,
    obligation,
    company,
    tax_type,
    vat_period,
    tax_account,
):
    """
    Submit a VAT closing Journal Entry with explicit fiscal meaning.

    The GL ingestion hook will recognise the payable-account line
    as the VAT Determination movement for the linked Fiscal
    Obligation.

    This prevents a system-generated VAT close from being treated
    as an arbitrary/manual Journal Entry.
    """

    previous = frappe.flags.get(
        "angola_tax_vat_closing"
    )

    frappe.flags.angola_tax_vat_closing = {
        "journal_entry":
            je.name,
        "fiscal_obligation":
            obligation,
        "company":
            company,
        "tax_type":
            tax_type,
        "vat_period":
            vat_period,
        "tax_account":
            tax_account,
    }

    try:
        je.submit()

    finally:
        if previous is None:
            frappe.flags.pop(
                "angola_tax_vat_closing",
                None,
            )
        else:
            frappe.flags.angola_tax_vat_closing = (
                previous
            )


def submit_vat_rounding_journal(
    je,
    obligation,
    company,
    tax_type,
    vat_period,
    tax_account,
):
    """
    Submit the VAT statutory rounding adjustment.

    The VAT payable-account line is classified as Reclassification
    rather than Determination, preserving the original raw VAT
    determination while bringing the accounting liability to the
    legally payable rounded amount.
    """

    previous = frappe.flags.get(
        "angola_tax_vat_rounding"
    )

    frappe.flags.angola_tax_vat_rounding = {
        "journal_entry":
            je.name,
        "fiscal_obligation":
            obligation,
        "company":
            company,
        "tax_type":
            tax_type,
        "vat_period":
            vat_period,
        "tax_account":
            tax_account,
    }

    try:
        je.submit()

    finally:

        if previous is None:

            frappe.flags.pop(
                "angola_tax_vat_rounding",
                None,
            )

        else:

            frappe.flags.angola_tax_vat_rounding = (
                previous
            )


def get_control_journal_context(gle):
    if gle.voucher_type != "Journal Entry":
        return None
    context = frappe.flags.get("angola_tax_control_journal")
    if context and context["journal_entry"] == gle.voucher_no:
        return context
    for doctype, field in (
        ("Tax Payment Plan", "opening_journal_entry"),
        ("Tax Settlement", "adjustment_journal_entry"),
        ("Tax Settlement", "settlement_journal_entry"),
    ):
        owner = frappe.db.get_value(
            doctype, {field: gle.voucher_no},
            ["fiscal_obligation", "company", "tax_type"], as_dict=True,
        )
        if owner:
            return owner
    return None

def sync_taxes_from_gl():
    """
    Synchronise configured tax-control accounts with Fiscal Obligations.

    Tax Account Configuration defines the accounting mapping.
    Tax Type defines the statutory identity and behaviour of the tax.
    """

    configurations = frappe.get_all(
        "Tax Account Configuration",
        filters={"active": 1, "docstatus": 1},
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
        limit_page_length=0,
    )

    for config in configurations:
        sync_configuration(config)


def sync_journal_entry(doc, method=None):
    """Capture tax-account GL rows after ERPNext posts a Journal Entry."""
    accounts = list({row.account for row in doc.accounts if row.account})
    if not accounts:
        return

    configurations = frappe.get_all(
        "Tax Account Configuration",
        filters={
            "active": 1,
            "docstatus": 1,
            "company": doc.company,
            "account": ["in", accounts],
        },
        fields=[
            "name", "company", "tax_type", "account",
            "determination_direction", "settlement_direction",
            "source_module", "effective_from", "effective_to",
        ],
        limit_page_length=0,
    )
    for config in configurations:
        sync_configuration(
            config,
            voucher_type="Journal Entry",
            voucher_no=doc.name,
        )


def sync_payment_entry(doc, method=None):
    """Capture configured tax-account GL rows after Payment Entry submission."""

    accounts = frappe.get_all(
        "GL Entry",
        filters={
            "company": doc.company,
            "voucher_type": "Payment Entry",
            "voucher_no": doc.name,
            "is_cancelled": 0,
        },
        pluck="account",
        distinct=True,
    )

    if not accounts:
        return

    configurations = frappe.get_all(
        "Tax Account Configuration",
        filters={
            "active": 1,
            "docstatus": 1,
            "company": doc.company,
            "account": ["in", accounts],
        },
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
        limit_page_length=0,
    )

    for config in configurations:
        sync_configuration(
            config,
            voucher_type="Payment Entry",
            voucher_no=doc.name,
        )


def cancel_journal_entry_sync(doc, method=None):
    """Remove Fiscal Obligation movements originating from a cancelled JE."""

    movements = frappe.get_all(
        "Fiscal Obligation Movement",
        filters={
            "voucher_type": "Journal Entry",
            "voucher_no": doc.name,
        },
        fields=["name", "parent"],
        limit_page_length=0,
    )

    if not movements:
        return

    by_obligation = {}

    for movement in movements:
        by_obligation.setdefault(
            movement.parent,
            set(),
        ).add(movement.name)

    for obligation_name, movement_names in by_obligation.items():
        obligation = frappe.get_doc(
            "Fiscal Obligation",
            obligation_name,
        )

        # A submitted Fiscal Obligation is legally/audit locked.
        # The Journal Entry cancellation guard should prevent this case.
        if obligation.docstatus == 1:
            frappe.throw(
                f"Journal Entry {doc.name} is linked to submitted "
                f"Fiscal Obligation {obligation.name}. "
                "Reverse the fiscal process before cancelling "
                "the accounting source."
            )

        if obligation.docstatus == 2:
            continue

        obligation.set(
            "movements",
            [
                row
                for row in (obligation.movements or [])
                if row.name not in movement_names
            ],
        )

        # Operational draft obligations are accounting-derived.
        # If their final source movement disappears, the derived
        # draft obligation must disappear as well.
        if (
            obligation.docstatus == 0
            and not obligation.movements
            and obligation.obligation_origin == "Operational"
        ):
            frappe.delete_doc(
                "Fiscal Obligation",
                obligation.name,
                ignore_permissions=True,
            )
            continue

        obligation.save(ignore_permissions=True)


def sync_payroll_taxes_from_gl():
    """
    Historical function retained for compatibility.
    """
    return sync_taxes_from_gl()


def cancel_payment_entry_sync(doc, method=None):
    """Remove Fiscal Obligation movements originating from a cancelled JE."""

    movements = frappe.get_all(
        "Fiscal Obligation Movement",
        filters={
            "voucher_type": "Payment Entry",
            "voucher_no": doc.name,
        },
        fields=["name", "parent"],
        limit_page_length=0,
    )

    if not movements:
        return

    by_obligation = {}

    for movement in movements:
        by_obligation.setdefault(
            movement.parent,
            set(),
        ).add(movement.name)

    for obligation_name, movement_names in by_obligation.items():
        obligation = frappe.get_doc(
            "Fiscal Obligation",
            obligation_name,
        )

        # A submitted Fiscal Obligation is legally/audit locked.
        # The Journal Entry cancellation guard should prevent this case.
        if obligation.docstatus == 1:
            frappe.throw(
                f"Journal Entry {doc.name} is linked to submitted "
                f"Fiscal Obligation {obligation.name}. "
                "Reverse the fiscal process before cancelling "
                "the accounting source."
            )

        if obligation.docstatus == 2:
            continue

        obligation.set(
            "movements",
            [
                row
                for row in (obligation.movements or [])
                if row.name not in movement_names
            ],
        )

        # Operational draft obligations are accounting-derived.
        # If their final source movement disappears, the derived
        # draft obligation must disappear as well.
        if (
            obligation.docstatus == 0
            and not obligation.movements
            and obligation.obligation_origin == "Operational"
        ):
            frappe.delete_doc(
                "Fiscal Obligation",
                obligation.name,
                ignore_permissions=True,
            )
            continue

        obligation.save(ignore_permissions=True)


def sync_payroll_taxes_from_gl():
    """
    Historical function retained for compatibility.
    """
    return sync_taxes_from_gl()


def sync_configuration(config, voucher_type=None, voucher_no=None):
    tax_type = get_tax_type(config.tax_type)

    filters = {
        "company": config.company,
        "account": config.account,
        "is_cancelled": 0,
    }

    if config.effective_from:
        filters["posting_date"] = [">=", config.effective_from]
    if voucher_no:
        filters["voucher_type"] = voucher_type
        filters["voucher_no"] = voucher_no

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
        limit_page_length=0,
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

        if reconcile_replaced_gl_entry(gle):
            continue

        from angola_tax.industrial_tax.adapters import ingest_gl
        if ingest_gl(config, gle):
            continue

        vat_closing = frappe.flags.get(
            "angola_tax_vat_closing"
        )

        if (
            vat_closing
            and gle.voucher_type == "Journal Entry"
            and gle.voucher_no
                == vat_closing["journal_entry"]
            and gle.account
                == vat_closing["tax_account"]
        ):
            obligation = frappe.get_doc(
                "Fiscal Obligation",
                vat_closing["fiscal_obligation"],
            )

            if (
                obligation.docstatus != 0
                or obligation.company != gle.company
                or obligation.company
                    != vat_closing["company"]
                or obligation.tax_type
                    != config.tax_type
                or obligation.tax_type
                    != vat_closing["tax_type"]
                or obligation.source_document_type
                    != "VAT Period Control"
                or obligation.source_document
                    != vat_closing["vat_period"]
                or obligation.liability_account
                    != config.account
                or config.account
                    != vat_closing["tax_account"]
            ):
                frappe.throw(
                    f"VAT closing {gle.voucher_no} does not "
                    "match its Fiscal Obligation, VAT Period "
                    "or configured Tax Account."
                )

            direction = get_movement_direction(
                gle.debit,
                gle.credit,
            )

            if (
                direction
                != config.determination_direction
            ):
                frappe.throw(
                    f"VAT closing {gle.voucher_no} uses "
                    f"{direction} on {gle.account}; expected "
                    f"{config.determination_direction}."
                )

            add_gl_movement(
                obligation,
                gle,
                config,
                "Determination",
                "Automatic",
            )

            obligation.save(
                ignore_permissions=True
            )

            continue

        vat_rounding = frappe.flags.get(
            "angola_tax_vat_rounding"
        )

        if (
            vat_rounding
            and gle.voucher_type == "Journal Entry"
            and gle.voucher_no
                == vat_rounding["journal_entry"]
            and gle.account
                == vat_rounding["tax_account"]
        ):

            obligation = frappe.get_doc(
                "Fiscal Obligation",
                vat_rounding["fiscal_obligation"],
            )

            if (
                obligation.company
                    != gle.company
                or obligation.company
                    != vat_rounding["company"]
                or obligation.tax_type
                    != config.tax_type
                or obligation.tax_type
                    != vat_rounding["tax_type"]
                or obligation.source_document_type
                    != "VAT Period Control"
                or obligation.source_document
                    != vat_rounding["vat_period"]
                or obligation.liability_account
                    != config.account
            ):
                frappe.throw(
                    f"VAT rounding Journal Entry "
                    f"{gle.voucher_no} does not match "
                    "its VAT Fiscal Obligation."
                )

            add_gl_movement(
                obligation,
                gle,
                config,
                "Reclassification",
                "Automatic",
            )

            obligation.save(
                ignore_permissions=True
            )

            continue

        control = get_control_journal_context(gle)
        if control:
            obligation = frappe.get_doc(
                "Fiscal Obligation", control["fiscal_obligation"]
            )
            if (
                obligation.company != gle.company
                or obligation.company != control["company"]
                or obligation.tax_type != config.tax_type
                or control["tax_type"] != config.tax_type
            ):
                frappe.throw(
                    f"Tax control transfer {gle.voucher_no} does not "
                    "match its source Fiscal Obligation."
                )
            add_gl_movement(
                obligation, gle, config, "Reclassification", "Automatic"
            )
            obligation.save(ignore_permissions=True)
            continue

        payment = frappe.flags.get("angola_tax_payment_submission")
        if (
            payment
            and gle.voucher_type == "Journal Entry"
            and gle.voucher_no == payment["journal_entry"]
            and gle.account == payment["tax_account"]
        ):
            obligation = frappe.get_doc(
                "Fiscal Obligation", payment["fiscal_obligation"]
            )
            direction = get_movement_direction(gle.debit, gle.credit)
            movement = (
                flt(gle.debit) if obligation.obligation_type == "Payable"
                else flt(gle.credit)
            )
            if (
                obligation.docstatus != 1
                or obligation.company != gle.company
                or obligation.company != payment["company"]
                or obligation.tax_type != config.tax_type
                or config.account != gle.account
                or direction != config.settlement_direction
                or abs(movement - flt(payment["amount"])) > 0.01
            ):
                frappe.throw(
                    f"Tax Payment {gle.voucher_no} does not match its "
                    "submitted Fiscal Obligation or configured Tax Account."
                )
            add_gl_movement(obligation, gle, config, "Payment", "Automatic")
            obligation.save(ignore_permissions=True)
            continue

        # A submitted Assessment adjustment is an audited GL
        # movement on its original obligation, even when the JE
        # posting date belongs to another fiscal period.
        adjustment = None
        if gle.voucher_type == "Journal Entry":
            adjustment = frappe.db.get_value(
                "Tax Assessment",
                {"adjustment_journal_entry": gle.voucher_no},
                ["name", "fiscal_obligation", "assessment_variance"],
                as_dict=True,
            )
        if adjustment:
            obligation = frappe.get_doc(
                "Fiscal Obligation", adjustment.fiscal_obligation
            )
            tax_account = (
                obligation.liability_account
                if obligation.obligation_type == "Payable"
                else obligation.receivable_account
            )
            if (
                obligation.obligation_origin != "Operational"
                or obligation.company != gle.company
                or obligation.tax_type != config.tax_type
                or gle.account != tax_account
            ):
                frappe.throw(
                    f"Assessment adjustment {gle.voucher_no} does not match "
                    "its source Fiscal Obligation."
                )
            movement_type = (
                "Adjustment Increase" if flt(adjustment.assessment_variance) > 0
                else "Adjustment Decrease"
            )
            add_gl_movement(
                obligation, gle, config, movement_type, "Automatic"
            )
            obligation.save(ignore_permissions=True)
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

        late_posting = obligation.docstatus == 1 and movement_type != "Payment"
        suggested_type = movement_type
        if late_posting:
            # Capture the GL fact without changing a submitted tax
            # determination or an assessment based on that determination.
            movement_type = "Unclassified"
            classification_status = "Unclassified"

        add_gl_movement(
            obligation,
            gle,
            config,
            movement_type,
            classification_status,
        )

        if late_posting:
            row = obligation.movements[-1]
            row.remarks = (
                f"Late GL posting after submission; suggested type: "
                f"{suggested_type}. {gle.remarks or ''}"
            ).strip()

        obligation.save(ignore_permissions=True)


def reconcile_replaced_gl_entry(gle):
    """Rebind a captured movement when ERPNext replaces its GL Entry ID.

    Match the accounting fact, and only rebind when both sides are unique.
    An ambiguous match is left for manual investigation; it must never
    create another Fiscal Obligation or silently assign a payment.
    """
    candidates = frappe.get_all(
        "Fiscal Obligation Movement",
        filters={
            "voucher_type": gle.voucher_type,
            "voucher_no": gle.voucher_no,
            "account": gle.account,
        },
        fields=["name", "parent", "gl_entry", "debit", "credit"],
        limit_page_length=0,
    )
    matches = [
        row for row in candidates
        if abs(flt(row.debit) - flt(gle.debit)) < 0.01
        and abs(flt(row.credit) - flt(gle.credit)) < 0.01
    ]
    if not matches:
        return False

    live = frappe.get_all(
        "GL Entry",
        filters={
            "company": gle.company,
            "voucher_type": gle.voucher_type,
            "voucher_no": gle.voucher_no,
            "account": gle.account,
            "is_cancelled": 0,
        },
        fields=["name", "debit", "credit"],
        limit_page_length=0,
    )
    matching_live = [
        row for row in live
        if abs(flt(row.debit) - flt(gle.debit)) < 0.01
        and abs(flt(row.credit) - flt(gle.credit)) < 0.01
    ]
    if len(matches) != 1 or len(matching_live) != 1:
        frappe.throw(
            f"Ambiguous GL replacement for {gle.voucher_type} "
            f"{gle.voucher_no} / {gle.account}. Review the "
            "Fiscal Obligation movements before GL synchronisation."
        )

    previous = matches[0]
    if frappe.db.exists("GL Entry", previous.gl_entry):
        frappe.throw(
            f"An existing GL Entry {previous.gl_entry} already represents "
            f"{gle.voucher_type} {gle.voucher_no} / {gle.account}. "
            "Review duplicate accounting rows."
        )
    parent_company = frappe.db.get_value(
        "Fiscal Obligation", previous.parent, "company"
    )
    if parent_company != gle.company:
        frappe.throw("GL replacement belongs to a different company.")

    frappe.db.set_value(
        "Fiscal Obligation Movement", previous.name,
        "gl_entry", gle.name, update_modified=False,
    )
    if frappe.db.exists("Tax Payment", {"gl_entry": previous.gl_entry}):
        payments = frappe.get_all(
            "Tax Payment",
            filters={"gl_entry": previous.gl_entry},
            fields=["name", "fiscal_obligation", "source_voucher_type",
                    "source_voucher", "journal_entry"],
            limit_page_length=0,
        )
        if len(payments) != 1 or (
            payments[0].fiscal_obligation != previous.parent
            or payments[0].source_voucher_type != gle.voucher_type
            or payments[0].source_voucher != gle.voucher_no
        ):
            frappe.throw("Tax Payment GL link is ambiguous.")
        frappe.db.set_value(
            "Tax Payment", payments[0].name,
            "gl_entry", gle.name, update_modified=False,
        )
    return True


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

    grouping = tax_type.get("obligation_grouping") or "Fiscal Period"
    filters = {
        "company": gle.company,
        "tax_type": config.tax_type,
        "obligation_origin": "Operational",
        "fiscal_period_from": period_from,
        "fiscal_period_to": period_to,
        "docstatus": ["!=", 2],
    }
    if grouping == "Source Voucher":
        if not gle.voucher_type or not gle.voucher_no:
            frappe.throw(
                "Source Voucher grouping requires an accounting voucher."
            )
        filters["source_document_type"] = gle.voucher_type
        filters["source_document"] = gle.voucher_no
    elif grouping != "Fiscal Period":
        frappe.throw(
            f"Unsupported Tax Type Obligation Grouping: {grouping}"
        )

    existing = frappe.get_all(
        "Fiscal Obligation", filters=filters,
        pluck="name", limit_page_length=2,
    )

    if len(existing) > 1:
        frappe.throw(
            f"Multiple operational Fiscal Obligations match "
            f"{gle.company} / {config.tax_type} / {gle.voucher_no}."
        )
    if existing:
        return frappe.get_doc(
            "Fiscal Obligation",
            existing[0],
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
            "obligation_origin": "Operational",
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
            "source_document_type": (
                gle.voucher_type if grouping == "Source Voucher" else None
            ),
            "source_document": (
                gle.voucher_no if grouping == "Source Voucher" else None
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
            "obligation_origin": "Operational",
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

def is_salary_payment_event(gle):
    if not gle.account:
        return False

    if (gle.debit or 0) <= 0:
        return False

    against_accounts = [
        account.strip()
        for account in (gle.against or "").split(",")
        if account.strip()
    ]

    for account_name in against_accounts:
        account_type = frappe.db.get_value(
            "Account",
            account_name,
            "account_type",
        )

        if account_type in ("Bank", "Cash"):
            return True

    return False

def get_previous_month_period(payment_date):
    payment_date = getdate(payment_date)

    first_day_current_month = payment_date.replace(day=1)
    previous_month_end = add_days(first_day_current_month, -1)
    previous_month_start = previous_month_end.replace(day=1)

    return previous_month_start, previous_month_end

def apply_irtd_trigger_from_gl(gle, company):
    if not is_salary_payment_event(gle):
        return

    period_from, period_to = get_previous_month_period(
        gle.posting_date
    )

    tax_type = frappe.db.get_value(
        "Tax Type",
        {"company": company, "tax_code": "IRTD", "active": 1},
        "name",
    )
    if not tax_type:
        return

    obligation_name = frappe.db.get_value(
        "Fiscal Obligation",
        {
            "company": company,
            "tax_type": tax_type,
            "obligation_origin": "Operational",
            "fiscal_period_from": period_from,
            "fiscal_period_to": period_to,
            "docstatus": ["!=", 2],
        },
        "name",
    )

    if not obligation_name:
        return

    obligation = frappe.get_doc(
        "Fiscal Obligation",
        obligation_name,
    )

    if not obligation.trigger_date:
        obligation.trigger_date = gle.posting_date
        obligation.save(ignore_permissions=True)

def scan_irtd_salary_payment_triggers(
    company,
    salary_payable_account,
    from_date=None,
    to_date=None,
):
    filters = {
        "company": company,
        "account": salary_payable_account,
        "is_cancelled": 0,
        "debit": [">", 0],
    }

    if from_date:
        filters["posting_date"] = [">=", from_date]

    if from_date and to_date:
        filters["posting_date"] = [
            "between",
            [from_date, to_date],
        ]
    elif to_date:
        filters["posting_date"] = ["<=", to_date]

    entries = frappe.get_all(
        "GL Entry",
        filters=filters,
        fields=[
            "name",
            "posting_date",
            "account",
            "against",
            "debit",
            "credit",
            "voucher_type",
            "voucher_no",
        ],
        order_by="posting_date asc, creation asc",
    )

    for row in entries:
        gle = frappe._dict(row)

        if not is_salary_payment_event(gle):
            continue

        apply_irtd_trigger_from_gl(
            gle,
            company,
        )


