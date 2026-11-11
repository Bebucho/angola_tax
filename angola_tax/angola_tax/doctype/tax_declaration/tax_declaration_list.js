frappe.listview_settings['Tax Declaration']={
    has_indicator_for_draft:true,
    add_fields:['delivery_state','preparation_state','freshness','is_overdue','is_latest_version','effective_accepted_version'],
    filters:[['is_latest_version','=',1]],
    get_indicator(doc){
        if(doc.delivery_state==='Rejected')return[__('Rejected'),'red','delivery_state,=,Rejected'];
        if(doc.is_overdue)return[__('Overdue'),'red','is_overdue,=,1'];
        if(doc.freshness==='Sources Changed')return[__('Sources Changed'),'orange','freshness,=,Sources Changed'];
        const colors={'Accepted':'green','Acknowledged':'blue','Submitted':'blue','Not Submitted':'grey'};
        return[__(doc.delivery_state||doc.preparation_state),colors[doc.delivery_state]||'grey','delivery_state,=,'+doc.delivery_state];
    },
    onload(list){list.page.add_inner_button(__('Generate Expected Filings'),()=>frappe.prompt([
        {fieldname:'start',label:__('Period From'),fieldtype:'Date',reqd:1},
        {fieldname:'end',label:__('Period Through'),fieldtype:'Date',reqd:1},
        {fieldname:'configuration',label:__('Declaration Rule (optional)'),fieldtype:'Link',options:'Tax Deadline Configuration'}],
        async values=>{const r=await frappe.call({method:'angola_tax.declarations.service.generate_expected',args:values,freeze:true});
            frappe.msgprint(__('Created: {0}; existing: {1}; rules needing review: {2}',[
                r.message.created.length,r.message.existing.length,r.message.needs_review.length]));list.refresh();},__('Generate Declaration Calendar')));}
};
