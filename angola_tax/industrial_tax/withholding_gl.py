"""Monthly collected withholding adopts the posted ledger, without manual registers."""
from collections import defaultdict
from decimal import Decimal
import frappe
from frappe.utils import getdate, now
from angola_tax.industrial_tax import agt_model

FORMAT = 'GL Withholding v1'
FACTS = ('gl_entry','posting_date','voucher_type','voucher_no','account','party_type','party','debit','credit')


def query(doc):
    from angola_tax.industrial_tax.mechanisms import mechanism_context, ROLES
    from angola_tax.industrial_tax.configuration import role_account
    profile = mechanism_context(doc)
    account = role_account(profile, ROLES[profile.mechanism][0], doc.fiscal_period_to)
    rows = frappe.get_all('GL Entry', filters=dict(company=doc.company, account=account,
        is_cancelled=0, posting_date=['between',[doc.fiscal_period_from,doc.fiscal_period_to]]),
        fields=['name as gl_entry','posting_date','voucher_type','voucher_no','account','party_type','party',
                'debit','credit','is_opening'], order_by='name', limit_page_length=0)
    return [r for r in rows if r.get('is_opening') != 'Yes' and r.voucher_type != 'Period Closing Voucher'
            and (agt_model.number(r.debit) or agt_model.number(r.credit))]


def facts(rows):
    return sorted([{k:(str(getdate(r.get(k))) if k=='posting_date' else
        str(agt_model.number(r.get(k)).normalize()) if k in ('debit','credit') else r.get(k) or '')
        for k in FACTS} for r in rows], key=lambda r:r['gl_entry'])


def settlement_vouchers(doc):
    payments=frappe.get_all('Tax Payment',filters=dict(company=doc.company,tax_type=doc.tax_type,docstatus=1,status='Confirmed'),
        fields=['source_voucher_type','source_voucher','journal_entry','originating_voucher_type','originating_voucher'],limit_page_length=0)
    result=set()
    for p in payments:
        for kind,name in ((p.source_voucher_type,p.source_voucher),('Journal Entry',p.journal_entry),
                          (p.originating_voucher_type,p.originating_voucher)):
            if kind and name:result.add((kind,name))
    transfers=frappe.get_all('Tax Settlement',filters=dict(company=doc.company,tax_type=doc.tax_type,docstatus=1),
        fields=['settlement_journal_entry'],limit_page_length=0)
    result.update(('Journal Entry',r.settlement_journal_entry) for r in transfers if r.settlement_journal_entry)
    return result


def classification(row, voucher, settlements=()):
    """Debits are never silently deducted from the month's recognised tax."""
    if voucher.get('docstatus') != 1:
        return 'Review'
    if (row.voucher_type,row.voucher_no) in settlements:
        return 'Settlement'
    if row.voucher_type=='Purchase Invoice' and voucher.get('is_return') and agt_model.number(row.debit)>0 and not agt_model.number(row.credit):
        return 'Reversal'
    if agt_model.number(row.credit)>0 and not agt_model.number(row.debit):
        return 'Recognition'
    return 'Review'


def amounts(rows, require_review=False):
    tax = settlement = Decimal(0)
    pending = 0
    seen = set()
    for r in rows:
        if not r.get('gl_entry') or r.gl_entry in seen:
            frappe.throw('Duplicate or missing withholding GL entry.')
        seen.add(r.gl_entry)
        net = agt_model.number(r.credit)-agt_model.number(r.debit)
        kind = r.get('classification')
        if kind=='Review': pending+=1; continue
        if kind not in ('Recognition','Reversal','Settlement','Excluded'):
            frappe.throw('Select a valid GL movement classification.')
        if kind=='Recognition' and net<=0: frappe.throw('Recognition requires a liability credit.')
        if kind=='Reversal' and net>=0: frappe.throw('Reversal requires a liability debit.')
        if kind in ('Recognition','Reversal'): tax+=net
        if kind=='Settlement': settlement-=net
        if require_review and kind!=r.get('captured_classification') and not r.get('reason'):
            frappe.throw('Explain only the GL movement classifications changed during review.')
    if require_review and pending: frappe.throw('Resolve the GL movements marked Review before submitting.')
    if tax<0: frappe.throw('Net withholding recognition is negative; use a controlled fiscal correction.')
    return dict(tax=float(tax),settlement=float(settlement),pending=pending)


