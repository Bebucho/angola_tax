// Copyright (c) 2026, Metagest and contributors
// For license information, please see license.txt

frappe.ui.form.on("Tax Type", {
    refresh(frm) {
        if (frm.is_new()) return;

        const company = frm.doc.company;
        const taxType = frm.doc.name;
        frappe.db.get_value("Tax Account Configuration", {
            company: company,
            tax_type: taxType,
            docstatus: ["!=", 2]
        }, "name").then(r => {
            if (frm.is_new() || frm.doc.name !== taxType || frm.doc.company !== company) return;
            if (r.message && r.message.name) return;

            frm.add_custom_button(__("Tax Account Configuration"), () => {
                frappe.new_doc("Tax Account Configuration", {
                    company: frm.doc.company,
                    tax_type: frm.doc.name,
                    tax_name: frm.doc.tax_name
                });
            }, __("Make"));
        });

        frm.add_custom_button(__("Tax Account Configurations"), () => {
            frappe.set_route("List", "Tax Account Configuration", {
                company: frm.doc.company,
                tax_type: frm.doc.name
            });
        }, __("View"));

        frm.add_custom_button(__("Tax Deadline Configurations"), () => {
            frappe.set_route("List", "Tax Deadline Configuration", {
                company: frm.doc.company,
                tax_type: frm.doc.name
            });
        }, __("View"));
    }
});
