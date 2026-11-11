"""Invoice allocations and monthly AGT map exported in the supplied template."""
from datetime import date
from copy import deepcopy
import re
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import frappe
from frappe.utils import getdate, now
from angola_tax.industrial_tax import agt_model
from angola_tax.industrial_tax.credit_model import quantized


def invoice_number(invoice,doctype):
    return str((invoice.get('bill_no') if doctype=='Purchase Invoice' else invoice.get('doc_agt')) or invoice.name).strip()


def validate_lines(doc):
    lines=doc.get('invoice_lines') or []
    if not lines:return
    base=tax=Decimal(0)
    expected='Sales Invoice' if doc.mechanism=='Suffered Withholding' else 'Purchase Invoice'
    references=None
    if doc.event_voucher_type=='Payment Entry':
        event=frappe.get_doc('Payment Entry',doc.event_voucher)
        references={r.reference_name for r in event.get('references') or [] if r.reference_doctype==expected and r.allocated_amount}
    for row in lines:
        if row.invoice_doctype!=expected:frappe.throw('Invoice allocation side must match the withholding mechanism.')
        if references is not None and row.invoice not in references:
            frappe.throw('Invoice allocation is not linked to the selected payment event.')
        invoice=frappe.get_doc(expected,row.invoice)
        party=invoice.customer if expected=='Sales Invoice' else invoice.supplier
        if invoice.docstatus!=1 or invoice.company!=doc.company or party!=doc.party:
            frappe.throw('Allocate a submitted same-company invoice for the withholding counterparty.')
        if getdate(row.invoice_date)!=getdate(invoice.posting_date) or row.invoice_number!=invoice_number(invoice,expected):
            frappe.throw('Invoice fiscal reference/date changed; refresh the invoice allocations.')
        if getdate(row.payment_date)!=getdate(doc.event_date):frappe.throw('Invoice allocation payment date must match the withholding event.')
        if row.nif_angolan not in ('S','N') or row.oil_sector not in ('S','N'):
            frappe.throw('Review NIF Angolano and petroleum-service flags for each operation.')
        if not row.provider_id or not row.provider_name or not row.description:
            frappe.throw('Invoice allocations require provider identification, name and service description.')
        if row.nif_angolan=='S':
            party_nif=str(frappe.db.get_value(doc.party_type,doc.party,'tax_id') or '').strip()
            if not party_nif or row.provider_id!=party_nif:
                frappe.throw('Allocation NIF differs from the counterparty tax identifier.')
        if agt_model.number(row.invoice_amount)!=agt_model.number(invoice.base_grand_total):
            frappe.throw('Map invoice amount differs from the submitted invoice company-currency total.')
        values={k:agt_model.number(row.get(k)) for k in ('invoice_amount','paid_amount','taxable_base','rate','tax_amount')}
        if any(v<0 for v in values.values()) or not values['taxable_base']:
            frappe.throw('Invoice allocation amounts must be nonnegative with a positive taxable base.')
        profile=frappe.get_doc('Industrial Tax Mechanism Profile',doc.profile)
        if values['rate']!=agt_model.number(profile.rate):frappe.throw('Invoice allocation rate differs from the approved mechanism.')
        difference=values['tax_amount']-quantized(values['taxable_base']*values['rate']/100,profile.amount_precision)
        if abs(difference)>Decimal('.01'):
            frappe.throw('Invoice allocation tax difference exceeds 0.01 against the approved rate/precision.')
        if difference and (not doc.rounding_review_complete or not doc.rounding_reason):
            frappe.throw('Review and explain the adopted invoice-level GL rounding difference.')
        base+=values['taxable_base'];tax+=values['tax_amount']
    if base!=agt_model.number(doc.taxable_base) or tax!=agt_model.number(doc.tax_amount):
        frappe.throw('Invoice allocations must exactly reconcile the withholding base and tax.')


