from angola_tax.reporting.access import Reader,column
from angola_tax.reporting.model import count_summary

FIELDS=[column('source_doctype','Tipo de Processo',width=180),column('source_name','Processo','Dynamic Link','source_doctype',180),
 column('tax_type','Imposto','Link','Tax Type',150),column('reference','Referência da Autoridade',width=190),column('received_on','Avaliação Recebida em','Date',width=170),column('notification_on','Data da Notificação','Date',width=165),
 column('decision','Decisão do Contribuinte',width=180),column('decision_due','Prazo para Decisão','Date',width=170),
 column('currency','Moeda','Link','Currency',80),column('claimed_amount','Montante Reclamado na Notificação','Currency','currency',235),column('assessed_amount','Montante Determinado','Currency','currency',175),
 column('disputed_amount','Montante Contestado na Decisão','Currency','currency',215),
 column('case_disputed_amount','Montante em Contencioso','Currency','currency',185),column('litigation','Contencioso','Link','Tax Litigation',180),column('case_status','Estado do Contencioso',width=180),
 column('stage','Fase Actual',width=200),column('phase_doctype','Tipo de Fase',width=160),column('phase','Documento da Fase','Dynamic Link','phase_doctype',190),
 column('submission_due','Prazo do Contribuinte','Date',width=175),column('submission_on','Apresentação em','Date'),
 column('response_due','Prazo de Resposta da Autoridade','Date',width=210),column('authority_response','Resposta da Autoridade',width=200),
 column('company_decision','Decisão na Fase',width=190),column('provision','Provisão','Link','Tax Provision',180),
 column('provision_review','Revisão da Provisão',width=170),column('responsible_user','Responsável','Link','User',200),
 column('review_note','Observação',width=320)]

CLOSED_CASES={'Closed','Won','Lost','Agreement'}

def phase_facts(reader,case):
    if not case:return {}
    phase=reader.linked(case.current_phase_doctype,case.current_phase) if case.current_phase_doctype and case.current_phase else None
    provision=reader.linked('Tax Provision',case.tax_provision)
    result=dict(litigation=case.name,case_status=case.status,stage=case.current_stage,case_disputed_amount=case.amount_under_dispute,
        provision=provision.name if provision else None,provision_review=case.provision_review_status)
    if phase:result.update(phase_doctype=phase.doctype,phase=phase.name,submission_due=phase.company_submission_due_date,
        submission_on=phase.submission_date,response_due=phase.response_due_date,authority_response=phase.agt_response,
        company_decision=phase.company_decision)
    return result

