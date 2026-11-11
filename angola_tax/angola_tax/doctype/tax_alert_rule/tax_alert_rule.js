frappe.ui.form.on('Tax Alert Rule', {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button(__('Preview Current Warnings'), async () => {
            if (frm.is_dirty()) await frm.save();
            const r = await frappe.call({method:'angola_tax.alerts.service.preview',args:{rule:frm.doc.name}});
            const d = new frappe.ui.Dialog({title:__('Warning Preview — No Notifications Sent'),size:'extra-large',fields:[
                {fieldname:'results',fieldtype:'HTML'}]});
            const e=frappe.utils.escape_html;
            const warnings=r.message.warnings||[];
            d.fields_dict.results.$wrapper.html('<p>'+__('Warnings: {0}. No fiscal documents changed.',[warnings.length])+'</p>'+
                warnings.map(w=>'<div class="border rounded p-3 mb-2"><strong>'+e(w.title)+' · '+e(w.company)+'</strong><p>'+e(w.explanation)+'</p><p>'+e(w.action_needed)+'</p></div>').join('')+
                (r.message.errors||[]).map(x=>'<p class="text-danger">'+e(x.message)+'</p>').join(''));
            d.show();
        });
        if(frm.doc.condition==='legacy_compliance')frm.add_custom_button(__('Review Previous Tasks'),async()=>{
            const r=await frappe.call({method:'angola_tax.alerts.service.legacy_tasks',args:{company:frm.doc.company}});
            const tasks=r.message||[];
            if(!tasks.length){frappe.msgprint(__('No readable open fiscal tasks found.'));return;}
            const choices=tasks.filter(t=>t.replacement_warnings.length).map(t=>({label:t.name+' · '+t.fiscal_obligation,value:t.name}));
            if(!choices.length){frappe.msgprint(__('Previous tasks remain preserved. Evaluate enabled rules to create replacement warnings first.'));return;}
            const d=new frappe.ui.Dialog({title:__('Retire Previous Tasks'),fields:[
                {fieldname:'tasks',label:__('Tasks Replaced by Active Warnings'),fieldtype:'MultiCheck',options:choices},
                {fieldname:'reason',label:__('Migration Reason'),fieldtype:'Small Text',reqd:1}],
                primary_action_label:__('Retire Selected Tasks'),primary_action:async v=>{
                    await frappe.call({method:'angola_tax.alerts.service.retire_legacy_tasks',args:{company:frm.doc.company,todo_names:JSON.stringify(v.tasks||[]),reason:v.reason}});d.hide();
                }});d.show();
        });
        frm.dashboard.set_headline(__('Rules are installed disabled. Enable the company fiscal replacement rule first; then enable the required conditions.'));
    },
    trigger_label(frm) {
        const choices = {"Fiscal action required": ["legacy_compliance", "Fiscal Compliance"], "Determination preparation deadline": ["determination_due", "Fiscal Compliance"], "Liquidation preparation deadline": ["liquidation_due", "Payments"], "Internal payment target": ["payment_internal_due", "Payments"], "Payment deadline": ["payment_due", "Payments"], "Declaration preparation": ["preparation_due", "Declarations"], "Declaration approval": ["approval_due", "Declarations"], "Declaration delivery": ["submission_due", "Declarations"], "Required filing not created": ["missing_filing", "Declarations"], "Rejected declaration": ["rejected_submission", "Declarations"], "Declaration correction pending": ["correction_pending", "Declarations"], "Declaration sources changed": ["sources_changed", "Declarations"], "Submission evidence missing": ["submission_evidence", "Declarations"], "Acknowledgement pending or evidence missing": ["acknowledgement_evidence", "Declarations"], "Payment confirmation pending or evidence missing": ["payment_evidence", "Payments"], "Tax movements require classification": ["unclassified_gl", "Accounting Review"], "Withholding movements require classification": ["withholding_gl", "Industrial Tax"], "Withholding map incomplete": ["incomplete_map", "Industrial Tax"], "AGT amounts require review": ["agt_allocation", "Industrial Tax"], "VAT movements require classification": ["vat_classification", "VAT"], "VAT reconciliation differences": ["vat_reconciliation", "VAT"], "Provisional tax differences": ["provisional_variance", "Industrial Tax"], "Unresolved fiscal differences": ["fiscal_variance", "Fiscal Compliance"], "Payment plan reconciliation difference": ["plan_variance", "Payments"], "Fiscal configuration requires review": ["configuration_health", "Configuration"], "Assessment decision deadline": ["assessment_due", "Disputes"], "Taxpayer action deadline": ["litigation_due", "Disputes"], "Authority reply requires follow-up": ["authority_response", "Disputes"], "Notification response deadline needs review": ["notification_deadline", "Disputes"], "Payment plan instalment": ["instalment_due", "Payments"], "Collection reference requires review": ["collection_review", "Payments"], "Fiscal deadline configuration missing": ["fiscal_deadline_missing", "Configuration"]};
        const selected=choices[frm.doc.trigger_label];
        if(selected){frm.set_value('condition',selected[0]);frm.set_value('fiscal_process',selected[1]);}
    }
});
