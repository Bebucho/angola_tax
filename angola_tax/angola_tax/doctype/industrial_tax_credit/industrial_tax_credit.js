frappe.ui.form.on("Industrial Tax Credit", {
    setup(frm) {
        frm.set_query("agt_import", () => ({filters: {company: frm.doc.company, docstatus: 1}}));
        frm.set_query("cost_center", () => ({filters: {company: frm.doc.company, is_group: 0}}));
        frm.set_query("source_withholding", () => ({filters: {company: frm.doc.company, mechanism: "Suffered Withholding", docstatus: 1}}));
        frm.set_query("source_provisional", () => ({filters: {company: frm.doc.company, docstatus: 1, accounting_complete: 1}}));
        frm.set_query("collection_reference", () => ({filters: {company: frm.doc.company, status: "Confirmed"}}));
    },
    refresh(frm) {
        if (!frm.is_new()) frm.add_custom_button(__("Refresh Credit Balance"), () => {
            frm.call("refresh_balance").then(() => frm.reload_doc());
        }, __("Actions"));
    }
});
