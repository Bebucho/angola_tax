"""Source-independent H1 revenue summaries; all amounts in company currency."""
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from angola_tax.industrial_tax.model import money
from angola_tax.industrial_tax.invoice_classification import classify, evidence_hash


def source_hash(gl_rows, invoices, mapping):
    # Individual movement identity and amounts are hashed, not merely totals.
    gl = [{key: str(row.get(key) or '') for key in
           ('name','account','posting_date','voucher_type','voucher_no','is_opening')} |
          {key: format(money(row.get(key)).normalize(),'f') for key in ('debit','credit')}
          for row in gl_rows]
    invoice_accounts = sorted((str(r['source_row']),str(r.get('income_account') or '')) for r in invoices)
    rules = sorted((str(r['account']), str(r['classification']), str(r.get('reason') or ''),
                    str(r.get('legal_reference') or '')) for r in mapping)
    return hashlib.sha256(json.dumps(dict(gl=sorted(gl,key=lambda r:r['name']),
        invoices=evidence_hash(invoices),invoice_accounts=invoice_accounts,rules=rules),
        sort_keys=True).encode()).hexdigest()


def aggregate(gl_rows, invoices, mapping, currency):
    totals = defaultdict(lambda: dict(debit=Decimal(0),credit=Decimal(0),
        invoice_revenue=Decimal(0),captured_withholding_base=Decimal(0),
        withholding_exception_count=0,gl_count=0,invoice_line_count=0))
    rules = {r['account']:r for r in mapping}
    if len(rules) != len(mapping):raise ValueError('Duplicate revenue account classification')
    for row in gl_rows:
        account = row['account'];total=totals[account]
        total['debit'] += money(row.get('debit'));total['credit'] += money(row.get('credit'))
        total['gl_count'] += 1
    decisions = classify(invoices,currency)
    exception_invoices = defaultdict(set)
    for row in invoices:
        account = row.get('income_account')
        if not account:raise ValueError('Invoice income account is missing')
        total=totals[account];total['invoice_revenue'] += money(row['base_amount'])
        total['invoice_line_count'] += 1
        classification,reason = decisions[row['source_row']]
        if classification == 'Excluded':total['captured_withholding_base'] += money(row['base_amount'])
        elif not reason.startswith('No II withholding amount identified;'):
            exception_invoices[account].add(row['sales_invoice'])
    result=[]
    for account,total in sorted(totals.items()):
        rule=rules.get(account,{})
        revenue=total['credit']-total['debit']
        total.update(account=account,classification=rule.get('classification','Unmapped'),
                     classification_reason=rule.get('reason',''),gl_revenue=revenue,
                     reconciliation_difference=revenue-total['invoice_revenue'],
                     withholding_exception_count=len(exception_invoices[account]))
        total['withholding_base']=(Decimal(0) if total['classification']=='Excluded'
                                   else total['captured_withholding_base'])
        total['other_exclusions']=Decimal(0)
        result.append(total)
    return result


def calculate(rows, strict=False):
    revenue=withheld=excluded=unexplained=Decimal(0);pending=0
    for row in rows:
        gl=money(row.get('gl_revenue'));wht=money(row.get('withholding_base'))
        other=money(row.get('other_exclusions'));policy=row.get('classification')
        captured=money(row.get('captured_withholding_base'))
        evidence=bool(row.get('review_reason') and row.get('review_evidence'))
        exceptional=(bool(row.get('withholding_exception_count')) or wht!=captured)
        if policy=='Excluded':
            # Whole-account exclusion supersedes withholding: never deduct twice.
            if wht or other:
                if strict:raise ValueError('Excluded accounts cannot also deduct withholding or other exclusions')
                pending+=1
            excluded+=gl
        elif policy in ('Eligible','Mixed'):
            # Signed credit notes can reduce one account while other accounts stay
            # positive. Bound exclusions by gross movements, not the net balance.
            credit=money(row.get('credit'));debit=money(row.get('debit'))
            if not (-debit <= wht <= credit and -debit <= wht+other <= credit):
                if strict:raise ValueError('Exclusions exceed captured account revenue movements')
                pending+=1
            withheld+=wht;excluded+=other
            if (exceptional or policy=='Mixed' or other) and not (row.get('exceptions_reviewed') and evidence):pending+=1
        else:pending+=1
        difference=money(row.get('reconciliation_difference'))
        if abs(difference)>Decimal('0.01') and not (row.get('difference_explained') and evidence):
            unexplained+=abs(difference)
        revenue+=gl
    if strict and (pending or unexplained):raise ValueError('Resolve unmapped accounts, withholding exceptions and unexplained GL differences before approval')
    return dict(revenue_total=revenue,withholding_base_total=withheld,
                other_exclusions_total=excluded,unexplained_difference=unexplained,
                pending_account_count=pending,taxable_base=revenue-withheld-excluded)
