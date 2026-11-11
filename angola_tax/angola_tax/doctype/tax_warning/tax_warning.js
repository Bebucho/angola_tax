frappe.ui.form.on('Tax Warning', {
    refresh(frm) {
        if(frm.is_new())return;
        frm.dashboard.set_headline(__('Acknowledgement, snooze and dismissal do not resolve the fiscal issue. The warning resolves when its source condition clears.'));
        frm.add_custom_button(__('Open Source'),()=>frappe.set_route('Form',frm.doc.source_doctype,frm.doc.source_name));
        if(frm.doc.kind==='Notice'||frm.doc.status==='Resolved')return;
        const act=async(action,values={})=>{await frappe.call({method:'angola_tax.alerts.service.act',args:{name:frm.doc.name,action,...values}});await frm.reload_doc();};
        frm.add_custom_button(__('Acknowledge'),()=>act('Acknowledge'));
        frm.add_custom_button(__('Snooze'),()=>frappe.prompt([
            {fieldname:'reason',label:__('Reason'),fieldtype:'Small Text',reqd:1},
            {fieldname:'until',label:__('Remind After'),fieldtype:'Datetime',reqd:1}],v=>act('Snooze',v),__('Snooze Warning')));
        frm.add_custom_button(__('Dismiss'),()=>frappe.prompt({fieldname:'reason',label:__('Reason'),fieldtype:'Small Text',reqd:1},v=>act('Dismiss',v),__('Dismiss Warning')));
        if(['Snoozed','Dismissed','Acknowledged'].includes(frm.doc.status))frm.add_custom_button(__('Resume'),()=>act('Resume'));
    }
});
