frappe.ui.form.on("Tax Notification", {
    setup(frm) {
        frm.set_query("tax_type", () => ({
            filters: { company: frm.doc.company, active: 1 }
        }));

        frm.set_query("tax_credit_account", () => ({
            filters: { company: frm.doc.company, is_group: 0 }
        }));
    },

    claimed_amount(frm) {
        update_balance(frm);
    },

    tax_credit_applied(frm) {
        update_balance(frm);
    },

    refresh(frm) {
        if (frm.is_new() || !frm.doc.fiscal_obligation) return;

        if (frm.doc.docstatus === 1) {
            ["company", "tax_type", "fiscal_period_from", "fiscal_period_to",
                "claimed_amount", "tax_credit_applied", "tax_credit_account",
                "decision", "decision_date", "decision_reference", "decision_document",
                "decision_notes", "notification_reference"].forEach(fieldname => {
                frm.set_df_property(fieldname, "read_only", 1);
            });
        }

        if (frm.doc.docstatus === 1) {
            if (frm.doc.tax_assessment) {
                frm.add_custom_button(__("Tax Assessment"), () => {
                    frappe.set_route("Form", "Tax Assessment", frm.doc.tax_assessment);
                }, __("Open"));
            }
            if (frm.doc.tax_litigation) {
                frm.add_custom_button(__("Tax Litigation"), () => {
                    frappe.set_route("Form", "Tax Litigation", frm.doc.tax_litigation);
                }, __("Open"));
            }
        }
    }
});

function update_balance(frm) {
    const amount = flt(frm.doc.claimed_amount || 0);
    const credit = flt(frm.doc.tax_credit_applied || 0);
    frm.set_value("balance_amount", Math.max(amount - credit, 0));
}
