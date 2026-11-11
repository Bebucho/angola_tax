"""Invoice classification evidence, independent of Frappe and cash credits."""
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from angola_tax.industrial_tax.model import money
from angola_tax.industrial_tax.credit_model import quantized


TEXT_FIELDS = ('source_row', 'sales_invoice', 'posting_date', 'item_code',
               'invoice_currency', 'withholding_kind', 'withholding_fields_available')
AMOUNT_FIELDS = ('base_amount', 'invoice_net_amount', 'conversion_rate',
                 'invoice_withholding', 'invoice_withholding_total', 'invoice_withholding_base')


def evidence_hash(rows):
    values = []
    for row in rows:
        value = {key: str(row.get(key) or '') for key in TEXT_FIELDS}
        value.update({key: format(money(row.get(key)).normalize(), 'f') for key in AMOUNT_FIELDS})
        values.append(value)
    return hashlib.sha256(json.dumps(sorted(values, key=lambda r:r['source_row']),
                                     sort_keys=True).encode()).hexdigest()


def classify(rows, company_currency):
    """Only unambiguous line withholding is automatic; zero is not eligibility."""
    invoices = defaultdict(list)
    for row in rows:
        invoices[row['sales_invoice']].append(row)
    result = {}
    for invoice_rows in invoices.values():
        first = invoice_rows[0]
        issue = None
        has_withholding = any(
            money(row.get(key)) != 0
            for row in invoice_rows
            for key in ('invoice_withholding', 'invoice_withholding_total',
                        'invoice_withholding_base')
        )
        if not all(row.get('withholding_fields_available') for row in invoice_rows):
            issue = 'Invoice withholding fields unavailable; review fiscal classification.'
        elif has_withholding and (
                first.get('invoice_currency') != company_currency
                or money(first.get('conversion_rate')) != 1):
            issue = 'Foreign-currency withholding units require review.'
        elif any(any(row.get(key) != first.get(key) for key in
                     ('withholding_kind', 'invoice_withholding_total', 'invoice_withholding_base',
                      'invoice_currency', 'conversion_rate')) for row in invoice_rows):
            issue = 'Inconsistent invoice withholding header evidence.'
        else:
            withheld = [row for row in invoice_rows if money(row.get('invoice_withholding')) != 0]
            total = sum((money(row.get('invoice_withholding')) for row in invoice_rows), Decimal(0))
            base = sum((money(row.get('invoice_net_amount')) for row in withheld), Decimal(0))
            if abs(total - money(first.get('invoice_withholding_total'))) > Decimal('0.01'):
                issue = 'Line withholding does not reconcile to the invoice total.'
            elif abs(base - money(first.get('invoice_withholding_base'))) > Decimal('0.01'):
                issue = 'Withheld line bases do not reconcile to the invoice withholding base.'
            elif withheld and str(first.get('withholding_kind') or '').strip().upper() != 'FONTE':
                issue = 'Invoice withholding kind is not the identified FONTE mapping.'
            elif any(money(row.get('invoice_withholding')) != quantized(
                     money(row.get('invoice_net_amount')) * Decimal('0.065')) for row in withheld):
                issue = 'Withholding is partial or differs from the ordinary 6.5% line amount.'
        for row in invoice_rows:
            if issue:
                value = ('Needs Review', issue)
            elif money(row.get('invoice_withholding')):
                value = ('Excluded', 'Invoice line subject to II withholding: '
                         f"{money(row['invoice_withholding']):.2f} {company_currency}; "
                         'line amount and invoice header reconciled. No annual credit created.')
            else:
                value = ('Needs Review', 'No II withholding amount identified; review provisional eligibility.')
            result[row['source_row']] = value
    return result
