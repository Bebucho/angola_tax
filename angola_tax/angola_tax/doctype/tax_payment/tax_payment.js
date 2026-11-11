frappe.ui.form.on("Tax Payment", {
    setup(frm) {
        frm.set_query("bank_cash_account", function () {
            return {
                filters: {
                    company: frm.doc.company,
                    account_type: "Bank",
                    is_group: 0
                }
            };
        });

        frm.set_query("collection_reference", function () {
            return {
                filters: {
                    status: "Issued"
                }
            };
        });
    },

    refresh(frm) {
        frm.toggle_reqd(
            "bank_cash_account",
            frm.doc.payment_mode === "Create Journal Entry"
        );

        frm.toggle_reqd(
            "source_voucher_type",
            frm.doc.payment_mode === "Link Existing Voucher"
        );

        frm.toggle_reqd(
            "source_voucher",
            frm.doc.payment_mode === "Link Existing Voucher"
        );

        if (frm.doc.journal_entry) {
            frm.add_custom_button(
                __("Open Journal Entry"),
                function () {
                    frappe.set_route(
                        "Form",
                        "Journal Entry",
                        frm.doc.journal_entry
                    );
                },
                __("Accounting")
            );
        }

        if (
            frm.doc.source_voucher_type &&
            frm.doc.source_voucher &&
            !(
                frm.doc.source_voucher_type === "Journal Entry" &&
                frm.doc.source_voucher === frm.doc.journal_entry
            )
        ) {
            frm.add_custom_button(
                __("Open Source Voucher"),
                function () {
                    frappe.set_route(
                        "Form",
                        frm.doc.source_voucher_type,
                        frm.doc.source_voucher
                    );
                },
                __("Accounting")
            );
        }
    },

    collection_reference(frm) {
        if (!frm.doc.collection_reference) {
            return;
        }

        // Server-side validation remains authoritative.
        // Saving the document loads the complete RUPE context.
        frm.dirty();
    },

    payment_mode(frm) {
        frm.toggle_reqd(
            "bank_cash_account",
            frm.doc.payment_mode === "Create Journal Entry"
        );

        frm.toggle_reqd(
            "source_voucher_type",
            frm.doc.payment_mode === "Link Existing Voucher"
        );

        frm.toggle_reqd(
            "source_voucher",
            frm.doc.payment_mode === "Link Existing Voucher"
        );
    }
});
