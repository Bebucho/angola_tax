"""AGT observations and reviewed allocations, without automatic accounting."""
from collections import defaultdict
from decimal import Decimal
import frappe
from frappe.model.document import Document
from frappe.utils import getdate, now
from angola_tax.industrial_tax import agt_model
from angola_tax.industrial_tax.configuration import get_company_currency


def report_file(doc):
    file=frappe.get_doc('File',{'file_url':doc.report_file})
    file.check_permission('read')
    content=file.get_content()
    if not isinstance(content,bytes):frappe.throw('Upload an XLSX report downloaded from the AGT portal.')
    return content


def parse(doc):
    try:return agt_model.parse_report(report_file(doc))
    except ValueError as exc:frappe.throw(str(exc))


def company_nif(company):
    value=str(frappe.db.get_value('Company',company,'tax_id') or '').strip()
    if not value:frappe.throw('Set the company Tax ID before importing AGT evidence.')
    return value


def row_values(rows):
    fields=agt_model.FIELDS+('row_key','source_row','calculation_difference','unpaid_amount','duplicate_count','original_json')
    numeric=set(agt_model.FIELDS[8:])|{'source_row','calculation_difference','unpaid_amount','duplicate_count'}
    return sorted([tuple(str(r.get(k) or '') if k not in numeric else agt_model.number(r.get(k,0)) for k in fields) for r in rows],key=lambda r:(r[0],r[1],r[2],r[-5]))


def current(doc):
    data=parse(doc)
    if doc.currency not in ('AOA','KZ') or get_company_currency(doc.company)!=doc.currency:
        frappe.throw('Company currency changed; use controlled correction of AGT evidence.')
    if any(str(doc.get(key) or '')!=str(data[key]) for key in ('company_tax_id','company_name','format_version','fiscal_years')):
        frappe.throw('Captured AGT metadata changed; capture the Draft again.')
    if data['file_hash']!=doc.file_hash or data['company_tax_id']!=company_nif(doc.company):
        frappe.throw('AGT file or taxpayer changed; capture the Draft again.')
    if row_values(data['rows'])!=row_values(doc.report_rows or []):
        frappe.throw('Captured AGT detail changed; capture the Draft again.')
    return data


def invoice_references(origin):
    refs={str(r.invoice_number).strip() for r in origin.get('invoice_lines') or [] if r.invoice_number}
    if refs:return refs
    # Legacy submitted registers retain their original accounting source.
    event=frappe.get_doc(origin.event_voucher_type,origin.event_voucher)
    for row in event.get('references') or []:
        if row.reference_doctype!='Sales Invoice':continue
        invoice=frappe.get_doc('Sales Invoice',row.reference_name)
        refs.add(str(invoice.get('doc_agt') or invoice.name).strip())
    if origin.accounting_voucher_type=='Sales Invoice':
        invoice=frappe.get_doc('Sales Invoice',origin.accounting_voucher)
        refs.add(str(invoice.get('doc_agt') or invoice.name).strip())
    return refs


def lock_taxpayer(company):
    nif=company_nif(company)
    # Companies representing branches of one legal taxpayer share portal evidence.
    frappe.db.sql('select name from `tabCompany` where trim(tax_id)=%s order by name for update',nif)


