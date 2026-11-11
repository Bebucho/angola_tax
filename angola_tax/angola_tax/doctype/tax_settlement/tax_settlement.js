frappe.ui.form.on("Tax Settlement", {
    setup(frm) {
        frm.set_query("cost_account", function () {
            return {
                filters: {
                    company: frm.doc.company,
                    is_group: 0
                }
            };
        });
    },

    refresh(frm) {
        set_assessment_amount_label(frm);
        frm.set_df_property(
            "liquidated_amount",
            "read_only",
            Boolean(frm.doc.tax_assessment)
        );
        default_liquidated_from_assessment(frm);
        calculate_collection_total(frm);

        if (frm.doc.docstatus === 0) {
            if (frm.doc.collection_mode === "Single RUPE") {
                ensure_single_rupe(frm);
            }
            frm.add_custom_button(
                __("Generate RUPE Lines"),
                () => generate_rupe_lines(frm),
                __("Actions")
            );
        }

        if (frm.doc.docstatus !== 1) {
            return;
        }

        if (frm.doc.payment_arrangement === "Payment Plan") {
            add_payment_plan_button(frm);
        }

        if (frm.doc.adjustment_journal_entry) {
            frm.add_custom_button(
                __("Ledger"),
                () => {
                    frappe.set_route(
                        "query-report",
                        "General Ledger",
                        {
                            company: frm.doc.company,
                            voucher_no:
                                frm.doc.adjustment_journal_entry
                        }
                    );
                },
                __("View")
            );
        }

        frm.add_custom_button(
            __("Tax Collection"),
            () => {
                frappe.set_route(
                    "List",
                    "Tax Collection Reference",
                    {
                        tax_settlement: frm.doc.name
                    }
                );
            },
            __("View")
        );

        frm.add_custom_button(
            __("Tax Payment"),
            () => create_tax_payment(frm),
            __("Create")
        );
    },

    tax_assessment(frm) {
        set_assessment_amount_label(frm);
        frm.set_df_property(
            "liquidated_amount",
            "read_only",
            Boolean(frm.doc.tax_assessment)
        );
        default_liquidated_from_assessment(frm);
    },

    fiscal_obligation(frm) {
        default_liquidated_from_assessment(frm);
    },

    liquidated_amount(frm) {
        ensure_single_rupe(frm);
    },

    collection_mode(frm) {
        generate_rupe_lines(frm);
    },

    collection_lines_add(frm) {
        calculate_collection_total(frm);
    }
});

function set_assessment_amount_label(frm) {
    if (!frm.doc.tax_assessment) {
        frm.set_df_property("expected_amount", "label", __("Determined Amount"));
        return;
    }

    const assessmentName = frm.doc.tax_assessment;
    frappe.db.get_value(
        "Tax Assessment",
        assessmentName,
        "assessment_origin"
    ).then(r => {
        if (frm.doc.tax_assessment !== assessmentName) return;
        frm.set_df_property(
            "expected_amount",
            "label",
            r.message && r.message.assessment_origin === "From Tax Notification"
                ? __("Balance Payable")
                : __("Determined Amount")
        );
    });
}

function default_liquidated_from_assessment(frm) {
    if (
        frm.doc.docstatus !== 0 ||
        !frm.doc.tax_assessment
    ) {
        return;
    }

    const assessment_name = frm.doc.tax_assessment;
    frappe.db.get_value(
        "Tax Assessment",
        assessment_name,
        [
            "docstatus",
            "fiscal_obligation",
            "effective_assessed_amount",
            "amount_assessed",
            "assessment_origin",
            "net_payable_amount",
            "tax_credit_applied",
            "assessed_penalty_amount"
        ]
    ).then(r => {
        const assessment = r.message;
        if (
            !assessment ||
            assessment.docstatus !== 1 ||
            frm.doc.tax_assessment !== assessment_name ||
            assessment.fiscal_obligation !== frm.doc.fiscal_obligation
        ) {
            return;
        }

        const amount = assessment.assessment_origin === "From Tax Notification"
            ? flt(assessment.net_payable_amount)
            : flt(
                assessment.effective_assessed_amount ||
                assessment.amount_assessed
            );
        const assessed_penalty = flt(assessment.assessed_penalty_amount);
        if (
            assessed_penalty > 0 &&
            !(frm.doc.settlement_components || []).length
        ) {
            frm.add_child("settlement_components", {
                component: "Principal",
                amount: amount - assessed_penalty
            });
            frm.add_child("settlement_components", {
                component: "Penalty",
                amount: assessed_penalty
            });
            frm.refresh_field("settlement_components");
        }
        const penalty = (frm.doc.settlement_components || [])
            .filter(row => row.component === "Penalty")
            .reduce((sum, row) => sum + flt(row.amount), 0);
        const final_amount = amount + (
            (frm.doc.settlement_components || []).length
                ? penalty - assessed_penalty : 0
        );
        if (
            amount > 0 &&
            (
                Math.abs(flt(frm.doc.expected_amount) - amount) > 0.001 ||
                Math.abs(flt(frm.doc.liquidated_amount) - final_amount) > 0.001
            )
        ) {
            frm.set_value({
                expected_amount: amount,
                liquidated_amount: final_amount
            }).then(() => ensure_single_rupe(frm));
        }
    });
}