def capture(doc):
    if doc.docstatus!=0: frappe.throw('Capture the withholding GL while Draft.')
    rows=query(doc)
    doc.set('gl_movements',[]);doc.set('map_invoices',[]);doc.set('withholdings',[])
    cache={};settlements=settlement_vouchers(doc)
    for r in rows:
        key=(r.voucher_type,r.voucher_no)
        if key not in cache: cache[key]=frappe.get_doc(*key)
        voucher=cache[key]
        kind=classification(r,voucher,settlements)
        doc.append('gl_movements',dict(**{k:r.get(k) for k in FACTS},classification=kind,
            captured_classification=kind,reason=''))
    doc.source_format=FORMAT
    doc.period_gl_hash=agt_model.digest(facts(rows))
    doc.review_complete=0;doc.gl_completeness_reviewed=0;doc.map_review_complete=0
    propose_map(doc,cache)
    doc.save()
    return dict(movements=len(rows),exceptions=doc.pending_gl_count,amount=doc.tax_amount)


def current(doc, lock=False):
    rows=query(doc)
    if lock:
        for r in rows: frappe.db.sql('select name from `tabGL Entry` where name=%s for update',r.gl_entry)
        rows=query(doc)
    expected=facts(rows)
    if not doc.period_gl_hash or agt_model.digest(expected)!=doc.period_gl_hash or facts(doc.gl_movements or [])!=expected:
        frappe.throw('Withholding GL changed or the captured facts were edited. Capture the month again.')
    cache={};settlements=settlement_vouchers(doc)
    stored_rows={r.gl_entry:r for r in doc.gl_movements or []}
    for r in rows:
        key=(r.voucher_type,r.voucher_no)
        if key not in cache: cache[key]=frappe.get_doc(*key)
        if cache[key].docstatus!=1 or cache[key].company!=doc.company:
            frappe.throw('Monthly withholding source must remain submitted in the same company.')
        kind=classification(r,cache[key],settlements)
        stored=stored_rows[r.gl_entry]
        if doc.docstatus==0 and stored.captured_classification!=kind:
            frappe.throw('Captured GL classification changed. Capture the month again.')
    return amounts(doc.gl_movements,require_review=True)


def approve(doc):
    from angola_tax.industrial_tax.mechanisms import mechanism_context
    profile=mechanism_context(doc)
    if profile.docstatus!=1 or not doc.review_complete:
        frappe.throw('Review the monthly GL determination and approve the profile.')
    if doc.accounting_complete or doc.fiscal_obligation or doc.recognition_journal_entry:
        frappe.throw('Draft monthly GL control cannot supply accounting or obligation links.')
    frappe.db.sql('select name from `tabCompany` where name=%s for update',doc.company)
    data=current(doc,lock=True)
    owned=frappe.db.sql('''select l.gl_entry from `tabIndustrial Tax Withholding GL Line` l
        join `tabIndustrial Tax Withholding Period` p on p.name=l.parent
        where p.company=%s and p.docstatus=1 and p.name!=%s
          and l.classification in ('Recognition','Reversal')''',(doc.company,doc.name),as_dict=True)
    used={r.gl_entry for r in owned}
    if any(r.gl_entry in used for r in doc.gl_movements if r.classification in ('Recognition','Reversal')):
        frappe.throw('A GL recognition already belongs to another approved withholding obligation; do not determine it twice.')
    legacy=frappe.db.sql('''select w.accounting_voucher_type,w.accounting_voucher,c.account
        from `tabIndustrial Tax Withholding Period Line` l
        join `tabIndustrial Tax Withholding Period` p on p.name=l.parent
        join `tabIndustrial Tax Withholding` w on w.name=l.withholding
        join `tabTax Account Configuration` c on c.name=w.account_configuration
        where p.company=%s and p.docstatus=1 and p.name!=%s''',(doc.company,doc.name),as_dict=True)
    used_vouchers={(r.accounting_voucher_type,r.accounting_voucher,r.account) for r in legacy}
    if any((r.voucher_type,r.voucher_no,r.account) in used_vouchers for r in doc.gl_movements
           if r.classification in ('Recognition','Reversal')):
        frappe.throw('This GL voucher was adopted by a legacy monthly control; use controlled revision instead of a second obligation.')
    doc.tax_raw=doc.tax_amount=doc.net_cash_requirement=data['tax']
    doc.approved_by,doc.approved_on=frappe.session.user,now()


