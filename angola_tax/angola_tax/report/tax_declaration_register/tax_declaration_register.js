frappe.query_reports["Tax Declaration Register"]={
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
      "label": "Fim do Período — Desde",
      "fieldtype": "Date"
    },
    {
      "fieldname": "to_date",
      "label": "Fim do Período — Até",
      "fieldtype": "Date"
    },
    {
      "fieldname": "declaration_type",
      "label": "Tipo de Declaração",
      "fieldtype": "Link",
      "options": "Tax Declaration Type"
    },
    {
      "fieldname": "status",
      "label": "Estado da Entrega",
      "fieldtype": "Select",
      "options": "\nNot Submitted\nSubmitted\nAcknowledged\nAccepted\nRejected"
    },
    {
      "fieldname": "all_versions",
      "label": "Todas as Versões",
      "fieldtype": "Check",
      "default": 0
    },
    {
      "fieldname": "include_missing",
      "label": "Incluir Obrigações Sem Registo",
      "fieldtype": "Check",
      "default": 1
    },
    {
      "fieldname": "responsible_user",
      "label": "Responsável",
      "fieldtype": "Link",
      "options": "User"
    }
  ]
};
frappe.query_reports["Tax Declaration Register"].formatter=function(value,row,column,data,default_formatter){
    if(column.fieldtype==='Currency' && data && !data.currency){
        return value==null ? '' : frappe.format(value,{fieldtype:'Float',precision:2});
    }
    return default_formatter(value,row,column,data);
};
frappe.query_reports["Tax Declaration Register"].onload=function(report){
    if(!report.get_filter_value('company')){
        const company=frappe.defaults.get_user_default('Company');
        if(company)report.set_filter_value('company',company);
    }
};