frappe.ui.form.on("Tax Settlement Collection", {
    rupe_amount(frm) {
        calculate_collection_total(frm);
    },

    collection_lines_remove(frm) {
        calculate_collection_total(frm);
    }
});


frappe.ui.form.on("Tax Settlement Component", {
    component(frm) {
        default_liquidated_from_assessment(frm);
    },
    amount(frm) {
        default_liquidated_from_assessment(frm);
    },
    settlement_components_remove(frm) {
        default_liquidated_from_assessment(frm);
    }
});


function ensure_single_rupe(frm) {
    if (
        frm.doc.docstatus !== 0 ||
        frm.doc.collection_mode !== "Single RUPE"
    ) {
        calculate_collection_total(frm);
        return;
    }

    const rows = frm.doc.collection_lines || [];

    if (rows.length === 0) {
        frm.add_child("collection_lines");
    }

    const current_rows =
        frm.doc.collection_lines || [];

    if (current_rows.length === 1) {
        frappe.model.set_value(
            current_rows[0].doctype,
            current_rows[0].name,
            "rupe_amount",
            flt(frm.doc.liquidated_amount)
        );
    }

    frm.refresh_field("collection_lines");
    calculate_collection_total(frm);
}


function generate_rupe_lines(frm) {
    if (frm.doc.docstatus !== 0) return;

    const amount = flt(frm.doc.liquidated_amount);
    if (amount <= 0) {
        frappe.msgprint(__("Set the Liquidated Amount first."));
        return;
    }

    const generate = count => {
        const precision = Math.max(0, Math.min(
            cint(frappe.boot.sysdefaults.currency_precision || 2), 6
        ));
        const unit = 10 ** precision;
        const total_units = Math.round(amount * unit);
        if (count > total_units) {
            frappe.msgprint(
                __("Each RUPE must have a positive amount.")
            );
            return;
        }

        const replace = () => {
            frm.clear_table("collection_lines");
            const base = Math.floor(total_units / count);
            for (let i = 0; i < count; i++) {
                const units = i === count - 1
                    ? total_units - base * (count - 1) : base;
                frm.add_child("collection_lines", {
                    rupe_amount: units / unit
                });
            }
            frm.refresh_field("collection_lines");
            calculate_collection_total(frm);
        };

        const rows = frm.doc.collection_lines || [];
        const has_data = rows.some(row =>
            row.rupe || row.nota_liquidacao || row.issue_date ||
            row.payment_due_date || row.collection_reference ||
            flt(row.rupe_amount) > 0
        );
        if (has_data || rows.length > 1) {
            frappe.confirm(
                __("Replace the existing RUPE lines and their details?"),
                replace
            );
        } else {
            replace();
        }
    };

    if (frm.doc.collection_mode === "Single RUPE") {
        generate(1);
        return;
    }

    frappe.prompt(
        [{
            fieldname: "count",
            label: __("Number of RUPEs"),
            fieldtype: "Int",
            reqd: 1,
            default: Math.max(2, (frm.doc.collection_lines || []).length)
        }],
        values => {
            const count = Number(values.count);
            if (!Number.isInteger(count) || count < 2) {
                frappe.msgprint(__("Enter at least two RUPEs."));
                return;
            }
            generate(count);
        },
        __("Generate RUPE Lines"),
        __("Generate")
    );
}


function add_payment_plan_button(frm) {
    frappe.db.get_value(
        "Tax Payment Plan",
        {tax_settlement: frm.doc.name, docstatus: ["!=", 2]},
        "name"
    ).then(r => {
        if (r.message && r.message.name) {
            frm.add_custom_button(
                __("Tax Payment Plan"),
                () => frappe.set_route(
                    "Form", "Tax Payment Plan", r.message.name
                ),
                __("View")
            );
        } else {
            frm.add_custom_button(
                __("Tax Payment Plan"),
                () => create_derived_payment_plan(frm),
                __("Create")
            );
        }
    });
}