def adopt(doc):
    from angola_tax.industrial_tax.mechanisms import mechanism_context, ROLES, make_obligation
    from angola_tax.industrial_tax.configuration import role_account
    current(doc,lock=True)
    profile=mechanism_context(doc)
    account=role_account(profile,ROLES[profile.mechanism][0],doc.fiscal_period_to)
    obligation=make_obligation(doc,account)
    doc.db_set('accounting_complete',1)
    for r in doc.gl_movements:
        if r.classification not in ('Recognition','Reversal'):continue
        obligation.append('movements',dict(posting_date=r.posting_date,voucher_type=r.voucher_type,
            voucher_no=r.voucher_no,account=r.account,debit=r.debit,credit=r.credit,
            net_amount=float(agt_model.number(r.credit)-agt_model.number(r.debit)),
            movement_type='Reclassification',classification_status='Automatic',source_module='Accounting',
            remarks=f'Adopted existing GL entry {r.gl_entry}; no new recognition'))
    obligation.save();obligation.submit()


def journal_invoice_reference(journal):
    return str(journal.get('bill_no') or journal.get('cheque_no') or '').strip()


def supplier_journal(journal, company, supplier):
    if journal.docstatus!=1 or journal.company!=company:
        return False
    return any(r.get('party_type')=='Supplier' and r.get('party')==supplier
        and (agt_model.number(r.get('credit')) or agt_model.number(r.get('debit')))
        for r in journal.get('accounts') or [])


def propose_map(doc, cache):
    """Follow payment references, including legacy supplier invoices held in journals."""
    from angola_tax.industrial_tax.withholding_map import invoice_number
    from angola_tax.industrial_tax.credit_model import quantized
    profile=frappe.get_doc('Industrial Tax Mechanism Profile',doc.profile)
    groups=defaultdict(lambda:Decimal(0))
    for r in doc.gl_movements:
        if r.classification=='Recognition':groups[(r.voucher_type,r.voucher_no)]+=agt_model.number(r.credit)-agt_model.number(r.debit)
    for key,tax in groups.items():
        voucher=cache[key]
        proposals=[]
        if key[0]=='Purchase Invoice':proposals=[('Purchase Invoice',voucher,0,None,None)]
        elif key[0]=='Payment Entry':
            refs=[r for r in voucher.get('references') or [] if r.reference_doctype in ('Purchase Invoice','Journal Entry') and r.allocated_amount]
            for ref in refs:
                invoice=frappe.get_doc(ref.reference_doctype,ref.reference_name)
                proposals.append((ref.reference_doctype,invoice,ref.allocated_amount,voucher.posting_date,ref))
        for typ,invoice,allocated,payment_date,ref in proposals:
            if invoice.docstatus!=1 or invoice.company!=doc.company:continue
            if typ=='Journal Entry':
                if voucher.get('party_type')!='Supplier' or not supplier_journal(invoice,doc.company,voucher.party):continue
                supplier=voucher.party;number=journal_invoice_reference(invoice)
                invoice_date=invoice.get('bill_date');invoice_amount=0;base=Decimal(0)
                description=str(invoice.get('user_remark') or '')
                notes='Journal reference captured. Original invoice date/amount/base and per-invoice withholding require review; opening balance is not the original invoice total.'
            else:
                supplier=invoice.supplier;number=invoice_number(invoice,typ)
                invoice_date=invoice.posting_date;invoice_amount=invoice.base_grand_total
                base=agt_model.number(invoice.get('base_net_total'))
                description='; '.join(dict.fromkeys(str(r.get('description') or r.get('item_name') or '').strip() for r in invoice.get('items') or []))
                notes=''
            single=len(proposals)==1
            domestic=(typ=='Purchase Invoice' and invoice.currency==doc.currency and
                (key[0]!='Payment Entry' or (voucher.get('paid_from_account_currency')==doc.currency and voucher.get('paid_to_account_currency')==doc.currency)))
            assigned_tax=tax if single else Decimal(0)
            if not domestic or not single or abs(quantized(base*agt_model.number(profile.rate)/100,profile.amount_precision)-tax)>Decimal('.01'):base=Decimal(0)
            if not single:notes+=' Multiple references: allocate the posted withholding only when supported; no proportional allocation is assumed.'
            if not domestic:notes+=' Legacy or foreign-currency amounts: review company-currency map values.'
            party=frappe.get_doc('Supplier',supplier)
            nif=str(party.get('tax_id') or '').strip()
            # The allocated payable is preserved separately; it may include tax deductions.
            paid=allocated if single and domestic else 0
            doc.append('map_invoices',dict(source_voucher_type=key[0],source_voucher=key[1],
                source_reference_row=ref.get('name') if ref else None,source_allocated_amount=allocated,
                capture_notes=notes.strip(),invoice_doctype=typ,invoice=invoice.name,invoice_number=number,
                invoice_date=invoice_date,payment_date=payment_date,invoice_amount=invoice_amount,
                provider_id=nif,provider_name=party.get('supplier_name') or party.name,
                nif_angolan='S' if len(nif)==10 and nif.isdigit() else '',oil_sector='',description=description,
                paid_amount=paid,taxable_base=float(base),rate=profile.rate,tax_amount=float(assigned_tax)))


