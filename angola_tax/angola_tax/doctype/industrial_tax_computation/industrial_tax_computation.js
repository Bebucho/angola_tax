frappe.ui.form.on("Industrial Tax Computation", {
    setup(frm) {
        frm.set_query("profile", () => ({filters: {company: frm.doc.company}}));
        frm.set_query("credit", "credit_allocations", () => ({filters: {company: frm.doc.company, annual_tax_type: frm.doc.tax_type, fiscal_year: (frm.doc.fiscal_period_from || "").slice(0,4), docstatus: 1}}));
        frm.set_query("cost_center", () => ({filters: {company: frm.doc.company, is_group: 0}}));
    },
    refresh(frm) {
        if (!frm.is_new() && frm.doc.docstatus === 0) {
            frm.add_custom_button(__("Capture GL Source"), () => {
                frm.call("capture_source").then(() => frm.reload_doc());
            }, __("Actions"));
        }
        if (frm.doc.docstatus === 1 && !frm.doc.accounting_complete) {
            frm.add_custom_button(__("Complete Accounting and Submit Obligation"), () => {
                frappe.confirm(__("Post the approved estimate true-up and credit offsets, then submit the annual obligation?"), () => {
                    frm.call("complete_accounting").then(() => frm.reload_doc());
                });
            }, __("Actions"));
        }
        for (const [field, doctype, label] of [
            ["fiscal_obligation", "Fiscal Obligation", "Fiscal Obligation"],
            ["recognition_journal_entry", "Journal Entry", "Recognition Journal"],
            ["offset_journal_entry", "Journal Entry", "Credit Offset Journal"]
        ]) {
            if (frm.doc[field]) frm.add_custom_button(__(label), () => {
                frappe.set_route("Form", doctype, frm.doc[field]);
            }, __("View"));
        }
    }
});
