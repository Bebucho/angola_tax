frappe.ui.form.on("VAT Period Control", {
    refresh(frm) {
        if (frm.doc.docstatus === 0 && !frm.is_new()) {
            frm.add_custom_button(
                __("Load Accounting"),
                function () {
                    frappe.call({
                        method:
                            "angola_tax.angola_tax.doctype." +
                            "vat_period_control.vat_period_control." +
                            "reload_accounting",
                        args: {
                            name: frm.doc.name
                        },
                        freeze: true,
                        freeze_message:
                            __("Loading VAT accounting movements..."),
                        callback: function (r) {
                            if (!r.exc) {
                                frm.reload_doc();
                                frappe.show_alert({
                                    message:
                                        __(
                                            "VAT accounting loaded: {0} movement(s)",
                                            [r.message.transactions]
                                        ),
                                    indicator: "green"
                                });
                            }
                        }
                    });
                }
            );
        }

        if (frm.doc.company) {
            frm.set_query("vat_settings", function () {
                return {
                    filters: {
                        company: frm.doc.company,
                        active: 1
                    }
                };
            });
        }
    },

    company(frm) {
        if (!frm.doc.company) {
            frm.set_value("vat_settings", null);
            return;
        }

        frappe.db.get_value(
            "VAT Settings",
            {
                company: frm.doc.company,
                active: 1
            },
            "name"
        ).then((r) => {
            if (r.message && r.message.name) {
                frm.set_value(
                    "vat_settings",
                    r.message.name
                );
            }
        });
    }
});


frappe.ui.form.on("VAT Period Control", {
    refresh(frm) {
        if (
            frm.doc.docstatus === 0
            && frm.doc.modelo_7_confirmed
            && !frm.doc.closing_journal_entry
        ) {
            frm.add_custom_button(
                __("VAT Reconciliation"),
                () => {
                    frappe.call({
                        method:
                            "angola_tax.angola_tax.doctype.vat_reconciliation.vat_reconciliation.create_from_vat_period",
                        args: {
                            vat_period_control:
                                frm.doc.name
                        },
                        freeze: true,
                        callback(r) {
                            if (r.message) {
                                frappe.set_route(
                                    "Form",
                                    "VAT Reconciliation",
                                    r.message
                                );
                            }
                        }
                    });
                },
                __("Create")
            );
        }

        if (frm.doc.vat_reconciliation) {
            frm.add_custom_button(
                __("VAT Reconciliation"),
                () => {
                    frappe.set_route(
                        "Form",
                        "VAT Reconciliation",
                        frm.doc.vat_reconciliation
                    );
                },
                __("View")
            );
        }
    }
});