function create_derived_payment_plan(frm) {
    const lines = frm.doc.collection_lines || [];
    frappe.model.with_doctype("Tax Payment Plan", () => {
        const plan = frappe.model.get_new_doc("Tax Payment Plan");
        plan.fiscal_obligation = frm.doc.fiscal_obligation;
        plan.tax_settlement = frm.doc.name;
        plan.original_debt_amount = frm.doc.liquidated_amount;
        plan.original_rupe = lines.length ? lines[0].rupe : null;
        plan.original_collection_document = lines.length
            ? lines[0].nota_liquidacao : null;
        plan.plan_start_date = lines.map(
            row => row.issue_date || row.payment_due_date
        ).sort()[0];
        plan.plan_end_date = lines.map(
            row => row.payment_due_date
        ).sort().slice(-1)[0];
        lines.forEach((source, index) => {
            const row = frappe.model.add_child(
                plan, "Tax Payment Plan Instalment", "instalments"
            );
            row.instalment_no = index + 1;
            row.nota_liquidacao = source.nota_liquidacao;
            row.rupe = source.rupe;
            row.due_date = source.payment_due_date;
            row.amount = source.rupe_amount;
        });
        frappe.set_route("Form", "Tax Payment Plan", plan.name);
    });
}


function calculate_collection_total(frm) {
    const total = (
        frm.doc.collection_lines || []
    ).reduce(
        (sum, row) =>
            sum + flt(row.rupe_amount),
        0
    );

    frm.set_value(
        "collection_total",
        total
    );

    frm.set_value(
        "collection_variance",
        total - flt(frm.doc.liquidated_amount)
    );
}


function create_tax_payment(frm) {
    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "Tax Collection Reference",
            filters: {
                tax_settlement: frm.doc.name,
                status: [
                    "in",
                    ["Issued", "Expired"]
                ]
            },
            fields: [
                "name",
                "rupe",
                "amount",
                "due_date",
                "tax_payment"
            ],
            order_by: "due_date asc",
            limit_page_length: 100
        },
        callback(r) {
            const refs = (
                r.message || []
            ).filter(
                row => !row.tax_payment
            );

            if (!refs.length) {
                frappe.msgprint(
                    __(
                        "No outstanding RUPE is available for payment."
                    )
                );
                return;
            }

            if (refs.length === 1) {
                open_tax_payment(
                    refs[0].name
                );
                return;
            }

            const options = refs.map(
                row => row.name
            );

            const dialog =
                new frappe.ui.Dialog({
                    title:
                        __("Select RUPE to Pay"),
                    fields: [
                        {
                            fieldname:
                                "collection_reference",
                            label:
                                __("Tax Collection Reference"),
                            fieldtype:
                                "Select",
                            options:
                                options.join("\n"),
                            reqd: 1
                        }
                    ],
                    primary_action_label:
                        __("Create Tax Payment"),
                    primary_action(values) {
                        dialog.hide();

                        open_tax_payment(
                            values.collection_reference
                        );
                    }
                });

            dialog.show();
        }
    });
}


function open_tax_payment(
    collection_reference
) {
    frappe.new_doc(
        "Tax Payment",
        {
            collection_reference:
                collection_reference
        }
    );
}


// =========================================================
// ACCOUNTING VIEW BUTTONS
// =========================================================

frappe.ui.form.on("Tax Settlement", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1) {
            return;
        }

        // Main Settlement accounting entry.
        if (frm.doc.settlement_journal_entry) {
            frm.add_custom_button(
                __("Journal Entry"),
                () => {
                    frappe.set_route(
                        "Form",
                        "Journal Entry",
                        frm.doc.settlement_journal_entry
                    );
                },
                __("View")
            );
        }

        // Variance accounting entry, when applicable.
        if (frm.doc.adjustment_journal_entry) {
            frm.add_custom_button(
                __("Adjustment Journal Entry"),
                () => {
                    frappe.set_route(
                        "Form",
                        "Journal Entry",
                        frm.doc.adjustment_journal_entry
                    );
                },
                __("View")
            );
        }
    }
});


// =========================================================
// ACCOUNTING VIEW BUTTONS
// =========================================================

frappe.ui.form.on("Tax Settlement", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1) {
            return;
        }

        // Main Settlement accounting entry.
        if (frm.doc.settlement_journal_entry) {
            frm.add_custom_button(
                __("Journal Entry"),
                () => {
                    frappe.set_route(
                        "Form",
                        "Journal Entry",
                        frm.doc.settlement_journal_entry
                    );
                },
                __("View")
            );
        }

        // Variance accounting entry, when applicable.
        if (frm.doc.adjustment_journal_entry) {
            frm.add_custom_button(
                __("Adjustment Journal Entry"),
                () => {
                    frappe.set_route(
                        "Form",
                        "Journal Entry",
                        frm.doc.adjustment_journal_entry
                    );
                },
                __("View")
            );
        }
    }
});
