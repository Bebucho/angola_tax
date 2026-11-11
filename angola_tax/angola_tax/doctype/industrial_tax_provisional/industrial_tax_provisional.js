frappe.ui.form.on("Industrial Tax Provisional", {
    setup(frm) {
        frm.set_query("profile", () => ({filters: {company: frm.doc.company, mechanism: "Own Provisional", docstatus: 1}}));
        frm.set_query("revenue_configuration", () => ({filters: {company: frm.doc.company, docstatus: 1}}));
        frm.set_query("cost_center", () => ({filters: {company: frm.doc.company, is_group: 0}}));
    },
    refresh(frm) {
        frm.toggle_display("source_lines", frm.doc.docstatus === 1 && frm.doc.source_format !== "GL Revenue v1");
        frm.toggle_display("revenue_accounts", frm.doc.source_format === "GL Revenue v1");
        if (!frm.is_new() && frm.doc.docstatus === 0) frm.add_custom_button(__("Capture H1 Revenue by Account"), () => {
            frm.call("capture_source").then(() => frm.reload_doc());
        }, __("Actions"));
        if (frm.doc.docstatus === 1 && !frm.doc.accounting_complete) frm.add_custom_button(__("Recover Pending Provisional Accounting"), () => {
            frappe.confirm(__("Adopt the reviewed existing journal or recognise the approved provisional asset and liability?"), () => {
                frm.call("complete_accounting").then(() => frm.reload_doc());
            });
        }, __("Actions"));
        if (frm.doc.docstatus === 1 && frm.doc.accounting_complete) frm.add_custom_button(__("Create Paid Provisional Credit"), () => {
            frappe.prompt([{fieldname: "collection_reference", fieldtype: "Link", options: "Tax Collection Reference", label: __("Confirmed Collection Reference"), reqd: 1, get_query: () => ({filters: {fiscal_obligation: frm.doc.fiscal_obligation, status: "Confirmed"}})}], values => {
                frm.call("create_credit", values).then(r => {if (r.message) frappe.set_route("Form", "Industrial Tax Credit", r.message);});
            }, __("Paid Provisional Credit"));
        }, __("Actions"));
        if (!frm.is_new() && (frm.doc.revenue_accounts || []).length) frm.add_custom_button(__("View Revenue Detail"), () => {
            frappe.prompt([
                {fieldname: "account", fieldtype: "Select", label: __("Income Account"), options: frm.doc.revenue_accounts.map(r => r.account).join("\n"), reqd: 1},
                {fieldname: "source", fieldtype: "Select", label: __("Source"), options: "GL\nInvoices", default: "GL", reqd: 1},
                {fieldname: "page", fieldtype: "Int", label: __("Page (25 rows)"), default: 1, reqd: 1}
            ], values => {
                frm.call("get_revenue_detail", values).then(r => {
                    const data = r.message;
                    const keys = values.source === "GL" ? ["voucher_no", "posting_date", "debit", "credit"] : ["sales_invoice", "item_code", "base_amount", "invoice_withholding"];
                    const labels = values.source === "GL" ? ["Voucher", "Date", "Debit", "Credit"] : ["Invoice", "Item", "Revenue Base", "Withholding Amount"];
                    const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
                    const warning = data.source_changed ? "<p>" + escape(__("Source changed since capture; recapture or use controlled correction.")) + "</p>" : "";
                    const rows = data.rows.map(row => "<tr>" + keys.map(key => "<td>" + escape(row[key]) + "</td>").join("") + "</tr>").join("");
                    frappe.msgprint({title: __("Revenue Source Detail"), wide: true, message: warning + "<p>" + escape(values.account) + " — " + escape(data.page) + " / " + escape(Math.max(1, Math.ceil(data.total / 25))) + "</p><table class='table table-bordered'><thead><tr>" + labels.map(label => "<th>" + escape(__(label)) + "</th>").join("") + "</tr></thead><tbody>" + rows + "</tbody></table>"});
                });
            }, __("View Revenue Detail"));
        }, __("View"));
        if (frm.doc.fiscal_obligation) frm.add_custom_button(__("Fiscal Obligation"), () => frappe.set_route("Form", "Fiscal Obligation", frm.doc.fiscal_obligation), __("View"));
    }
});
