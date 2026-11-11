"""Versioned AGT file contract. No framework, posting, or credit creation."""
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
from io import BytesIO
import json
import re
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
RNS='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
TAG=lambda name:'{'+NS+'}'+name
HEADERS=('Exercício','Mês','Número Documento','NIF Entidade Pagadora','Número Factura',
 'Descrição Serviço','Data Factura','Data Pagamento','Valor Factura','Valor Pago',
 'Valor Sujeito a Retenção','Taxa','Imposto Retido','Imposto Pago')
FIELDS=('fiscal_year','month','document_number','payer_tax_id','invoice_number',
 'description','invoice_date','payment_date','invoice_amount','paid_amount',
 'taxable_base','rate','withheld_amount','tax_paid')
MAP_HEADERS=('Nº Ordem','NIF Angolano?','Identificação do Prestador','Nome',
 'Nº Declaração Conformidade','Sector de Prestação de Serviço Petrolífero?',
 'Nº da Factura','Descrição do Serviço','Data da Factura','Data do Pagamento',
 'Valor da Factura','Valor Pago','Valor Sujeito a Retenção','Taxa','Imposto Retido')


def number(value,portuguese=False):
    if value is None or value=='':raise ValueError('Missing numeric value; blank is not zero.')
    if isinstance(value,bool):raise ValueError('Invalid numeric value.')
    text=str(value).strip().replace('\u00a0','')
    if portuguese and isinstance(value,str):
        if not re.fullmatch(r'[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?',text):
            raise ValueError('Invalid Portuguese numeric value.')
        text=text.replace('.','').replace(',','.')
    try:result=Decimal(text)
    except InvalidOperation:raise ValueError('Invalid numeric value.')
    if not result.is_finite():raise ValueError('Numeric value must be finite.')
    return result


def identifier(value):
    if value is None:return ''
    if isinstance(value,(int,float,Decimal)):
        parsed=number(value)
        if parsed!=parsed.to_integral_value():raise ValueError('Identifier must be integral or text.')
        return format(parsed,'f').split('.')[0]
    return str(value).strip()


def parse_date(value):
    if isinstance(value,datetime):return value.date().isoformat()
    if isinstance(value,date):return value.isoformat()
    text=str(value or '').strip()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:-\d{2}\.\d{2}\.\d{2})?',text):
        raise ValueError('Unsupported AGT date format.')
    if len(text)>10:datetime.strptime(text,'%Y-%m-%d-%H.%M.%S')
    return date.fromisoformat(text[:10]).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()


def operation_key(company_tax_id,row):
    # Paid tax is an observation that can change in later extracts.
    values=[format(number(row[k]).normalize(),'f') if k in FIELDS[8:] else row[k]
        for k in FIELDS if k not in ('tax_paid','description')]
    return digest([company_tax_id]+values)


def read_sheets(content):
    if len(content)>10*1024*1024:raise ValueError('AGT XLSX exceeds 10 MiB.')
    try:
        with ZipFile(BytesIO(content)) as archive:
            info=archive.infolist()
            if len(info)>2048 or sum(i.file_size for i in info)>64*1024*1024:
                raise ValueError('AGT workbook is too large when expanded.')
            def xml(path):return ET.fromstring(archive.read(path))
            shared=[]
            if 'xl/sharedStrings.xml' in archive.namelist():
                shared=[''.join(e.itertext()) for e in xml('xl/sharedStrings.xml').findall(TAG('si'))]
            relationships={e.attrib['Id']:e.attrib['Target'] for e in xml('xl/_rels/workbook.xml.rels')}
            sheets=[]
            for sheet in xml('xl/workbook.xml').find(TAG('sheets')):
                target=relationships[sheet.attrib['{'+RNS+'}id']]
                path=target.lstrip('/') if target.startswith('/') else 'xl/'+target
                if '..' in path.split('/'):raise ValueError('Unsafe worksheet path.')
                rows=[]
                for row in xml(path).find(TAG('sheetData')):
                    values={}
                    for cell in row:
                        coordinate=cell.attrib['r'];column=re.match(r'[A-Z]+',coordinate)[0]
                        kind=cell.attrib.get('t');v=cell.find(TAG('v'))
                        if kind=='inlineStr':value=''.join(cell.find(TAG('is')).itertext())
                        elif v is None:value=None
                        elif kind=='s':value=shared[int(v.text)]
                        elif kind in ('str','e'):value=v.text
                        elif kind=='b':value=v.text=='1'
                        else:value=number(v.text)
                        values[column]=value
                    rows.append((int(row.attrib['r']),values))
                sheets.append((sheet.attrib['name'],rows))
            return sheets
    except (KeyError,ET.ParseError,IndexError,TypeError,BadZipFile,NotImplementedError) as exc:
        raise ValueError('Invalid AGT workbook structure.') from exc