def validate_allocations(doc,data,lock=False):
    if lock:
        lock_taxpayer(doc.company)
    previous=defaultdict(Decimal)
    for r in frappe.db.sql('''select a.portal_row_key,a.amount from `tabIndustrial Tax AGT Allocation` a
        join `tabIndustrial Tax AGT Import` i on i.name=a.parent
        where i.company_tax_id=%s and i.docstatus=1 and i.name!=%s''',(doc.get('company_tax_id') or company_nif(doc.company),doc.name),as_dict=True):
        previous[r.portal_row_key]+=agt_model.number(r.amount)
    allocations=[dict(portal_row_key=a.portal_row_key,source_withholding=a.source_withholding,amount=a.amount) for a in doc.allocations or []]
    try:totals=agt_model.allocation_totals(data['rows'],allocations,previous)
    except ValueError as exc:frappe.throw(str(exc))
    rows={r['row_key']:r for r in data['rows']}
    for a in doc.allocations or []:
        row=rows[a.portal_row_key]
        origin=frappe.get_doc('Industrial Tax Withholding',a.source_withholding)
        if (origin.docstatus!=1 or origin.mechanism!='Suffered Withholding' or origin.company!=doc.company
                or str(origin.fiscal_year)!=row['fiscal_year']):
            frappe.throw('AGT allocations require approved suffered withholding for the same company and exercise.')
        if str(frappe.db.get_value('Customer',origin.party,'tax_id') or '').strip()!=row['payer_tax_id']:
            frappe.throw('AGT payer NIF does not match the customer withholding.')
        if row['invoice_number'] not in invoice_references(origin):
            frappe.throw('AGT invoice does not match the reviewed withholding invoice/payment references.')
        if getdate(origin.event_date)!=getdate(row['payment_date']):
            frappe.throw('AGT payment date differs from the withholding event; reconcile before allocation.')
        origin.validate_vouchers()
        from angola_tax.industrial_tax.computation import fingerprint
        if fingerprint(origin.source_query())!=origin.source_hash:
            frappe.throw('Withholding GL evidence changed; controlled correction is required.')
        if abs(row['calculation_difference'])>Decimal('.01'):
            frappe.throw('AGT rate/base calculation difference exceeds 0.01; review the report before allocation.')
        profile=frappe.get_doc('Industrial Tax Mechanism Profile',origin.profile)
        if agt_model.number(profile.rate)!=row['rate']:
            frappe.throw('AGT tax rate differs from the approved withholding mechanism.')
        if not a.eligibility_date or not a.eligibility_reason:
            frappe.throw('Review the allocation eligibility date and its basis; the report has no tax remittance date.')
        source_dates=[getdate(origin.event_date)]+[getdate(r.posting_date) for r in origin.source_query()]
        if getdate(a.eligibility_date)<max(source_dates):
            frappe.throw('Eligibility cannot precede withholding and fiscal asset recognition.')
    for name,total in totals.items():
        origin=frappe.get_doc('Industrial Tax Withholding',name)
        previous_origin=frappe.db.sql('''select coalesce(sum(a.amount),0) from `tabIndustrial Tax AGT Allocation` a
          join `tabIndustrial Tax AGT Import` i on i.name=a.parent
          where i.docstatus=1 and a.source_withholding=%s and i.name!=%s''',(name,doc.name))[0][0]
        if total+agt_model.number(previous_origin or 0)>agt_model.number(origin.tax_amount):
            frappe.throw('Cumulative AGT allocations exceed the registered withholding amount.')
    return totals