def refresh_map(doc):
    if doc.docstatus not in (0,1) or doc.get('source_format')!=FORMAT:
        frappe.throw('Refresh invoice references on a draft or approved monthly GL control.')
    current(doc)
    cache={(r.voucher_type,r.voucher_no):None for r in doc.gl_movements if r.classification=='Recognition'}
    for key in cache:cache[key]=frappe.get_doc(*key)
    doc.set('map_invoices',[]);doc.map_review_complete=0
    propose_map(doc,cache)
    doc.save()
    return len(doc.map_invoices)


def map_values(doc):
    """Map completeness is independent of approving the ledger tax obligation."""
    from angola_tax.industrial_tax.withholding_map import invoice_number
    from angola_tax.industrial_tax.credit_model import quantized
    if not doc.map_review_complete:frappe.throw('Review the AGT map detail before export. The GL obligation is already determined independently.')
    current(doc)
    groups=defaultdict(lambda:Decimal(0))
    for r in doc.gl_movements:
        if r.classification=='Reversal':frappe.throw('This month contains a reversal; prepare a controlled declaration correction before exporting the map.')
        if r.classification=='Recognition':groups[(r.voucher_type,r.voucher_no)]+=agt_model.number(r.credit)-agt_model.number(r.debit)
    allocated=defaultdict(lambda:Decimal(0));result=[]
    for r in doc.map_invoices or []:
        key=(r.source_voucher_type,r.source_voucher)
        if key not in groups:frappe.throw('Map allocation must refer to a recognition voucher in the captured GL.')
        typ=r.invoice_doctype
        if typ not in ('Purchase Invoice','Journal Entry'):frappe.throw('Map invoice source must be Purchase Invoice or a supplier Journal Entry.')
        voucher=frappe.get_doc(*key);invoice=frappe.get_doc(typ,r.invoice)
        if invoice.docstatus!=1 or invoice.company!=doc.company:
            frappe.throw('Map requires a submitted same-company supplier invoice source.')
        if typ=='Purchase Invoice':
            supplier=invoice.supplier
            if r.invoice_number!=invoice_number(invoice,typ) or not r.invoice_date or getdate(r.invoice_date)!=getdate(invoice.posting_date):
                frappe.throw('Map fiscal invoice reference/date differs from the source.')
        else:
            supplier=voucher.get('party')
            if not supplier or not supplier_journal(invoice,doc.company,supplier):frappe.throw('Legacy invoice journal must belong to the payment supplier.')
            number=journal_invoice_reference(invoice)
            if not r.invoice_number or (number and r.invoice_number!=number):frappe.throw('Review the original invoice reference on the supplier journal.')
            if not r.invoice_date or (invoice.get('bill_date') and getdate(r.invoice_date)!=getdate(invoice.bill_date)):
                frappe.throw('Complete the original invoice date; opening posting date is not invoice evidence.')
        if key[0]=='Purchase Invoice' and r.invoice!=key[1]:frappe.throw('Map invoice must match its GL source.')
        if key[0]=='Payment Entry':
            if voucher.party_type!='Supplier' or voucher.party!=supplier:frappe.throw('Map invoice supplier differs from the payment.')
            references=[x for x in voucher.get('references') or [] if x.reference_doctype==typ and x.reference_name==r.invoice and x.allocated_amount]
            if not references:
                frappe.throw('Map invoice is not referenced by the source payment.')
            if r.get('source_reference_row'):
                references=[x for x in references if x.get('name')==r.source_reference_row]
                if not references or agt_model.number(r.source_allocated_amount)!=agt_model.number(references[0].allocated_amount):
                    frappe.throw('Payment reference allocation changed; refresh the invoice links.')
            if getdate(r.payment_date)!=getdate(voucher.posting_date):frappe.throw('Map payment date differs from the source payment.')
        if not r.payment_date or not getdate(doc.fiscal_period_from)<=getdate(r.payment_date)<=getdate(doc.fiscal_period_to):
            frappe.throw('Map needs the effective payment date within this month; invoice date is not a payment date.')
        if getdate(r.invoice_date)>getdate(r.payment_date):frappe.throw('Original invoice date cannot be later than the effective payment date.')
        if r.nif_angolan not in ('S','N') or r.oil_sector not in ('S','N') or not r.provider_id or not r.provider_name or not r.description:
            frappe.throw('Complete only the missing provider/service information and S/N flags for the AGT map.')
        party=frappe.get_doc('Supplier',supplier)
        if r.nif_angolan=='S' and r.provider_id!=str(party.get('tax_id') or '').strip():frappe.throw('Map NIF differs from the supplier.')
        if typ=='Purchase Invoice' and agt_model.number(r.invoice_amount)!=agt_model.number(invoice.base_grand_total):frappe.throw('Map invoice amount differs from the company-currency source.')
        if typ=='Journal Entry' and agt_model.number(r.invoice_amount)<=0:frappe.throw('Complete the original invoice amount; the legacy outstanding balance is not its face value.')
        profile=frappe.get_doc('Industrial Tax Mechanism Profile',doc.profile)
        base,rate,tax,paid=(agt_model.number(r.get(k)) for k in ('taxable_base','rate','tax_amount','paid_amount'))
        if base>agt_model.number(r.invoice_amount):frappe.throw('Withholding base cannot exceed the reviewed original invoice amount.')
        if base<=0 or tax<=0 or paid<=0 or rate!=agt_model.number(profile.rate) or abs(quantized(base*rate/100,profile.amount_precision)-tax)>Decimal('.01'):
            frappe.throw('Review the map base/rate/payment; tax must reconcile to the posted GL within 0.01.')
        allocated[key]+=tax
        result.append({k:r.get(k) for k in ('nif_angolan','provider_id','provider_name','conformity_reference','oil_sector',
            'invoice_number','description','invoice_date','payment_date','invoice_amount','paid_amount','taxable_base','rate','tax_amount')})
    if allocated!=groups:frappe.throw('AGT invoice detail does not reconcile to every recognition voucher. Complete missing allocations before export.')
    return result


