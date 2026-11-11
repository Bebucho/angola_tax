frappe.ui.form.on('Tax Deadline Configuration',{
    refresh(frm){
        if(frm.doc.declaration_purpose==='Declaration')frm.set_query('declaration_type',()=>({filters:{}}));
    },
    declaration_purpose(frm){
        if(frm.doc.declaration_purpose==='Declaration' && !(frm.doc.deadline_rules||[]).length){
            const row=frm.add_child('deadline_rules',{deadline_type:'Submission',periodicity:'Monthly',
                due_month_offset:1,use_end_of_month:1,declaration_anchor:'Period End',declaration_working_day:1});
            frm.refresh_field('deadline_rules');
        }
    }
});