def capture_invoice(doc):
    """Only a single invoice, domestic payment and fully known facts are proposed."""
    if doc.docstatus!=0:frappe.throw('Capture invoice allocations while Draft.')
    doc.validate()
    event=frappe.get_doc(doc.event_voucher_type,doc.event_voucher)
    typ='Sales Invoice' if doc.mechanism=='Suffered Withholding' else 'Purchase Invoice'
    references=[r for r in event.get('references') or [] if r.reference_doctype==typ and r.allocated_amount]
    if len(references)!=1 or doc.event_voucher_type!='Payment Entry':
        frappe.throw('Automatic proposal requires one invoice on a Payment Entry; enter reviewed allocations for split or journal payments.')
    invoice=frappe.get_doc(typ,references[0].reference_name)
    if invoice.currency!=doc.currency or event.paid_from_account_currency!=doc.currency or event.paid_to_account_currency!=doc.currency:
        frappe.throw('Foreign-currency invoice/payment allocations require explicit reviewed company-currency amounts.')
    party=frappe.get_doc(doc.party_type,doc.party)
    doc.set('invoice_lines',[])
    doc.append('invoice_lines',dict(invoice_doctype=typ,invoice=invoice.name,
        invoice_number=invoice_number(invoice,typ),invoice_date=invoice.posting_date,payment_date=doc.event_date,
        provider_id=party.tax_id,provider_name=party.get('customer_name') or party.get('supplier_name'),
        nif_angolan='S',oil_sector='',description='',conformity_reference='',
        invoice_amount=invoice.base_grand_total,
        paid_amount=references[0].allocated_amount,
        taxable_base=doc.taxable_base,rate=frappe.get_doc('Industrial Tax Mechanism Profile',doc.profile).rate,
        tax_amount=doc.tax_amount))
    # Service description and petroleum scope remain explicit review fields.
    doc.review_complete=0;doc.save();return 1


def period_lines(doc):
    result=[]
    for r in doc.withholdings or []:
        origin=frappe.get_doc('Industrial Tax Withholding',r.withholding)
        if origin.docstatus!=1 or origin.company!=doc.company or origin.tax_type!=doc.tax_type:
            frappe.throw('Monthly map contains an invalid withholding register.')
        if not origin.get('invoice_lines'):
            frappe.throw(f'Withholding {origin.name} lacks reviewed invoice allocations for the AGT map.')
        validate_lines(origin)
        for row in origin.invoice_lines:
            if not getdate(doc.fiscal_period_from)<=getdate(row.payment_date)<=getdate(doc.fiscal_period_to):
                frappe.throw('Map payment falls outside the approved withholding month.')
            result.append(dict(withholding=origin.name,**{k:row.get(k) for k in (
                'nif_angolan','provider_id','provider_name','conformity_reference','oil_sector',
                'invoice_number','description','invoice_date','payment_date','invoice_amount',
                'paid_amount','taxable_base','rate','tax_amount')}))
    return result


def map_values(doc):
    if doc.get('source_format') == 'GL Withholding v1':
        from angola_tax.industrial_tax.withholding_gl import map_values as gl_map_values
        return gl_map_values(doc)
    rows=period_lines(doc)
    if sum((agt_model.number(r['tax_amount']) for r in rows),Decimal(0))!=agt_model.number(doc.tax_amount):
        frappe.throw('AGT map detail does not reconcile to the monthly determined tax.')
    return rows


