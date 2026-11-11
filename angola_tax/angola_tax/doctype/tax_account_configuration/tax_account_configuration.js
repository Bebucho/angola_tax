// Copyright (c) 2026, Metagest and contributors
// For license information, please see license.txt

frappe.ui.form.on("Tax Account Configuration", {
    refresh(frm) {
        if (frm.is_new() || !frm.doc.company || !frm.doc.tax_type) return;

        const company = frm.doc.company;
        const taxType = frm.doc.tax_type;
        frappe.db.get_value("Tax Deadline Configuration", {
            company: company,
            tax_type: taxType,
            docstatus: ["!=", 2]
        }, "name").then(r => {
            if (frm.is_new() || frm.doc.company !== company || frm.doc.tax_type !== taxType) return;
            if (r.message && r.message.name) return;

            frm.add_custom_button(__("Tax Deadline Configuration"), () => {
                frappe.new_doc("Tax Deadline Configuration", {
                    company: frm.doc.company,
                    tax_type: frm.doc.tax_type,
                    tax_name: frm.doc.tax_name
                });
            }, __("Make"));
        });

        frm.add_custom_button(__("Tax Deadline Configurations"), () => {
            frappe.set_route("List", "Tax Deadline Configuration", {
                company: frm.doc.company,
                tax_type: frm.doc.tax_type
            });
        }, __("View"));

        frm.add_custom_button(__("Tax Type"), () => {
            frappe.set_route("Form", "Tax Type", frm.doc.tax_type);
        }, __("View"));
    }
});
