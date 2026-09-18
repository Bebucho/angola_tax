frappe.ui.form.on("Fiscal Obligation", {
    refresh(frm) {
        const unclassified = get_unclassified_movements(frm);

        if (!unclassified.length) {
            return;
        }

        frm.dashboard.set_headline(
            __(
                "{0} accounting movement(s) require fiscal classification.",
                [unclassified.length]
            )
        );

        frm.add_custom_button(
            __("Review Unclassified Movements"),
            () => {
                review_unclassified_movements(frm);
            },
            __("Tax Control")
        );
    },
});


function get_unclassified_movements(frm) {
    return (frm.doc.movements || []).filter(
        row =>
            row.movement_type === "Unclassified" ||
            row.classification_status === "Unclassified"
    );
}


function review_unclassified_movements(frm) {
    const rows = get_unclassified_movements(frm);

    if (!rows.length) {
        frappe.msgprint(__("There are no unclassified movements."));
        return;
    }

    let index = 0;

    function open_row() {
        if (index >= rows.length) {
            frm.refresh_field("movements");

            frappe.show_alert({
                message: __(
                    "Review completed. Save the Fiscal Obligation to confirm the classifications."
                ),
                indicator: "green",
            });

            return;
        }

        const row = rows[index];

        const dialog = new frappe.ui.Dialog({
            title: __(
                "Review Movement {0} of {1}",
                [index + 1, rows.length]
            ),

            fields: [
                {
                    fieldname: "posting_date",
                    label: __("Posting Date"),
                    fieldtype: "Date",
                    default: row.posting_date,
                    read_only: 1,
                },
                {
                    fieldname: "voucher_type",
                    label: __("Voucher Type"),
                    fieldtype: "Data",
                    default: row.voucher_type,
                    read_only: 1,
                },
                {
                    fieldname: "voucher_no",
                    label: __("Voucher"),
                    fieldtype: "Data",
                    default: row.voucher_no,
                    read_only: 1,
                },
                {
                    fieldname: "account",
                    label: __("Account"),
                    fieldtype: "Data",
                    default: row.account,
                    read_only: 1,
                },
                {
                    fieldname: "debit",
                    label: __("Debit"),
                    fieldtype: "Currency",
                    default: row.debit,
                    read_only: 1,
                },
                {
                    fieldname: "credit",
                    label: __("Credit"),
                    fieldtype: "Currency",
                    default: row.credit,
                    read_only: 1,
                },
                {
                    fieldname: "remarks",
                    label: __("Remarks"),
                    fieldtype: "Small Text",
                    default: row.remarks,
                    read_only: 1,
                },
                {
                    fieldname: "movement_type",
                    label: __("Fiscal Classification"),
                    fieldtype: "Select",
                    options: [
                        "",
                        "Determination",
                        "Assessment",
                        "Payment",
                        "Adjustment Increase",
                        "Adjustment Decrease",
                        "Reversal",
                        "Reclassification",
                    ].join("\n"),
                    reqd: 1,
                },
            ],

            primary_action_label: __("Apply and Continue"),

            primary_action(values) {
                frappe.model.set_value(
                    row.doctype,
                    row.name,
                    "movement_type",
                    values.movement_type
                );

                dialog.hide();

                index += 1;
                open_row();
            },
        });

        dialog.set_secondary_action_label(__("Skip"));

        dialog.set_secondary_action(() => {
            dialog.hide();

            index += 1;
            open_row();
        });

        dialog.show();
    }

    open_row();
}
