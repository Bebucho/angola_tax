frappe.ui.form.on("Tax Payment Plan", {
    refresh(frm) {
        if (
            frm.doc.docstatus === 1 &&
            frm.doc.status === "Active"
        ) {
            frm.add_custom_button(
                __("Reissue Payment Plan"),
                function () {
                    frm.call("make_reissue").then(r => {
                        if (r.message) {
                            frappe.set_route(
                                "Form",
                                "Tax Payment Plan",
                                r.message
                            );
                        }
                    });
                },
                __("Tax")
            );
        }

        if (frm.doc.opening_journal_entry) {
            frm.add_custom_button(
                __("Open Journal Entry"),
                function () {
                    frappe.set_route(
                        "Form",
                        "Journal Entry",
                        frm.doc.opening_journal_entry
                    );
                },
                __("Accounting")
            );
        }

        if (frm.doc.previous_plan) {
            frm.add_custom_button(
                __("Open Previous Plan"),
                function () {
                    frappe.set_route(
                        "Form",
                        "Tax Payment Plan",
                        frm.doc.previous_plan
                    );
                },
                __("Tax")
            );
        }
    }
});
