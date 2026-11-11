"""Aggregation and filtering helpers only; source amounts/statuses remain authoritative."""
from collections import defaultdict
from decimal import Decimal

def number(value):return Decimal(str(value or 0))

def amounts_by_currency(rows,fields,eligible=None):
    buckets=defaultdict(lambda:{field:Decimal(0) for field in fields})
    for row in rows:
        if eligible and not eligible(row):continue
        currency=row.get('currency')
        if not currency:continue  # Never assign or mix an unknown denomination.
        for field in fields:buckets[currency][field]+=number(row.get(field))
    return dict(buckets)

def summary_amounts(buckets,labels):
    values=[]
    for currency in sorted(buckets):
        for field,label in labels.items():
            values.append({'label':label+' — '+currency,'value':buckets[currency][field],
                'datatype':'Currency','currency':currency,'indicator':'Blue'})
    return values

def count_summary(rows,label='Registos'):
    return {'label':label,'value':len(rows),'datatype':'Int','indicator':'Blue'}
