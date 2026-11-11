frappe.ui.form.on("Tax Technical Meeting", {
    setup(frm) {
        const allowed = [
            "Tax Litigation DAP",
            "Tax Litigation Reclamacao Administrativa",
            "Tax Litigation Recurso Hierarquico",
            "Tax Litigation Impugnacao Judicial"
        ];
        frm.set_query("reference_doctype", () => ({filters: [["DocType", "name", "in", allowed]]}));
    }
});
