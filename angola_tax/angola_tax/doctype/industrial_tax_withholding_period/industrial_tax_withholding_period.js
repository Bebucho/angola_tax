frappe.ui.form.on("Industrial Tax Withholding Period", {
    setup(frm) {
        frm.set_query("profile", () => ({filters: {company: frm.doc.company, mechanism: ["in", ["Collected Resident Withholding", "Collected Nonresident Withholding"]], docstatus: 1}}));
    },
    refresh(frm) {
        if (!frm.is_new() && frm.doc.source_format === "GL Withholding v1" && frm.doc.docstatus !== 2) frm.add_custom_button(__("Refresh Invoice Links from Payments"), () => {
            frappe.confirm(__("Refresh invoice links from the source payments? This replaces map detail and resets its review; the GL obligation remains unchanged."), () => {
                frm.call("refresh_agt_invoice_links").then(() => frm.reload_doc());
            });
        }, __("Actions"));
        if (frm.doc.docstatus === 1 && frm.doc.accounting_complete) frm.add_custom_button(__("Export AGT Monthly Map"), () => {
            frm.call("export_agt_map").then(r => {if (r.message) window.open(r.message, "_blank", "noopener");});
        }, __("Actions"));
        if (!frm.is_new() && frm.doc.docstatus === 0) frm.add_custom_button(__("Capture Liability GL"), () => {
            frm.call("capture_source").then(() => frm.reload_doc());
        }, __("Actions"));
        if (frm.doc.fiscal_obligation) frm.add_custom_button(__("Fiscal Obligation"), () => frappe.set_route("Form", "Fiscal Obligation", frm.doc.fiscal_obligation), __("View"));
    }
});
