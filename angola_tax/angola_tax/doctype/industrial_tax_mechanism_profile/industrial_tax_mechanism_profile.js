frappe.ui.form.on("Industrial Tax Mechanism Profile", {
    setup(frm) {
        frm.set_query("annual_profile", () => ({filters: {company: frm.doc.company, docstatus: 1}}));
        frm.set_query("tax_type", () => ({filters: {company: frm.doc.company, active: 1, docstatus: 1}}));
        frm.set_query("deadline_configuration", () => ({filters: {company: frm.doc.company, tax_type: frm.doc.tax_type, active: 1, docstatus: 1}}));
        frm.set_query("configuration", "account_roles", () => ({filters: {company: frm.doc.company, tax_type: frm.doc.tax_type, active: 1, docstatus: 1}}));
    },
    mechanism(frm) {if (frm.doc.docstatus === 0) return frm.set_value("rate", frm.doc.mechanism === "Own Provisional" ? 2 : 6.5);}
});