def xml_with_namespaces(root,original):
    namespaces={prefix:uri for _,(prefix,uri) in ET.iterparse(BytesIO(original),events=['start-ns'])}
    used={str(e.tag).split('}')[0][1:] for e in root.iter() if str(e.tag).startswith('{')}
    used.update(k.split('}')[0][1:] for e in root.iter() for k in e.attrib if k.startswith('{'))
    for prefix,uri in namespaces.items():
        ET.register_namespace(prefix,uri)
        if prefix and uri not in used:root.set('xmlns:'+prefix,uri)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def build_xlsx(company_tax_id,period,rows):
    """Update only data cells in the original OpenXML template, preserving all parts."""
    content=(Path(__file__).parent/'templates/ii_withholding.xlsx').read_bytes()
    with ZipFile(BytesIO(content)) as source:
        root=ET.fromstring(source.read('xl/worksheets/sheet1.xml'))
        ns=agt_model.NS;tag=agt_model.TAG;data=root.find(tag('sheetData'))
        for row in list(data):data.remove(row)
        def put_row(n,values):
            row=ET.SubElement(data,tag('row'),{'r':str(n)})
            for i,value in enumerate(values):
                c=ET.SubElement(row,tag('c'),{'r':chr(65+i)+str(n)})
                if isinstance(value,(int,float,Decimal)):
                    ET.SubElement(c,tag('v')).text=str(value)
                elif isinstance(value,date):
                    c.set('t','inlineStr');ET.SubElement(ET.SubElement(c,tag('is')),tag('t')).text=value.isoformat()
                else:
                    c.set('t','inlineStr');ET.SubElement(ET.SubElement(c,tag('is')),tag('t')).text=str(value or '')
        put_row(1,[company_tax_id,'NIF do Contribuinte']);put_row(2,[period,'Período (AAAA-MM)'])
        put_row(3,agt_model.MAP_HEADERS)
        for i,r in enumerate(rows,1):
            put_row(i+3,[i,r['nif_angolan'],r['provider_id'],r['provider_name'],r['conformity_reference'] or '',
                r['oil_sector'],r['invoice_number'],r['description'],str(r['invoice_date']),str(r['payment_date']),
                agt_model.number(r['invoice_amount']),agt_model.number(r['paid_amount']),agt_model.number(r['taxable_base']),
                agt_model.number(r['rate'])/100,agt_model.number(r['tax_amount'])])
        # Use template's existing percentage/date styles; values are numeric Excel dates.
        original=ET.fromstring(source.read('xl/worksheets/sheet1.xml'))
        original_styles={c.attrib['r']:c.attrib.get('s') for c in original.findall('.//'+tag('c'))}
        styles={re.match(r'[A-Z]+',c.attrib['r'])[0]:c.attrib.get('s') for oldrow in original.findall('.//'+tag('row')) if oldrow.attrib['r']=='4' for c in oldrow}
        style_root=ET.fromstring(source.read('xl/styles.xml'))
        xfs=style_root.find(tag('cellXfs'))
        for col in ('K','L','M','N','O'):
            style=deepcopy(xfs[int(styles[col])])
            style.set('numFmtId','10' if col=='N' else '4');style.set('applyNumberFormat','1')
            styles[col]=str(len(xfs));xfs.append(style)
        xfs.set('count',str(len(xfs)))
        for row in data:
            for c in row:
                if int(row.attrib['r'])<4:
                    if original_styles.get(c.attrib['r']):c.set('s',original_styles[c.attrib['r']])
                    continue
                col=re.match(r'[A-Z]+',c.attrib['r'])[0]
                if styles.get(col):c.set('s',styles[col])
                if col in ('I','J'):
                    value=''.join(c.itertext());dt=date.fromisoformat(value)
                    for e in list(c):c.remove(e)
                    c.attrib.pop('t',None)
                    ET.SubElement(c,tag('v')).text=str((dt-date(1899,12,30)).days)
        dim=root.find(tag('dimension'))
        if dim is not None:dim.set('ref',f'A1:O{len(rows)+3}')
        modified={'xl/worksheets/sheet1.xml':xml_with_namespaces(root,source.read('xl/worksheets/sheet1.xml')),
                  'xl/styles.xml':xml_with_namespaces(style_root,source.read('xl/styles.xml'))}
        # Original sample formulas were replaced by authoritative numeric values.
        if 'xl/calcChain.xml' in source.namelist():
            for part in ('[Content_Types].xml','xl/_rels/workbook.xml.rels'):
                tree=ET.fromstring(source.read(part))
                for item in list(tree):
                    if 'calcChain' in item.attrib.get('PartName','') or 'calcChain' in item.attrib.get('Type',''):
                        tree.remove(item)
                modified[part]=ET.tostring(tree,encoding='utf-8',xml_declaration=True)
        output=BytesIO()
        with ZipFile(output,'w') as target:
            for entry in source.infolist():
                if entry.filename=='xl/calcChain.xml':continue
                target.writestr(entry,modified.get(entry.filename,source.read(entry.filename)))
        return output.getvalue()


