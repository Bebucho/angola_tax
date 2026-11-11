from angola_tax.reporting.access import Reader,column
from angola_tax.reporting.model import amounts_by_currency,summary_amounts,count_summary

FIELDS=[column('collection_reference','Referência de Cobrança','Link','Tax Collection Reference',190),
 column('fiscal_obligation','Obrigação Fiscal','Link','Fiscal Obligation',170),column('tax_type','Imposto','Link','Tax Type',150),
 column('nota_liquidacao','Nota de Liquidação',width=175),column('rupe','RUPE',width=180),column('status','Estado da Cobrança',width=170),
 column('currency','Moeda','Link','Currency',80),column('reference_amount','Montante da Referência','Currency','currency',180),
 column('confirmed_amount','Confirmado no Controlo','Currency','currency',180),column('principal_amount','Principal','Currency','currency'),
 column('penalty_amount','Penalidades','Currency','currency'),column('issue_date','Emitida em','Date'),column('due_date','Vencimento','Date'),
 column('payment_date','Pagamento em','Date'),column('tax_payment','Pagamento Fiscal','Link','Tax Payment',180),
 column('payment_status','Estado do Pagamento',width=170),column('payment_plan','Plano de Pagamento','Link','Tax Payment Plan',180),
 column('instalment_no','Prestação','Int',width=90),column('dc_pago_reference','Referência DC Pago',width=175),
 column('dc_pago_date','DC Pago em','Date'),column('evidence','Comprovativo','Link','File',180),
 column('review_note','Observação',width=320)]

def execute(filters=None):
    reader=Reader(filters);rows=[]
    mode=reader.filters.date_basis or 'Payment'
    field={'Payment':'payment_date','Issue':'issue_date','Due':'due_date'}.get(mode)
    if not field:
        import frappe
        frappe.throw('Seleccione a data do pagamento, emissão ou vencimento.')
    for reference in reader.documents('Tax Collection Reference'):
        if not reader.filters.include_inactive and reference.status in ('Cancelled','Superseded'):continue
        if not reader.matches_status(reference.status) or not reader.date_matches(reference.get(field)):continue
        obligation=reader.linked('Fiscal Obligation',reference.fiscal_obligation)
        payment=reader.linked('Tax Payment',reference.tax_payment)
        plan=reader.linked('Tax Payment Plan',reference.payment_plan)
        tax_type=obligation.tax_type if obligation else payment.tax_type if payment else plan.tax_type if plan else None
        if not reader.matches_tax(tax_type):continue
        owner=obligation.responsible_user if obligation else None
        if not reader.matches_owner(owner):continue
        # The reference has no currency field: use a readable authoritative fiscal parent only.
        currency=obligation.currency if obligation else payment.currency if payment else plan.currency if plan else None
        note=[]
        evidence=reader.evidence(payment.dc_pago,'Tax Payment',payment.name) if payment else None
        if reference.status=='Confirmed' and (not payment or payment.status!='Confirmed'):
            note.append('Confirmação da cobrança sem pagamento confirmado acessível; rever o processo.')
        if reference.status=='Confirmed' and not evidence:note.append('Comprovativo DC Pago não acessível ou em falta.')
        if not currency:note.append('Moeda não disponível; montante excluído dos totais monetários.')
        rows.append(dict(collection_reference=reference.name,fiscal_obligation=obligation.name if obligation else None,
            tax_type=tax_type if reader.shared('Tax Type',tax_type) else None,nota_liquidacao=reference.nota_liquidacao,rupe=reference.rupe,status=reference.status,currency=currency,
            reference_amount=reference.amount,confirmed_amount=reference.amount if reference.status=='Confirmed' else None,
            principal_amount=reference.principal_amount,penalty_amount=reference.penalty_amount,
            issue_date=reference.issue_date,due_date=reference.due_date,payment_date=reference.payment_date,
            tax_payment=payment.name if payment else None,payment_status=payment.status if payment else None,
            payment_plan=plan.name if plan else None,instalment_no=reference.instalment_no,
            dc_pago_reference=payment.dc_pago_reference if payment else None,dc_pago_date=payment.dc_pago_date if payment else None,
            evidence=evidence,review_note=' '.join(note)))
    rows.sort(key=lambda r:(str(r.get(field) or ''),r['collection_reference']))
    totals=amounts_by_currency(rows,['confirmed_amount'])
    summary=[count_summary(rows,'Referências de Cobrança'),
        {'label':'Referências Confirmadas','value':sum(r['status']=='Confirmed' for r in rows),'datatype':'Int','indicator':'Green'}]+summary_amounts(totals,{'confirmed_amount':'Confirmado no Controlo'})
    note=reader.note('Uma linha por referência de cobrança. A confirmação provém do estado da referência, conforme o controlo de pagamentos existente; um lançamento contabilístico não confirma pagamento. Comprovativos e eventuais inconsistências são apresentados separadamente. Totais não incluem moedas desconhecidas.')
    return FIELDS,rows,note,None,summary,True
