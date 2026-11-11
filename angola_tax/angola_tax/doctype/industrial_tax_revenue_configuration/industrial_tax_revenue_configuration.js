frappe.ui.form.on("Industrial Tax Revenue Configuration", {
    setup(frm) {
        frm.set_query("account", "accounts", () => ({filters: {company: frm.doc.company, root_type: "Income", is_group: 0, disabled: 0}}));
    },
    refresh(frm) {
        if (!frm.is_new() && frm.doc.docstatus === 0) frm.add_custom_button(__("Load Income Accounts"), () => {
            frm.call("load_income_accounts").then(() => frm.reload_doc());
        }, __("Actions"));
    }
});
