frappe.ui.form.on('Tax Declaration', {
    refresh(frm) {
        frm.set_query('deadline_configuration', () => ({filters:{company:frm.doc.company,
            declaration_purpose:'Declaration', declaration_type:frm.doc.declaration_type, docstatus:1, active:1}}));
        if (frm.is_new()) return;
        if (frm.doc.docstatus === 0) {
            if(frm.doc.preparation_state==='Prepared')frm.page.set_primary_action(__('Approve Package'),async()=>{
                await frm.save('Submit');await frm.reload_doc();
            });
            frm.add_custom_button(__('Prepare Package'), async () => {
                if (frm.is_dirty()) await frm.save();
                await frm.call('prepare'); await frm.reload_doc();
            });
        }
        if (frm.doc.snapshot_file) frm.add_custom_button(__('Check Sources'), async () => {
            await frm.call('check_sources'); await frm.reload_doc();
        });
        if (frm.doc.docstatus === 1) {
            frm.dashboard.set_headline(__('Internally approved. Portal delivery: {0}',[__(frm.doc.delivery_state)]));
            frm.add_custom_button(__('Record Portal Evidence'), () => {
                const attempts = [...new Set((frm.doc.events||[]).filter(r=>r.action==='Submission').map(r=>r.attempt_id))];
                const dialog = new frappe.ui.Dialog({title:__('Portal Evidence'),fields:[
                    {fieldname:'action',label:__('Action'),fieldtype:'Select',options:'Submission\nAcknowledged\nAccepted\nRejected\nOutcome Unknown\nEvidence Correction',reqd:1},
                    {fieldname:'occurred_on',label:__('Actual Occurrence Time'),fieldtype:'Datetime',reqd:1},
                    {fieldname:'attempt_id',label:__('Attempt ID'),fieldtype:'Data',reqd:1,
                        description:__('New submission: use a new stable ID. Response: use the matching submission ID.')},
                    {fieldname:'external_reference',label:__('Portal Reference'),fieldtype:'Data'},
                    {fieldname:'evidence',label:__('Private Evidence URL'),fieldtype:'Data',reqd:1,
                        description:__('Use Upload Evidence below. The file is attached privately to this declaration.')},
                    {fieldname:'reason',label:__('Reason / Evidence Correction'),fieldtype:'Small Text'}],
                    primary_action_label:__('Record Evidence'),primary_action:async values=>{
                        await frm.call('record_delivery',values);dialog.hide();await frm.reload_doc();
                    }});
                dialog.show();
                $('<button class="btn btn-default btn-sm">'+__('Upload Evidence')+'</button>')
                    .appendTo(dialog.body).on('click',()=>new frappe.ui.FileUploader({
                        doctype:frm.doctype,docname:frm.doc.name,is_private:1,
                        on_success:file=>dialog.set_value('evidence',file.file_url)
                    }));
                if(attempts.length)dialog.set_value('attempt_id',attempts[attempts.length-1]);
            });
            frm.add_custom_button(__('Create Revision'),()=>frappe.prompt([
                {fieldname:'kind',label:__('Kind'),fieldtype:'Select',options:'Replacement\nCorrective',reqd:1},
                {fieldname:'reason',label:__('Reason'),fieldtype:'Small Text',reqd:1}],async values=>{
                    const r=await frm.call('create_revision',values);frappe.set_route('Form','Tax Declaration',r.message);
                },__('New Declaration Version')));
        }
        if(frm.doc.effective_accepted_version)frm.dashboard.set_headline(
            __('Effective accepted filing: {0}',[frappe.utils.escape_html(frm.doc.effective_accepted_version)]));
        if(frm.doc.freshness==='Sources Changed')frm.dashboard.add_comment(
            __('Sources changed. Preserve this package and review the originating fiscal process.'),'orange',true);
        if(frm.doc.generated_file)frm.add_custom_button(__('Open Prepared File'),()=>window.open(frm.doc.generated_file,'_blank'));
    },
    declaration_type(frm){frm.set_value('deadline_configuration','');},
    source_doctype(frm){frm.set_value('source_name','');}
});
