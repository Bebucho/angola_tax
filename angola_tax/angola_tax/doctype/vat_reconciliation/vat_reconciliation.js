frappe.ui.form.on("VAT Reconciliation", {
    refresh(frm) {
        if (frm.doc.vat_period_control) {
            frm.add_custom_button(
                __("VAT Period"),
                () => {
                    frappe.set_route(
                        "Form",
                        "VAT Period Control",
                        frm.doc.vat_period_control
                    );
                },
                __("View")
            );
        }

        if (frm.doc.docstatus === 0) {
            frm.add_custom_button(
                __("Apply to VAT Period"),
                () => {
                    frm.call(
                        "apply_to_vat_period"
                    ).then(() => {
                        frm.reload_doc();
                    });
                },
                __("Actions")
            );

            frm.add_custom_button(
                __("Create Adjustment"),
                () => {
                    frm.call(
                        "create_adjustment"
                    ).then((r) => {
                        if (r.message) {
                            frappe.show_alert({
                                message:
                                    __("Journal Entry {0} created", [
                                        r.message
                                    ]),
                                indicator: "green"
                            });
                        }

                        frm.reload_doc();
                    });
                },
                __("Actions")
            );
        }

        if (
            frm.doc.docstatus === 1
            && frm.doc.vat_period_control
            && !frm.doc.closing_journal_entry
        ) {
            frm.add_custom_button(
                __("Create VAT Closing"),
                () => {
                    frappe.call({
                        method:
                            "frappe.client.get",
                        args: {
                            doctype:
                                "VAT Period Control",
                            name:
                                frm.doc.vat_period_control
                        },
                        callback() {
                            frappe.call({
                                method:
                                    "angola_tax.angola_tax.doctype.vat_period_control.vat_period_control.create_vat_closing_from_reconciliation",
                                args: {
                                    vat_period_control:
                                        frm.doc.vat_period_control
                                },
                                freeze: true,
                                callback(r) {
                                    if (r.message) {
                                        frappe.show_alert({
                                            message:
                                                __("VAT Closing {0} created", [
                                                    r.message
                                                ]),
                                            indicator:
                                                "green"
                                        });
                                    }

                                    frm.reload_doc();
                                }
                            });
                        }
                    });
                },
                __("Actions")
            );
        }
    },

    reconciling_items(frm) {
        frm.dirty();
    }
});


frappe.ui.form.on("VAT Reconciling Item", {
    accounting_amount(frm, cdt, cdn) {
        calculate_difference(
            frm,
            cdt,
            cdn
        );
    },

    declared_amount(frm, cdt, cdn) {
        calculate_difference(
            frm,
            cdt,
            cdn
        );
    }
});


function calculate_difference(
    frm,
    cdt,
    cdn
) {
    const row = locals[cdt][cdn];

    frappe.model.set_value(
        cdt,
        cdn,
        "difference",
        flt(row.declared_amount)
            - flt(row.accounting_amount)
    );
}
