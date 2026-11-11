frappe.ui.form.on("Fiscal Obligation", {
    refresh(frm) {
        if (
            frm.is_new() ||
            frm.doc.docstatus !== 1
        ) {
            return;
        }

        /*
         * TAX ASSESSMENT
         *
         * An authority assessment may arise independently of a
         * taxpayer filing. No filing prerequisite is imposed here.
         */
        if (
            flt(frm.doc.outstanding_amount) > 0 &&
            !["Closed", "Cancelled", "Superseded"].includes(frm.doc.status) &&
            !frm.doc.latest_tax_assessment
        ) {
            frm.add_custom_button(
                __("Tax Assessment"),
                () => {
                    create_tax_assessment_from_obligation(frm);
                },
                __("Create")
            );
        }

        if (frm.doc.latest_tax_assessment) {
            frm.add_custom_button(
                __("Open Tax Assessment"),
                () => {
                    frappe.set_route(
                        "Form",
                        "Tax Assessment",
                        frm.doc.latest_tax_assessment
                    );
                },
                __("Tax")
            );
        }

        if (frm.doc.latest_payment_plan) {
            frm.add_custom_button(
                __("Open Payment Plan"),
                () => {
                    frappe.set_route(
                        "Form",
                        "Tax Payment Plan",
                        frm.doc.latest_payment_plan
                    );
                },
                __("Tax")
            );
        }

        if (frm.doc.latest_tax_payment) {
            frm.add_custom_button(
                __("Open Tax Payment"),
                () => {
                    frappe.set_route(
                        "Form",
                        "Tax Payment",
                        frm.doc.latest_tax_payment
                    );
                },
                __("Tax")
            );
        }
    }
});


function create_tax_assessment_from_obligation(frm) {
    frappe.call({
        method:
            "angola_tax.angola_tax.doctype." +
            "tax_assessment.tax_assessment." +
            "get_new_assessment_from_obligation",

        args: {
            fiscal_obligation: frm.doc.name
        },

        freeze: true,

        callback(r) {
            if (!r.message) {
                return;
            }

            frappe.new_doc(
                "Tax Assessment",
                r.message
            );
        }
    });
}

// Classification status follows the selected fiscal meaning.
frappe.ui.form.on("Fiscal Obligation Movement", {
    movement_type(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        const status = row.movement_type && row.movement_type !== "Unclassified"
            ? "Reviewed" : "Unclassified";
        frappe.model.set_value(cdt, cdn, "classification_status", status);
    }
});