class IndustrialTaxAGTImport(Document):
    def validate(self):
        self.currency=get_company_currency(self.company)
        if self.currency not in ('AOA','KZ'):
            frappe.throw('This AGT format requires reviewed AOA/KZ company currency.')
        if self.file_hash:
            self.import_key=agt_model.digest([self.company,self.file_hash])
        self.retained_total=float(sum((agt_model.number(r.withheld_amount) for r in self.report_rows or []),Decimal(0)))
        self.paid_total=float(sum((agt_model.number(r.tax_paid) for r in self.report_rows or []),Decimal(0)))
        self.allocated_total=float(sum((agt_model.number(r.amount) for r in self.allocations or []),Decimal(0)))
        self.row_count=len(self.report_rows or [])
        self.unallocated_paid_total=self.paid_total-self.allocated_total
        self.unpaid_total=self.retained_total-self.paid_total

    @frappe.whitelist()
    def capture_report(self):
        self.check_permission('write')
        if self.docstatus!=0:frappe.throw('Capture AGT evidence while Draft.')
        data=parse(self)
        if data['company_tax_id']!=company_nif(self.company):
            frappe.throw('The AGT report taxpayer NIF differs from the selected company.')
        self.set('report_rows',[]);self.set('allocations',[])
        for row in data['rows']:
            self.append('report_rows',{k:float(v) if isinstance(v,Decimal) else v for k,v in row.items()})
        for key in ('file_hash','company_tax_id','company_name','format_version','fiscal_years'):
            setattr(self,key,data[key])
        self.captured_on=now();self.review_complete=0;self.review_evidence=self.report_file
        self.save();return self.row_count

    @frappe.whitelist()
    def suggest_matches(self):
        self.check_permission('write')
        if self.docstatus!=0:frappe.throw('Suggest reconciliations while Draft.')
        data=current(self);self.set('allocations',[])
        matches=0
        candidates_by_key=defaultdict(list)
        tax_ids={}
        for item in frappe.get_all('Industrial Tax Withholding',filters=dict(company=self.company,
                mechanism='Suffered Withholding',docstatus=1,
                fiscal_year=['in',sorted({r['fiscal_year'] for r in data['rows']})],
                event_date=['between',[min(r['payment_date'] for r in data['rows']),max(r['payment_date'] for r in data['rows'])]]),fields=['name','party'],limit_page_length=0):
            if item.party not in tax_ids:
                tax_ids[item.party]=str(frappe.db.get_value('Customer',item.party,'tax_id') or '').strip()
            origin=frappe.get_doc('Industrial Tax Withholding',item.name)
            for ref in invoice_references(origin):
                candidates_by_key[(str(origin.fiscal_year),str(origin.event_date),tax_ids[item.party],ref)].append(origin)
        for row in data['rows']:
            if row['duplicate_count'] or row['tax_paid']<=0:continue
            candidates=candidates_by_key.get((row['fiscal_year'],row['payment_date'],row['payer_tax_id'],row['invoice_number']),[])
            if len(candidates)!=1:continue
            origin=candidates[0]
            self.append('allocations',dict(portal_row_key=row['row_key'],source_withholding=origin.name,
                amount=float(min(row['tax_paid'],agt_model.number(origin.tax_amount)))))
            matches+=1
        self.review_complete=0;self.save();return matches

    def before_submit(self):
        data=current(self)
        if not self.review_complete or not self.review_evidence:
            frappe.throw('Review AGT observations and allocations; attach the review evidence.')
        if not self.extracted_on or getdate(self.extracted_on)<max(getdate(r['payment_date']) for r in data['rows']):
            frappe.throw('Review the extraction date; it cannot precede reported customer payments.')
        validate_allocations(self,data,lock=True)
        self.approved_by,self.approved_on=frappe.session.user,now()

    @frappe.whitelist()
    def create_credits(self):
        self.check_permission('read')
        if self.docstatus!=1:frappe.throw('Approve the AGT reconciliation first.')
        data=current(self);validate_allocations(self,data,lock=True)
        created=[]
        for source in sorted({a.source_withholding for a in self.allocations or []}):
            existing=frappe.db.get_value('Industrial Tax Credit',dict(source_withholding=source,
                agt_import=self.name,docstatus=['!=',2]),'name')
            if existing:created.append(existing);continue
            credit=frappe.get_doc(dict(doctype='Industrial Tax Credit',company=self.company,
                credit_kind='Suffered Withholding',source_withholding=source,agt_import=self.name,eligibility_evidence=self.report_file))
            credit.insert();created.append(credit.name)
        return created

    def before_cancel(self):
        if frappe.db.exists('Industrial Tax Credit',dict(agt_import=self.name,docstatus=['!=',2])):
            frappe.throw('AGT reconciliation is referenced by a credit; use controlled correction.')
    def before_update_after_submit(self):frappe.throw('Approved AGT evidence and allocations are immutable.')


def credit_evidence(credit,origin):
    if not credit.agt_import:frappe.throw('Select an approved AGT report reconciliation; no separate certificate is required.')
    cache=frappe.flags.setdefault('ii_agt_credit_evidence',{})
    key=(credit.company,credit.agt_import)
    if key not in cache:
        doc=frappe.get_doc('Industrial Tax AGT Import',credit.agt_import)
        if doc.docstatus!=1 or doc.company!=credit.company:frappe.throw('Credit requires approved same-company AGT reconciliation.')
        data=current(doc)
        validate_allocations(doc,data,lock=True)
        by_origin=defaultdict(list)
        for allocation in doc.allocations or []:by_origin[allocation.source_withholding].append(allocation)
        cache[key]=by_origin
    rows=cache[key].get(origin.name,[])
    if not rows:frappe.throw('AGT reconciliation has no allocation for this withholding.')
    # Request-local cache avoids re-reading a whole report for each generated credit.
    # The credit controller separately revalidates its own withholding GL evidence.
    amount=sum((agt_model.number(a.amount) for a in rows),Decimal(0))
    return amount,max(getdate(a.eligibility_date) for a in rows)
