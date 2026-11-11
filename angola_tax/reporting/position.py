import frappe
from angola_tax.reporting.access import Reader,column
from angola_tax.reporting.model import amounts_by_currency,summary_amounts,count_summary
from angola_tax.statutory_deadline import get_statutory_payment_due_date

FIELDS=[column('obligation','Obrigação Fiscal','Link','Fiscal Obligation',170),column('tax_type','Imposto','Link','Tax Type',160),
 column('tax_name','Designação',width=200),column('period_start','Início do Período','Date'),column('period_end','Fim do Período','Date'),
 column('obligation_type','Natureza'),column('status','Estado'),column('approved','Aprovada','Check',width=90),
 column('currency','Moeda','Link','Currency',80),column('determined','Apurado','Currency','currency'),
 column('assessed','Determinado pela Autoridade','Currency','currency',180),column('effective_amount','Montante Efectivo','Currency','currency',165),
 column('credits_applied','Créditos Aplicados','Currency','currency',160),column('confirmed_paid','Pagamento Confirmado','Currency','currency',175),
 column('payable_outstanding','Por Pagar','Currency','currency'),column('receivable_outstanding','Por Recuperar','Currency','currency'),
 column('payment_due','Prazo de Pagamento','Date',width=165),column('overdue','Em Atraso','Check',width=90),
 column('under_dispute','Em Contestação','Check',width=110),column('payment_plan','Plano de Pagamento','Check',width=155),
 column('responsible_user','Responsável','Link','User',200),column('compliance_message','Acção Pendente',width=320)]

def execute(filters=None):
    reader=Reader(filters);rows=[]
    for doc in reader.documents('Fiscal Obligation'):
        if doc.status in ('Cancelled','Superseded') or doc.superseded_by:continue
        if not reader.filters.include_closed and doc.status in ('Closed','Reconciled'):continue
        if not reader.filters.include_drafts and doc.docstatus!=1:continue
        if not reader.matches_tax(doc.tax_type) or not reader.matches_status(doc.status) or not reader.matches_owner(doc.responsible_user):continue
        if not reader.period_matches(doc.fiscal_period_from,doc.fiscal_period_to):continue
        if reader.filters.outstanding_only and not doc.outstanding_amount:continue
        rows.append(dict(obligation=doc.name,tax_type=doc.tax_type if reader.shared('Tax Type',doc.tax_type) else None,tax_name=doc.tax_name,
            period_start=doc.fiscal_period_from,period_end=doc.fiscal_period_to,obligation_type=doc.obligation_type,
            status=doc.status,approved=int(doc.docstatus==1),currency=doc.currency,
            determined=doc.amount_determined,assessed=doc.amount_assessed,effective_amount=doc.effective_obligation_amount,
            credits_applied=doc.credit_applied_amount,confirmed_paid=doc.amount_paid,
            payable_outstanding=doc.outstanding_amount if doc.obligation_type=='Payable' else None,
            receivable_outstanding=doc.outstanding_amount if doc.obligation_type=='Receivable' else None,
            payment_due=get_statutory_payment_due_date(doc),overdue=doc.is_overdue,under_dispute=doc.under_dispute,
            payment_plan=doc.payment_plan,responsible_user=doc.responsible_user,compliance_message=doc.compliance_message))
    rows.sort(key=lambda r:(str(r['tax_type'] or ''),str(r['period_end'] or ''),r['obligation']))
    totals=amounts_by_currency(rows,['payable_outstanding','receivable_outstanding','confirmed_paid','credits_applied'],lambda r:r['approved'])
    summary=[count_summary(rows,'Obrigações Apresentadas')]+summary_amounts(totals,{
        'payable_outstanding':'Por Pagar','receivable_outstanding':'Por Recuperar','confirmed_paid':'Pagamentos Confirmados','credits_applied':'Créditos Aplicados'})
    chart=None
    if len(totals)==1:
        currency=next(iter(totals));groups={}
        for row in rows:
            if not row['approved'] or row['currency']!=currency:continue
            key=row['tax_name'] or row['tax_type'] or 'Sem classificação'
            groups[key]=groups.get(key,0)+float(row['payable_outstanding'] or 0)
        if groups:chart={'data':{'labels':list(groups),'datasets':[{'name':'Por Pagar — '+currency,'values':list(groups.values())}]},'type':'bar','height':250}
    note=reader.note('Posição actual dos processos. Os filtros seleccionam o período fiscal; não representam um saldo histórico à data. Totais monetários incluem apenas obrigações aprovadas e mantêm moedas e valores a recuperar separados. Não são efectuadas actualizações ou novos cálculos fiscais.')
    return FIELDS,rows,note,chart,summary,True