def check_map_update(doc):
    """Allow declaration-detail correction without modifying approved tax/accounting."""
    from datetime import datetime
    old=doc.get_doc_before_save()
    allowed={'map_invoices','map_review_complete','modified','modified_by','_last_update'}
    def value(field,v):
        # Browser JSON and MariaDB represent the same dates, numbers and empty
        # fields differently. Compare typed values, never Document identities.
        if field.fieldtype=='Date':return str(getdate(v)) if v else None
        if field.fieldtype=='Datetime':return datetime.fromisoformat(str(v)).isoformat(timespec='microseconds') if v else None
        if field.fieldtype in ('Currency','Float','Percent','Int','Check'):
            return agt_model.number(int(v) if isinstance(v,bool) else (0 if v is None or v=='' else v))
        if field.fieldtype=='Table':
            child=frappe.get_meta(field.options)
            return [dict(name=r.get('name'),idx=int(r.get('idx') or 0),
                **{f.fieldname:value(f,r.get(f.fieldname)) for f in child.fields
                   if f.fieldtype not in ('Section Break','Column Break','Tab Break','HTML','Button')}) for r in v or []]
        return '' if v is None else v
    for field in doc.meta.fields:
        if field.fieldtype in ('Section Break','Column Break','Tab Break','HTML','Button'):continue
        if field.fieldname not in allowed and value(field,doc.get(field.fieldname))!=value(field,old.get(field.fieldname)):
            frappe.throw(f'Approved monthly GL tax/accounting is immutable: {field.fieldname} changed. Only AGT map detail can be updated.')
    if doc.map_review_complete:map_values(doc)
