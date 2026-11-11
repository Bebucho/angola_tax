frappe.ui.form.on("Industrial Tax Withholding", {
    setup(frm) {
        frm.set_query("profile", () => ({filters: {company: frm.doc.company, mechanism: ["!=", "Own Provisional"], docstatus: 1}}));
        frm.set_query("event_voucher_type", () => ({filters: {name: ["in", ["Payment Entry", "Journal Entry"]]}}));
        frm.set_query("accounting_voucher_type", () => ({filters: {name: ["in", ["Payment Entry", "Journal Entry", "Sales Invoice", "Purchase Invoice"]]}}));
    },
    profile(frm) {
        if (!frm.doc.profile) return;
        return frappe.db.get_value("Industrial Tax Mechanism Profile", frm.doc.profile, "mechanism").then(r => {
            const mechanism = r.message && r.message.mechanism;
            return frm.set_value({mechanism, party_type: mechanism === "Suffered Withholding" ? "Customer" : "Supplier"});
        });
    },
    refresh(frm) {
        if (!frm.is_new() && frm.doc.docstatus === 0) frm.add_custom_button(__("Propose Invoice Allocation"), () => {
            frm.call("capture_invoice_allocations").then(() => frm.reload_doc());
        }, __("Actions"));
        if (!frm.is_new() && frm.doc.docstatus === 0) frm.add_custom_button(__("Capture Withholding GL Evidence"), () => {
            frm.call("capture_source").then(() => frm.reload_doc());
        }, __("Actions"));
        if (frm.doc.docstatus === 1 && frm.doc.mechanism === "Suffered Withholding") frm.add_custom_button(__("Create Suffered Withholding Credit"), () => {
            frm.call("create_credit").then(r => {if (r.message) frappe.set_route("Form", "Industrial Tax Credit", r.message);});
        }, __("Actions"));
    }
});
