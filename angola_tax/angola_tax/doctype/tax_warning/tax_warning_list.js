frappe.listview_settings['Tax Warning']={
    hide_name_column:true,
    filters:[['status','!=','Resolved']],
    add_fields:['severity','status','kind','company','fiscal_process','responsible_user'],
    get_indicator(doc){return [__(doc.status),({Critical:'red',High:'orange',Medium:'blue',Low:'grey'})[doc.severity]||'grey','status,=,'+doc.status];},
    onload(listview){
        listview.page.add_inner_button(__('Warnings Summary'),()=>frappe.set_route('query-report','Tax Warnings Summary'));
        listview.page.add_inner_button(__('Preview Rules'),async()=>{
            const r=await frappe.call({method:'angola_tax.alerts.service.preview'});
            frappe.msgprint({title:__('Preview — No Notifications Sent'),message:__('Warnings found: {0}. Rules with evaluation errors: {1}. Review each rule using its Preview button.',[r.message.warnings.length,r.message.errors.length])});
        });
    }
};
