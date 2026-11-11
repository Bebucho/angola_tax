"""Read-only site inspection for the next automatic payment ingestion adapter."""
import json
import frappe


def run(company='HAMAD ENERGY'):
    types=['Payment Entry','Payment Entry Reference','Payment Entry Deduction','Purchase Invoice','Sales Invoice']
    metadata={}
    for typ in types:
        metadata[typ]=[dict(fieldname=f.fieldname,fieldtype=f.fieldtype,options=f.options)
            for f in frappe.get_meta(typ).fields if f.fieldtype not in ('Section Break','Column Break') and
            any(key in f.fieldname.lower() for key in ('retenc','reten','withhold','tax','deduct','allocat','paid','receiv','currency','exchange','reference','bill_no','doc_agt'))]
    accounts=[r.name for r in frappe.get_all('Account',filters={'company':company},fields=['name','account_number'],limit_page_length=0)
        if str(r.account_number or '').strip().startswith(('3413','3414','3415'))]
    gl=frappe.get_all('GL Entry',filters=dict(company=company,account=['in',accounts],is_cancelled=0),
        fields=['name','posting_date','account','voucher_type','voucher_no','debit','credit','party_type','party'],
        order_by='posting_date desc,name',limit_page_length=20) if accounts else []
    vouchers=[];seen=set()
    for row in gl:
        key=(row.voucher_type,row.voucher_no)
        if key in seen or row.voucher_type not in ('Payment Entry','Journal Entry','Purchase Invoice','Sales Invoice'):continue
        seen.add(key);doc=frappe.get_doc(*key)
        fields=['name','company','docstatus','posting_date','party_type','party','supplier','customer','currency','paid_from_account_currency','paid_to_account_currency',
            'paid_amount','received_amount','base_paid_amount','base_received_amount','source_exchange_rate','target_exchange_rate',
            'grand_total','base_grand_total','net_total','base_net_total','bill_no','doc_agt']
        detail={f:doc.get(f) for f in fields if doc.get(f) is not None};detail['doctype']=doc.doctype
        for table in ('references','deductions','taxes'):
            detail[table]=[{k:r.get(k) for k in ('reference_doctype','reference_name','allocated_amount','account','account_head','tax_amount','base_tax_amount','cost_center','add_deduct_tax') if r.get(k) is not None} for r in doc.get(table) or []]
        vouchers.append(detail)
        if len(vouchers)>=10:break
    result=dict(company=company,metadata=metadata,accounts=accounts,gl=gl,vouchers=vouchers)
    print(json.dumps(result,default=str,indent=2));return result