def execute(filters=None):
    reader=Reader(filters);rows=[]
    # Separate inaccessible document types from empty case populations.
    assessments=reader.documents('Tax Assessment',required=False)
    notifications=reader.documents('Tax Notification',required=False)
    cases=reader.documents('Tax Litigation',required=False)
    if not any(reader.can_read_type(dt) for dt in ('Tax Assessment','Tax Notification','Tax Litigation')):
        import frappe
        frappe.throw('Sem acesso aos processos de decisão ou contencioso fiscal.',frappe.PermissionError)
    case_by_assessment={}
    for case in cases:
        if not reader.filters.include_closed and case.status in CLOSED_CASES:continue
        case_by_assessment.setdefault(case.tax_assessment,[]).append(case)
    covered_cases=set();represented_assessments=set()
    for assessment in assessments:
        if assessment.superseded_by or assessment.assessment_decision=='Cancelled by AGT':continue
        linked=case_by_assessment.get(assessment.name,[])
        # One row per assessment/case. Never sum assessment and litigation balances together.
        candidates=linked or [None]
        for case in candidates:
            if not reader.filters.include_closed and assessment.assessment_decision not in ('Pending','Disputed') and not case:continue
            if not reader.matches_tax(assessment.tax_type) or not reader.matches_owner(assessment.responsible_user):continue
            if not reader.date_matches(assessment.date_received):continue
            if not reader.matches_status(case.status if case else assessment.assessment_decision):continue
            row=dict(source_doctype='Tax Assessment',source_name=assessment.name,tax_type=assessment.tax_type if reader.shared('Tax Type',assessment.tax_type) else None,
                reference=assessment.assessment_reference,received_on=assessment.date_received,decision=assessment.assessment_decision,
                decision_due=assessment.action_due_date or assessment.legal_time_limit_date,currency=assessment.currency,
                assessed_amount=assessment.effective_assessed_amount,disputed_amount=assessment.disputed_amount,
                responsible_user=assessment.responsible_user,
                review_note='Prazo de decisão não registado.' if assessment.assessment_decision=='Pending' and not (assessment.action_due_date or assessment.legal_time_limit_date) else '')
            if assessment.name in represented_assessments:
                row['assessed_amount']=None;row['disputed_amount']=None
                row['review_note']='Montantes da avaliação apresentados na primeira linha deste processo.'
            row.update(phase_facts(reader,case));rows.append(row)
            represented_assessments.add(assessment.name)
            if case:covered_cases.add(case.name)
    for notification in notifications:
        if notification.tax_assessment in represented_assessments:continue
        if not reader.filters.include_closed and notification.decision:continue
        if not reader.matches_tax(notification.tax_type) or not reader.matches_owner(notification.responsible_user):continue
        if not reader.date_matches(notification.notification_date) or not reader.matches_status(notification.decision or 'Pending'):continue
        case=reader.linked('Tax Litigation',notification.tax_litigation)
        if case and not reader.filters.include_closed and case.status in CLOSED_CASES:case=None
        if case and case.name in covered_cases:case=None
        obligation=reader.linked('Fiscal Obligation',notification.fiscal_obligation)
        row=dict(source_doctype='Tax Notification',source_name=notification.name,tax_type=notification.tax_type if reader.shared('Tax Type',notification.tax_type) else None,
            reference=notification.notification_reference,received_on=None,notification_on=notification.notification_date,
            decision=notification.decision or None,decision_due=None,responsible_user=notification.responsible_user,
            assessed_amount=None,disputed_amount=None,claimed_amount=notification.claimed_amount,currency=obligation.currency if obligation else None,
            review_note='Montante reclamado apresentado separadamente da avaliação e do contencioso. O prazo de resposta ainda não está registado; consultar a notificação e a fase aplicável.')
        row.update(phase_facts(reader,case));rows.append(row)
        if case:covered_cases.add(case.name)
    for case in cases:
        if case.name in covered_cases or (not reader.filters.include_closed and case.status in CLOSED_CASES):continue
        if not reader.matches_tax(case.tax_type) or not reader.matches_status(case.status):continue
        assessment=reader.linked('Tax Assessment',case.tax_assessment)
        if reader.filters.from_date or reader.filters.to_date:
            if not assessment or not reader.date_matches(assessment.date_received):continue
        if reader.filters.responsible_user and (not assessment or not reader.matches_owner(assessment.responsible_user)):continue
        row=dict(source_doctype='Tax Litigation',source_name=case.name,tax_type=case.tax_type if reader.shared('Tax Type',case.tax_type) else None,
            reference=case.case_number,received_on=assessment.date_received if assessment else None,
            decision=assessment.assessment_decision if assessment else None,
            currency=assessment.currency if assessment else None,assessed_amount=None,disputed_amount=None,
            responsible_user=assessment.responsible_user if assessment else None,
            review_note='Contencioso apresentado separadamente. Montantes da avaliação não são repetidos.')
        row.update(phase_facts(reader,case));rows.append(row)
    rows.sort(key=lambda r:(str(r.get('received_on') or r.get('notification_on') or ''),r['source_doctype'],r['source_name'],str(r.get('litigation') or '')))
    summary=[count_summary(rows,'Processos Apresentados'),
        {'label':'Avaliações Distintas','value':len({r['source_name'] for r in rows if r['source_doctype']=='Tax Assessment'}),'datatype':'Int','indicator':'Blue'},
        {'label':'Contenciosos Distintos','value':len({r['litigation'] for r in rows if r.get('litigation')}),'datatype':'Int','indicator':'Orange'}]
    note=reader.note('Posição actual das decisões e do contencioso. Datas seleccionam a recepção da avaliação ou a data da notificação, conforme o documento. Decisões, fases e prazos são lidos dos documentos existentes. Não se somam exposição da avaliação, notificação e contencioso, para evitar duplicação. Documentos históricos/inactivos ficam excluídos por defeito.')
    return FIELDS,rows,note,None,summary,True
