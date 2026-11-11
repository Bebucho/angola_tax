"""Pure calculation and calendar functions. No framework dependency."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def money(value):
    try:
        result = Decimal(str(value or 0))
    except InvalidOperation as exc:
        raise ValueError("Invalid monetary amount") from exc
    if not result.is_finite():
        raise ValueError("Monetary amounts must be finite")
    return result


def annual(pbt, adjustments, rate, precision=2):
    rate = money(rate)
    if rate != Decimal("25"):
        raise ValueError("First package supports only ordinary general-regime 25%")
    if precision not in (0, 2):
        raise ValueError("Approve an explicit supported tax precision (0 or 2)")
    addbacks = deductions = Decimal(0)
    for row in adjustments:
        amount = money(row["amount"])
        if amount < 0 or row["kind"] not in ("Add-back", "Deduction"):
            raise ValueError("Adjustments require a positive amount and fiscal direction")
        if amount and (not row.get("legal_reference") or not row.get("evidence")):
            raise ValueError("Every adjustment requires legal reference and evidence")
        if row["kind"] == "Add-back":
            addbacks += amount
        else:
            deductions += amount
    result = money(pbt) + addbacks - deductions
    base = max(result, Decimal(0))
    raw = base * rate / 100
    rounded = raw.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
    return dict(pbt=money(pbt), addbacks=addbacks, deductions=deductions,
                fiscal_result=result, taxable_base=base, tax_raw=raw,
                tax=rounded, rounding=rounded - raw)


def true_up(tax, prior):
    return (money(tax) - money(prior)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def company_currency(value):
    if value not in ("AOA", "KZ"):
        raise ValueError("First II package requires company currency AOA or its configured KZ code")
    return value


def accepts_tax_expense_account(root_type, account_number):
    # PGC class 87 is a results account. Its explicit fiscal role is authoritative
    # even when the existing ERPNext chart places that class under Equity.
    number = str(account_number or "").strip()
    return root_type == "Expense" or (
        root_type == "Equity" and number.isdigit() and number.startswith("87")
    )


def annual_due(period_end, offset, holidays=()):
    if period_end.month != 12 or period_end.day != 31 or offset != 5:
        raise ValueError("First annual adapter requires December year-end and May deadline")
    due = date(period_end.year + 1, 5, monthrange(period_end.year + 1, 5)[1])
    holidays = set(holidays)
    while due.weekday() >= 5 or due in holidays:
        due -= timedelta(days=1)
    return due


def exposure(determined, assessed=None, settlements=None, paid=0):
    # None distinguishes an absent assessment from a genuine zero assessment.
    amount = (sum((money(v) for v in settlements), Decimal(0))
              if settlements else money(assessed if assessed is not None else determined))
    return amount, max(amount - money(paid), Decimal(0))


def accepts_tax_credit_account(root_type, account_number, role):
    """Explicit fiscal debit roles in the existing PGC liability hierarchy."""
    prefixes = {
        "Suffered Withholding Asset": "3415",
        "Pending Suffered Withholding Asset": "3414",
        "Provisional Asset": "3419",
    }
    if role not in (*prefixes, "Recognised Carryforward Credit"):
        return False
    if root_type == "Asset":
        return True
    number = str(account_number or "").strip()
    prefix = prefixes.get(role)
    return bool(root_type == "Liability" and prefix and number.isdigit()
                and number.startswith(prefix))
