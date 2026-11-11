// Copyright (c) 2026, Metagest and contributors
// For license information, please see license.txt

const PROCESS_STAGES = ["Determination", "Liquidation", "Payment"];

function prepare_process_schedule(frm) {
    if (frm.doc.docstatus !== 0) return;

    const existing = new Set(
        (frm.doc.process_schedule || []).map(row => row.process_stage)
    );
    let added = false;

    for (const stage of PROCESS_STAGES) {
        if (existing.has(stage)) continue;

        frm.add_child("process_schedule", {
            process_stage: stage,
            target_timing: "Current Month",
            target_day_rule: "Fixed Day",
            critical_rule: "Fixed Rule",
            critical_timing: "Current Month",
            critical_day_rule: "Fixed Day"
        });
        added = true;
    }

    const grid = frm.fields_dict.process_schedule.grid;
    grid.cannot_add_rows = true;
    grid.cannot_delete_rows = true;
    grid.update_docfield_property("process_stage", "read_only", 1);

    if (added) {
        frm.refresh_field("process_schedule");
    } else {
        grid.refresh();
    }
}

frappe.ui.form.on("Tax Deadline Configuration", {
    refresh(frm) {
        prepare_process_schedule(frm);
        if (frm.is_new() || !frm.doc.company || !frm.doc.tax_type) return;

        frm.add_custom_button(__("Tax Account Configurations"), () => {
            frappe.set_route("List", "Tax Account Configuration", {
                company: frm.doc.company,
                tax_type: frm.doc.tax_type
            });
        }, __("View"));

        frm.add_custom_button(__("Tax Type"), () => {
            frappe.set_route("Form", "Tax Type", frm.doc.tax_type);
        }, __("View"));
    }
});