def export_map(doc):
    doc.check_permission('read')
    if doc.docstatus!=1 or not doc.accounting_complete:
        frappe.throw('Submit the reconciled monthly withholding determination before exporting the map.')
    from angola_tax.industrial_tax.agt_import import company_nif
    rows=map_values(doc);payload=build_xlsx(company_nif(doc.company),str(doc.fiscal_period_from)[:7],rows)
    file=frappe.get_doc(dict(doctype='File',file_name=f'II_Retencoes_{doc.name}.xlsx',is_private=1,
        content=payload,attached_to_doctype=doc.doctype,attached_to_name=doc.name))
    file.insert()
    return file.file_url


def gl_review(doc):
    """Review all new liability credits, including those not registered as paid events."""
    from angola_tax.industrial_tax.mechanisms import mechanism_context, ROLES
    from angola_tax.industrial_tax.configuration import role_account
    profile=mechanism_context(doc)
    account=role_account(profile,ROLES[profile.mechanism][0],doc.fiscal_period_to)
    movements=frappe.get_all('GL Entry',filters=dict(company=doc.company,account=account,is_cancelled=0,
        posting_date=['between',[doc.fiscal_period_from,doc.fiscal_period_to]]),
        fields=['name','posting_date','voucher_type','voucher_no','debit','credit','is_opening'],order_by='name',limit_page_length=0)
    movements=[r for r in movements if r.get('is_opening')!='Yes' and r.voucher_type!='Period Closing Voucher']
    registered=frappe.db.sql('''select w.name,w.accounting_voucher_type,w.accounting_voucher,w.event_date,w.tax_amount
        from `tabIndustrial Tax Withholding` w join `tabTax Account Configuration` c on c.name=w.account_configuration
        where w.company=%s and c.account=%s and w.docstatus=1 order by w.name''',(doc.company,account),as_dict=True)
    used={}
    for row in registered:
        key=(row.accounting_voucher_type,row.accounting_voucher)
        used[key]=used.get(key,Decimal(0))+agt_model.number(row.tax_amount)
    credits={}
    for row in movements:
        key=(row.voucher_type,row.voucher_no)
        credits[key]=credits.get(key,Decimal(0))+agt_model.number(row.credit)-agt_model.number(row.debit)
    gross=sum((max(v,Decimal(0)) for v in credits.values()),Decimal(0))
    unmatched=sum((max(v-used.get(k,Decimal(0)),Decimal(0)) for k,v in credits.items()),Decimal(0))
    return dict(hash=agt_model.digest([account,[dict(r) for r in movements],[dict(r) for r in registered]]),
        liability_credit_total=gross,unregistered_credit=unmatched)


def capture_gl_review(doc):
    data=gl_review(doc)
    doc.period_gl_hash=data['hash'];doc.gl_liability_credit_total=float(data['liability_credit_total'])
    doc.unregistered_liability_credit=float(data['unregistered_credit'])
    doc.gl_completeness_reviewed=0;doc.gl_difference_reason=''


def require_gl_review(doc):
    data=gl_review(doc)
    if not doc.period_gl_hash or data['hash']!=doc.period_gl_hash:
        frappe.throw('Monthly liability GL or registered withholdings changed; capture the month again.')
    if not doc.gl_completeness_reviewed:
        frappe.throw('Review monthly withholding GL completeness before submission.')
    if data['unregistered_credit'] and not doc.gl_difference_reason:
        frappe.throw('Explain liability credits without registered withholding payment events; attach review evidence.')
