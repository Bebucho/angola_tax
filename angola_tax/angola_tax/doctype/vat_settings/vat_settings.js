


// VAT PURCHASE CLASSIFICATION AUTO-MAPPING

function set_vat_purchase_modelo7_fields(cdt, cdn) {
    const row = locals[cdt][cdn];

    const field_map = {
        "Fixed Assets and Investments": ["16", "17"],
        "Inventory": ["18", "19"],
        "Other Consumption Goods": ["20", "21"],
        "Services": ["22", "23"],
    };

    const fields = field_map[row.modelo_7_category] || ["", ""];

    frappe.model.set_value(
        cdt,
        cdn,
        "modelo_7_base_field",
        fields[0]
    );

    frappe.model.set_value(
        cdt,
        cdn,
        "modelo_7_vat_field",
        fields[1]
    );
}


frappe.ui.form.on("VAT Purchase Classification Line", {

    modelo_7_category(frm, cdt, cdn) {
        set_vat_purchase_modelo7_fields(cdt, cdn);

        frm.refresh_field("purchase_classifications");
    },

    item(frm, cdt, cdn) {
        const row = locals[cdt][cdn];

        if (!row.item) {
            return;
        }

        frappe.db.get_value(
            "Item",
            row.item,
            "item_group"
        ).then(r => {
            if (
                r.message
                && r.message.item_group
                && !row.item_group
            ) {
                frappe.model.set_value(
                    cdt,
                    cdn,
                    "item_group",
                    r.message.item_group
                );
            }
        });
    },
});


frappe.ui.form.on("VAT Settings", {

    validate(frm) {
        (frm.doc.purchase_classifications || []).forEach(row => {

            const field_map = {
                "Fixed Assets and Investments": ["16", "17"],
                "Inventory": ["18", "19"],
                "Other Consumption Goods": ["20", "21"],
                "Services": ["22", "23"],
            };

            const fields =
                field_map[row.modelo_7_category] || ["", ""];

            frappe.model.set_value(
                row.doctype,
                row.name,
                "modelo_7_base_field",
                fields[0]
            );

            frappe.model.set_value(
                row.doctype,
                row.name,
                "modelo_7_vat_field",
                fields[1]
            );
        });
    },

});