def parse_report(content):
    candidates=[]
    for sheet_name,rows in read_sheets(content):
        headers=[(n,r) for n,r in rows if tuple(str(r.get(chr(65+i)) or '').strip() for i in range(14))==HEADERS]
        if headers:candidates.append((sheet_name,rows,headers))
    if len(candidates)!=1 or len(candidates[0][2])!=1:raise ValueError('Expected exactly one AGT retained-values table.')
    sheet_name,rows,headers=candidates[0];header_row=headers[0][0]
    metadata=dict(rows)
    tax_id=str(metadata.get(1,{}).get('A') or '')
    if not tax_id.startswith('Número Fiscal:'):raise ValueError('Missing AGT taxpayer header.')
    tax_id=tax_id.split(':',1)[1].strip()
    if not re.fullmatch(r'\d{10}',tax_id):raise ValueError('Invalid AGT company NIF.')
    company_name=str(metadata.get(2,{}).get('A') or '').split(':',1)[-1].strip()
    result=[]
    for n,values in rows:
        if n<=header_row or not any(v is not None for v in values.values()):continue
        try:
            raw={key:values.get(chr(65+i)) for i,key in enumerate(FIELDS)}
            row={key:identifier(raw[key]) for key in FIELDS[:6]}
            if not re.fullmatch(r'\d{4}',row['fiscal_year']) or not 1<=int(row['month'])<=12:
                raise ValueError('Invalid fiscal year or month.')
            row['month']=str(int(row['month'])).zfill(2)
            if not re.fullmatch(r'\d{10}',row['payer_tax_id']) or not row['document_number'] or not row['invoice_number']:
                raise ValueError('Missing or invalid payer/document/invoice identifier.')
            for key in ('invoice_date','payment_date'):row[key]=parse_date(raw[key])
            for key in FIELDS[8:]:row[key]=number(raw[key],portuguese=True)
            if any(row[k]<0 for k in FIELDS[8:]) or row['rate']>100:
                raise ValueError('Negative amounts or invalid rate require a different reviewed format.')
            row['calculation_difference']=row['withheld_amount']-(row['taxable_base']*row['rate']/100).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            row['unpaid_amount']=row['withheld_amount']-row['tax_paid']
            row['row_key']=operation_key(tax_id,row)
            row['source_row']=n
            row['original_json']=json.dumps(raw,default=str,ensure_ascii=False)
            row['duplicate_count']=0
            result.append(row)
        except (ValueError,TypeError) as exc:raise ValueError(f'AGT row {n}: {exc}') from exc
    if not result:raise ValueError('AGT report contains no detail rows.')
    counts=Counter(r['row_key'] for r in result)
    for r in result:r['duplicate_count']=counts[r['row_key']]-1
    return dict(company_tax_id=tax_id,company_name=company_name,sheet_name=sheet_name,
        format_version='AGT Retained Values v1',rows=result,file_hash=hashlib.sha256(content).hexdigest(),
        fiscal_years=', '.join(sorted({r['fiscal_year'] for r in result})),
        retained_total=sum((r['withheld_amount'] for r in result),Decimal(0)),
        paid_total=sum((r['tax_paid'] for r in result),Decimal(0)))


def allocation_totals(rows,allocations,previous=None):
    """Cap cumulative allocations by paid portal evidence, not retained expectation."""
    source={r['row_key']:r for r in rows};used=dict(previous or {});totals={}
    if len(source)!=len(rows):raise ValueError('Ambiguous duplicate AGT operations require review; do not allocate them.')
    for a in allocations:
        key=a['portal_row_key'];row=source.get(key)
        if not row:raise ValueError('Allocation references a missing AGT operation.')
        amount=number(a.get('amount'))
        if amount<=0:raise ValueError('Allocation must be positive.')
        if row['tax_paid']>row['withheld_amount']:raise ValueError('Portal paid tax exceeds retained tax; review this operation.')
        used[key]=number(used.get(key,0))+amount
        if used[key]>row['tax_paid']:raise ValueError('Allocations exceed confirmed paid portal tax.')
        origin=a['source_withholding'];totals[origin]=totals.get(origin,Decimal(0))+amount
    return totals
