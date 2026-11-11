frappe.ui.form.on('Tax Litigation DAP', {
    refresh(frm) {
        if (!frm.is_new() && frm.doc.docstatus === 1 && !frm.doc.agt_response) {
            frm.add_custom_button(__("Record AGT Reply"), () => agt_reply_dialog(frm));
        }
        if (!frm.is_new() && frm.doc.docstatus === 1 && frm.doc.agt_response && !frm.doc.company_decision && frm.doc.agt_response !== "Argument Accepted") {
            frm.add_custom_button(__("Company Decision"), () => company_decision_dialog(frm));
        }
        frm.add_custom_button(__("Review Tax Provision"), () => open_phase_provision_dialog(frm), __("Provision"));
        frm.add_custom_button(__("Technical Meeting"), () => {
            frappe.new_doc("Tax Technical Meeting", {
                reference_doctype: frm.doctype,
                reference_name: frm.doc.name
            });
        }, __("Record"));
        if (frm.doc.tax_settlement) {
            frm.add_custom_button(__("Open Tax Settlement"), () => frappe.set_route("Form", "Tax Settlement", frm.doc.tax_settlement));
        }
    }
});

function agt_reply_dialog(frm) {
    const d = new frappe.ui.Dialog({
        title: __("Record AGT Reply"),
        fields: [
            {fieldname:"agt_response",fieldtype:"Select",label:__("AGT Response"),options:"Argument Accepted\nArgument Rejected\nRevised Amount Proposed",reqd:1},
            {fieldname:"agt_response_date",fieldtype:"Date",label:__("Response Date"),reqd:1},
            {fieldname:"agt_response_reference",fieldtype:"Data",label:__("Reference")},
            {fieldname:"agt_response_document",fieldtype:"Attach",label:__("AGT Reply Document"),reqd:1},
            {fieldname:"agt_revised_amount",fieldtype:"Currency",label:__("Revised Amount"),depends_on:"eval:doc.agt_response === 'Revised Amount Proposed'"},
        ],
        primary_action_label: __("Save Reply"),
        primary_action(values) {
            Promise.all(Object.keys(values).map(field => frm.set_value(field, values[field]))).then(() => frm.save()).then(() => d.hide());
        }
    });
    d.show();
}

function company_decision_dialog(frm) {
    const d = new frappe.ui.Dialog({
        title: __("Company Decision"),
        fields: [
            {fieldname:"company_decision",fieldtype:"Select",label:__("Decision"),options:frm.doctype === "Tax Litigation Impugnacao Judicial" ? "Accept AGT Position\nContinue Litigation" : "Accept AGT Position\nContinue to Next Phase",reqd:1},
            {fieldname:"settlement_amount",fieldtype:"Currency",label:__("Accepted Settlement Amount"),depends_on:"eval:doc.company_decision === 'Accept AGT Position'"},
            {fieldname:"company_decision_date",fieldtype:"Date",label:__("Decision Date"),reqd:1,default:frappe.datetime.get_today()},
            {fieldname:"company_decision_notes",fieldtype:"Small Text",label:__("Notes")},
        ],
        primary_action_label: __("Record Decision"),
        primary_action(values) {
            Promise.all(Object.keys(values).map(field => frm.set_value(field, values[field]))).then(() => frm.save()).then(() => d.hide());
        }
    });
    d.show();
}

function open_phase_provision_dialog(frm) {
    const d = new frappe.ui.Dialog({
        title: __("Review Tax Provision"),
        fields: [
            {fieldname:"provision_decision",fieldtype:"Select",label:__("Provision Decision"),options:"No Provision\nFull Provision\nPartial Provision\nAlready Recognised\nDisclosure Only",reqd:1},
            {fieldname:"provision_amount",fieldtype:"Currency",label:__("Provision Amount"),depends_on:"eval:doc.provision_decision === 'Partial Provision'"},
            {fieldname:"notes",fieldtype:"Small Text",label:__("Review Notes")},
        ],
        primary_action_label: __("Record Review"),
        primary_action(values) {
            frappe.call({method:"angola_tax.angola_tax.doctype.tax_litigation.tax_litigation.record_litigation_provision_decision",args:{litigation_name:frm.doc.tax_litigation,provision_decision:values.provision_decision,provision_amount:values.provision_amount,notes:values.notes,phase_doctype:frm.doctype,phase_name:frm.doc.name},freeze:true,callback(r){if(r.message){d.hide();frm.reload_doc();}}});
        }
    });
    d.show();
}
