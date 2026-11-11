frappe.query_reports["Tax Fiscal Position"]={
  "filters": [
    {
      "fieldname": "company",
      "label": "Empresa",
      "fieldtype": "Link",
      "reqd": 1,
      "options": "Company"
    },
    {
      "fieldname": "from_date",
      "label": "Desde",
      "fieldtype": "Date"
    },
    {
      "fieldname": "to_date",
      "label": "Até",
      "fieldtype": "Date"
    },
    {
      "fieldname": "tax_type",
      "label": "Imposto",
      "fieldtype": "Link",
      "options": "Tax Type"
    },
    {
      "fieldname": "status",
      "label": "Estado",
      "fieldtype": "Select",
      "options": "\nDraft\nDetermined\nUnder Review\nApproved\nFiled\nAwaiting Assessment\nAwaiting Liquidation\nLiquidated\nReady for Payment\nUnder Payment Plan\nPaid\nReconciled\nClosed\nSettled by Credits"
    },
    {
      "fieldname": "outstanding_only",
      "label": "Só Com Saldo Pendente",
      "fieldtype": "Check",
      "default": 0
    },
    {
      "fieldname": "include_drafts",
      "label": "Incluir Rascunhos",
      "fieldtype": "Check",
      "default": 0
    },
    {
      "fieldname": "include_closed",
      "label": "Incluir Encerradas",
      "fieldtype": "Check",
      "default": 0
    },
    {
      "fieldname": "responsible_user",
      "label": "Responsável",
      "fieldtype": "Link",
      "options": "User"
    }
  ]
};
frappe.query_reports["Tax Fiscal Position"].formatter=function(value,row,column,data,default_formatter){
    if(column.fieldtype==='Currency' && data && !data.currency){
        return value==null ? '' : frappe.format(value,{fieldtype:'Float',precision:2});
    }
    return default_formatter(value,row,column,data);
};
frappe.query_reports["Tax Fiscal Position"].onload=function(report){
    if(!report.get_filter_value('company')){
        const company=frappe.defaults.get_user_default('Company');
        if(company)report.set_filter_value('company',company);
    }
};
