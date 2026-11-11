"""Framework-independent credit bridge, base classification and calendar."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from angola_tax.industrial_tax.model import money


def quantized(value, precision=2):
    if precision not in (0, 2):
        raise ValueError('Approve supported monetary precision (0 or 2)')
    return money(value).quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)


def provisional(rows, precision=2, exemption=None):
    eligible = Decimal(0)
    for row in rows:
        if row.get('classification') not in ('Eligible', 'Excluded'):
            raise ValueError('Review every H1 invoice line before approval')
        if not row.get('reason'):
            raise ValueError('Each H1 classification requires a reason')
        if row['classification'] == 'Eligible':
            eligible += money(row['amount'])
    if eligible < 0:
        raise ValueError('Negative net H1 base requires a reviewed correction')
    raw = Decimal(0) if exemption else eligible * Decimal('0.02')
    return eligible, raw, quantized(raw, precision)


def bridge(gross, requests):
    gross = money(gross)
    total = sum((money(v) for v in requests), Decimal(0))
    if gross < 0 or total < 0 or any(money(v) < 0 for v in requests):
        raise ValueError('Tax and credit allocations must be nonnegative')
    if total > gross:
        raise ValueError('Allocate at most the gross tax; retain unused credits in the register')
    return total, gross - total


def available(amount, committed):
    remaining = money(amount) - money(committed)
    if remaining < 0:
        raise ValueError('Credit overconsumption detected')
    return remaining


def last_working_month(period_end, offset, holidays=()):
    serial = period_end.year * 12 + period_end.month - 1 + int(offset)
    year, month_zero = divmod(serial, 12)
    month = month_zero + 1
    due = date(year, month, monthrange(year, month)[1])
    holidays = set(holidays)
    while due.weekday() >= 5 or due in holidays:
        due -= timedelta(days=1)
    return due
