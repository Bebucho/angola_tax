frappe.ui.form.on("Industrial Tax AGT Import", {
    setup(frm) {
        frm.set_query("source_withholding", "allocations", () => ({filters: {company: frm.doc.company, mechanism: "Suffered Withholding", docstatus: 1}}));
    },
    refresh(frm) {
        if (frm.is_new()) return;
        if (frm.doc.docstatus === 0) {
            frm.add_custom_button(__("Capture AGT Report"), () => {
                frappe.confirm(__("Capture the file and replace current observations and allocations?"), () => frm.call("capture_report").then(() => frm.reload_doc()));
            }, __("Actions"));
            if (frm.doc.file_hash) {
                frm.add_custom_button(__("Suggest Matches"), () => {
                    frappe.confirm(__("Replace allocations with unapproved exact-match suggestions? Review eligibility dates and amounts before submission."), () => frm.call("suggest_matches").then(() => frm.reload_doc()));
                }, __("Actions"));
                frm.add_custom_button(__("Review Eligibility Dates"), () => {
                    frappe.prompt([
                        {fieldname: "eligibility_date", fieldtype: "Date", label: __("Reviewed Eligibility Date"), reqd: 1},
                        {fieldname: "eligibility_reason", fieldtype: "Small Text", label: __("Eligibility Date Basis"), reqd: 1}
                    ], values => {
                        (frm.doc.allocations || []).forEach(row => {
                            row.eligibility_date = values.eligibility_date;
                            row.eligibility_reason = values.eligibility_reason;
                        });
                        frm.set_value("review_complete", 0);frm.refresh_field("allocations");frm.dirty();
                    }, __("Review dates for all suggested allocations"));
                }, __("Actions"));
                frm.add_custom_button(__("Add Allocation"), () => {
                    const rows = frm.doc.report_rows || [];
                    frappe.prompt([
                        {fieldname: "row_number", fieldtype: "Select", label: __("AGT Invoice Row"), options: rows.map(r => `${r.idx}: ${r.invoice_number} / ${r.payer_tax_id} / ${r.tax_paid}`).join("\n"), reqd: 1},
                        {fieldname: "source_withholding", fieldtype: "Link", options: "Industrial Tax Withholding", label: __("Reviewed Withholding"), reqd: 1, get_query: () => ({filters: {company: frm.doc.company, mechanism: "Suffered Withholding", docstatus: 1}})},
                        {fieldname: "amount", fieldtype: "Currency", label: __("Amount"), reqd: 1},
                        {fieldname: "eligibility_date", fieldtype: "Date", label: __("Reviewed Eligibility Date"), reqd: 1},
                        {fieldname: "eligibility_reason", fieldtype: "Small Text", label: __("Eligibility Date Basis"), reqd: 1}
                    ], values => {
                        const row = rows.find(r => r.idx === parseInt(values.row_number, 10));
                        frm.add_child("allocations", {portal_row_key: row.row_key, source_withholding: values.source_withholding,
                            amount: values.amount, eligibility_date: values.eligibility_date, eligibility_reason: values.eligibility_reason});
                        frm.set_value("review_complete", 0);frm.refresh_field("allocations");frm.dirty();
                    }, __("Allocate Portal Paid Tax"));
                }, __("Actions"));
            }
        }
        if (frm.doc.docstatus === 1) frm.add_custom_button(__("Create Draft Credits"), () => {
            frm.call("create_credits").then(r => {frappe.msgprint(__("Draft credits: {0}", [(r.message || []).join(", ")]));});
        }, __("Actions"));
    }
});
