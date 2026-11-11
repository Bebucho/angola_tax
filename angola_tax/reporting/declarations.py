from datetime import date
import frappe
from frappe.utils import getdate,nowdate
from angola_tax.reporting.access import Reader,column
from angola_tax.reporting.model import count_summary

FIELDS=[column('entry_kind','Registo',width=165),column('declaration','Declaração','Link','Tax Declaration',180),
 column('configuration','Aplicabilidade e Prazos','Link','Tax Deadline Configuration',180),
 column('declaration_type','Tipo de Declaração','Link','Tax Declaration Type',180),column('statutory_name','Designação',width=240),
 column('period_start','Início do Período','Date'),column('period_end','Fim do Período','Date'),column('applicability','Aplicabilidade'),
 column('revision','Versão','Int',width=80),column('preparation_state','Preparação',width=130),column('delivery_state','Entrega na Autoridade',width=175),
 column('chain_state','Situação das Versões',width=180),column('effective_accepted_version','Versão Aceite Efectiva','Link','Tax Declaration',190),
 column('legal_due','Prazo Legal','Date'),column('effective_due','Prazo Aplicável','Date'),column('penalty_free_until','Dispensa de Penalidade Até','Date',width=190),
 column('overdue','Em Atraso','Check',width=90),column('days_overdue','Dias de Atraso','Int',width=110),
 column('freshness','Verificação das Fontes',width=175),column('last_checked_on','Última Verificação','Datetime',width=170),
 column('submission_reference','Referência de Submissão',width=180),column('submission_on','Submissão em','Datetime',width=160),
 column('acknowledged_on','Recepção pela Autoridade em','Datetime',width=190),column('accepted_on','Aceite pela Autoridade em','Datetime',width=185),
 column('submission_evidence','Comprovativo de Submissão','Link','File',185),column('response_evidence','Comprovativo da Resposta','Link','File',185),
 column('responsible_user','Responsável','Link','User',200)]

def execute(filters=None):
    reader=Reader(filters);rows=[]
    for doc in reader.documents('Tax Declaration'):
        if not reader.filters.all_versions and not doc.is_latest_version:continue
        if reader.filters.declaration_type and doc.declaration_type!=reader.filters.declaration_type:continue
        if not reader.date_matches(doc.period_end) or not reader.matches_owner(doc.responsible_user):continue
        if not reader.matches_status(doc.delivery_state):continue
        typ=reader.shared('Tax Declaration Type',doc.declaration_type)
        submission=next((e for e in reversed(doc.events or []) if e.action=='Submission'),None)
        responses=[e for e in doc.events or [] if e.action in ('Acknowledged','Accepted','Rejected') and submission and e.attempt_id==submission.attempt_id]
        ack=next((e for e in reversed(responses) if e.action=='Acknowledged'),None)
        accepted=next((e for e in reversed(responses) if e.action=='Accepted'),None)
        accepted_version=reader.linked('Tax Declaration',doc.effective_accepted_version)
        rows.append(dict(entry_kind='Declaração',declaration=doc.name,configuration=doc.deadline_configuration if reader.linked('Tax Deadline Configuration',doc.deadline_configuration) else None,
            declaration_type=doc.declaration_type if typ else None,statutory_name=typ.statutory_name if typ else None,
            period_start=doc.period_start,period_end=doc.period_end,applicability=doc.applicability,revision=doc.revision,
            preparation_state=doc.preparation_state,delivery_state=doc.delivery_state,chain_state=doc.chain_state,
            effective_accepted_version=accepted_version.name if accepted_version else None,legal_due=doc.legal_due_date,effective_due=doc.effective_due_date,
            penalty_free_until=doc.penalty_free_until,overdue=doc.is_overdue,days_overdue=doc.days_overdue,
            freshness=doc.freshness,last_checked_on=doc.last_checked_on,
            submission_reference=submission.external_reference if submission else None,submission_on=submission.occurred_on if submission else None,
            acknowledged_on=ack.occurred_on if ack else None,accepted_on=accepted.occurred_on if accepted else None,
            submission_evidence=reader.evidence(submission.evidence,'Tax Declaration',doc.name) if submission else None,
            response_evidence=reader.evidence(responses[-1].evidence,'Tax Declaration',doc.name) if responses else None,
            responsible_user=doc.responsible_user))
    missing=0;expected_available=True
    if reader.filters.get('include_missing',1) and not reader.filters.status:
        if not reader.can_read_type('Tax Deadline Configuration'):expected_available=False
        else:
            from angola_tax.declarations.service import expected_filings
            from angola_tax.declarations.configuration import resolve
            today=getdate(nowdate());horizon=today.replace(year=today.year-1,month=1,day=1)
            start=max(getdate(reader.filters.from_date),horizon) if reader.filters.from_date else horizon
            end=min(getdate(reader.filters.to_date),today) if reader.filters.to_date else today
            if end>=start:
                for config,period_start,period_end,key in expected_filings(start,end,company=reader.company):
                    if reader.linked('Tax Deadline Configuration',config.name) is None:continue
                    if reader.filters.declaration_type and config.declaration_type!=reader.filters.declaration_type:continue
                    if not reader.matches_owner(config.declaration_responsible_user):continue
                    # Existing occurrence detection is scoped to one company and shared calendar key.
                    if frappe.db.exists('Tax Declaration',{'company':reader.company,'original_key':key,'docstatus':['!=',2]}):continue
                    typ=reader.shared('Tax Declaration Type',config.declaration_type)
                    due=resolve(config,period_start,period_end)
                    rows.append(dict(entry_kind='Obrigação prevista — sem registo',declaration=None,configuration=config.name,
                        declaration_type=config.declaration_type if typ else None,statutory_name=typ.statutory_name if typ else None,
                        period_start=period_start,period_end=period_end,applicability=config.declaration_applicability,
                        legal_due=due['legal_due_date'],effective_due=due['effective_due_date'],responsible_user=config.declaration_responsible_user))
                    missing+=1
    rows.sort(key=lambda r:(str(r.get('effective_due') or ''),str(r.get('declaration_type') or ''),str(r.get('declaration') or r.get('configuration') or '')))
    summary=[count_summary([r for r in rows if r.get('declaration')],'Declarações Registadas'),
        {'label':'Obrigações Previstas Sem Registo','value':missing,'datatype':'Int','indicator':'Orange'},
        {'label':'Declarações em Atraso','value':sum(bool(r.get('overdue')) for r in rows),'datatype':'Int','indicator':'Red'},
        {'label':'Fontes Alteradas','value':sum(r.get('freshness')=='Sources Changed' for r in rows),'datatype':'Int','indicator':'Orange'}]
    note=reader.note('Datas seleccionam o fim do período fiscal. Estados de preparação, entrega e versões provêm da declaração. Aprovação interna e ficheiro gerado não equivalem a submissão ou aceitação. Obrigações sem registo provêm do calendário existente, desde Janeiro do ano anterior até hoje; não recebem um estado de declaração inventado.')
    if not expected_available:note+=' Sem acesso à configuração: obrigações previstas não foram avaliadas.'
    return FIELDS,rows,note,None,summary,True
