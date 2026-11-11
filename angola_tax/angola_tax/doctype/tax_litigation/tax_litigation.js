frappe.ui.form.on("Tax Litigation", {
    refresh(frm) {
        if (frm.doc.provision_review_status === "Pending") {
            frm.add_custom_button(__("Record Provision Decision"), () => open_provision_dialog(frm.doc.name), __("Provision"));
        }
        if (!frm.is_new()) {
            load_phase_workflow_actions(frm);
        }
    }
});

function load_phase_workflow_actions(frm) {
    frappe.call({
        method: "angola_tax.angola_tax.doctype.tax_litigation.tax_litigation.get_litigation_current_phase",
        args: {litigation_name: frm.doc.name},
        callback(r) {
            const phase = r.message;
            if (!phase) {
                frm.add_custom_button(__("Create DAP Phase"), () => {
                    frappe.call({
                        method: "angola_tax.angola_tax.doctype.tax_litigation.tax_litigation.create_dap_phase_from_litigation",
                        args: {litigation_name: frm.doc.name},
                        freeze: true,
                        callback(result) {
                            if (result.message) {
                                frm.reload_doc();
                            }
                        },
                    });
                });
                return;
            }

            frm.add_custom_button(__("Open Current Phase"), () => {
                frappe.set_route("Form", phase.doctype, phase.name);
            });

            if (phase.docstatus !== 1) {
                return;
            }

            if (!phase.agt_response) {
                frm.add_custom_button(__("Record AGT Reply"), () => open_agt_reply_dialog(frm, phase));
                return;
            }
            if (phase.company_decision || phase.agt_response === "Argument Accepted") {
                return;
            }

            frm.add_custom_button(__("Accept AGT Position"), () => {
                open_company_decision_dialog(frm, phase, "Accept AGT Position");
            });

            const next_stage = {
                "Tax Litigation DAP": "Reclamação Administrativa",
                "Tax Litigation Reclamacao Administrativa": "Recurso Hierárquico",
                "Tax Litigation Recurso Hierarquico": "Impugnação Judicial",
            }[phase.doctype];
            const continue_decision = next_stage ? "Continue to Next Phase" : "Continue Litigation";
            const continue_label = next_stage
                ? __("Proceed to {0}", [next_stage])
                : __("Continue Litigation");
            frm.add_custom_button(continue_label, () => {
                open_company_decision_dialog(frm, phase, continue_decision);
            });
        }
    });
}

function open_agt_reply_dialog(frm, phase) {
    const d = new frappe.ui.Dialog({
        title: __("Record AGT Reply"),
        fields: [
            {fieldname: "agt_response", fieldtype: "Select", label: __("AGT Response"), options: "Argument Accepted\nArgument Rejected\nRevised Amount Proposed", reqd: 1},
            {fieldname: "response_date", fieldtype: "Date", label: __("Response Date"), reqd: 1, default: frappe.datetime.get_today()},
            {fieldname: "reference", fieldtype: "Data", label: __("Reference")},
            {fieldname: "document", fieldtype: "Attach", label: __("AGT Reply Document"), reqd: 1},
            {fieldname: "revised_amount", fieldtype: "Currency", label: __("Revised Amount"), depends_on: "eval:doc.agt_response === 'Revised Amount Proposed'"},
        ],
        primary_action_label: __("Save Reply"),
        primary_action(values) {
            frappe.call({
                method: "angola_tax.angola_tax.doctype.tax_litigation.tax_litigation.record_litigation_agt_reply",
                args: {
                    litigation_name: frm.doc.name,
                    agt_response: values.agt_response,
                    response_date: values.response_date,
                    reference: values.reference,
                    document: values.document,
                    revised_amount: values.revised_amount,
                },
                freeze: true,
                callback(r) {
                    if (r.message) {
                        d.hide();
                        frm.reload_doc();
                    }
                },
            });
        },
    });
    d.show();
}

function open_company_decision_dialog(frm, phase, decision) {
    const accepting = decision === "Accept AGT Position";
    const fields = [
        {fieldname: "decision_date", fieldtype: "Date", label: __("Decision Date"), reqd: 1, default: frappe.datetime.get_today()},
        {fieldname: "notes", fieldtype: "Small Text", label: __("Notes")},
    ];
    if (accepting) {
        fields.splice(1, 0, {
            fieldname: "settlement_amount",
            fieldtype: "Currency",
            label: __("Accepted Settlement Amount"),
            default: phase.agt_revised_amount || frm.doc.amount_under_dispute,
            reqd: 1,
        });
    }

    const d = new frappe.ui.Dialog({
        title: accepting ? __("Accept AGT Position") : __("Confirm Litigation Decision"),
        fields,
        primary_action_label: __("Confirm"),
        primary_action(values) {
            frappe.call({
                method: "angola_tax.angola_tax.doctype.tax_litigation.tax_litigation.record_litigation_company_decision",
                args: {
                    litigation_name: frm.doc.name,
                    company_decision: decision,
                    decision_date: values.decision_date,
                    settlement_amount: values.settlement_amount,
                    notes: values.notes,
                },
                freeze: true,
                callback(r) {
                    if (r.message) {
                        d.hide();
                        frm.reload_doc();
                    }
                },
            });
        },
    });
    d.show();
}

function open_provision_dialog(litigation_name, phase_doctype, phase_name) {
    const d = new frappe.ui.Dialog({
        title: __("Tax Provision Decision"),
        fields: [
            {fieldname:"provision_decision",fieldtype:"Select",label:__("Provision Decision"),options:"No Provision\nFull Provision\nPartial Provision\nAlready Recognised\nDisclosure Only",reqd:1},
            {fieldname:"provision_amount",fieldtype:"Currency",label:__("Provision Amount"),depends_on:"eval:doc.provision_decision === 'Partial Provision'"},
            {fieldname:"notes",fieldtype:"Small Text",label:__("Notes")},
        ],
        primary_action_label: __("Record Decision"),
        primary_action(values) {
            frappe.call({
                method:"angola_tax.angola_tax.doctype.tax_litigation.tax_litigation.record_litigation_provision_decision",
                args:{litigation_name,provision_decision:values.provision_decision,provision_amount:values.provision_amount,notes:values.notes,phase_doctype,phase_name},
                freeze:true,
                callback(r) { if (r.message) { d.hide(); frappe.set_route("Form", "Tax Litigation", litigation_name); } }
            });
        }
    });
    d.show();
}
