frappe.ui.form.on("VAT Import Document", {

    setup(frm) {

        frm.set_query(
            "source_cost_stock_account",
            function () {
                return {
                    filters: {
                        company: frm.doc.company,
                        is_group: 0
                    }
                };
            }
        );
    },


    refresh(frm) {

        if (frm.is_new()) {
            return;
        }

        if (!frm.doc.recognition_journal_entry) {

            frm.add_custom_button(
                __("Recognise Customs VAT"),
                function () {

                    frappe.call({
                        method:
                            "angola_tax.angola_tax.doctype."
                            + "vat_import_document."
                            + "vat_import_document."
                            + "create_customs_recognition",

                        args: {
                            name: frm.doc.name
                        },

                        freeze: true,

                        callback() {
                            frm.reload_doc();
                        }
                    });
                },
                __("Accounting")
            );
        }


        if (
            frm.doc.cost_treatment === "Expense / Cost"
            && frm.doc.debit_note_reference
            && frm.doc.debit_note_date
            && frm.doc.source_cost_stock_account
            && !frm.doc.debit_note_reclassification_journal_entry
        ) {

            frm.add_custom_button(
                __("Clear Broker Debit Note"),
                function () {

                    frappe.call({
                        method:
                            "angola_tax.angola_tax.doctype."
                            + "vat_import_document."
                            + "vat_import_document."
                            + "create_debit_note_reclassification",

                        args: {
                            name: frm.doc.name
                        },

                        freeze: true,

                        callback() {
                            frm.reload_doc();
                        }
                    });
                },
                __("Accounting")
            );
        }


        if (frm.doc.purchase_receipt) {

            frm.add_custom_button(
                __("Purchase Receipt"),
                function () {
                    frappe.set_route(
                        "Form",
                        "Purchase Receipt",
                        frm.doc.purchase_receipt
                    );
                },
                __("View")
            );
        }


        if (frm.doc.landed_cost_voucher) {

            frm.add_custom_button(
                __("Landed Cost Voucher"),
                function () {
                    frappe.set_route(
                        "Form",
                        "Landed Cost Voucher",
                        frm.doc.landed_cost_voucher
                    );
                },
                __("View")
            );
        }


        if (frm.doc.recognition_journal_entry) {

            frm.add_custom_button(
                __("Customs Recognition JE"),
                function () {
                    frappe.set_route(
                        "Form",
                        "Journal Entry",
                        frm.doc.recognition_journal_entry
                    );
                },
                __("View")
            );
        }


        if (
            frm.doc.debit_note_reclassification_journal_entry
        ) {

            frm.add_custom_button(
                __("Debit Note Reclassification JE"),
                function () {
                    frappe.set_route(
                        "Form",
                        "Journal Entry",
                        frm.doc
                            .debit_note_reclassification_journal_entry
                    );
                },
                __("View")
            );
        }
    }
});
