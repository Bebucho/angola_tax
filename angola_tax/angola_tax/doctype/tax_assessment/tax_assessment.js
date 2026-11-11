
frappe.ui.form.on("Tax Assessment", {
    setup(frm) {
        apply_progressive_rules(frm);
        calculate_legal_limit(frm);
    },

    refresh(frm) {
        apply_progressive_rules(frm);
        calculate_legal_limit(frm);
    },

    assessment_origin(frm) {
        apply_progressive_rules(frm);
    },

    assessment_type(frm) {
        apply_progressive_rules(frm);
    },

    assessment_decision(frm) {
        apply_progressive_rules(frm);
    },

    date_received(frm) {
        calculate_legal_limit(frm);
    },

    legal_time_limit_days(frm) {
        calculate_legal_limit(frm);
    },

    amount_assessed(frm) {
        calculate_undisputed_amount(frm);
    },

    disputed_amount(frm) {
        calculate_undisputed_amount(frm);
    }
});


function apply_progressive_rules(frm) {
    const origin =
        frm.doc.assessment_origin || "";

    const assessmentType =
        frm.doc.assessment_type || "";

    const decision =
        frm.doc.assessment_decision || "";

    const fromObligation =
        ["From Fiscal Obligation", "From Tax Notification"].includes(origin);
    const fromNotification = origin === "From Tax Notification";

    frm.set_df_property(
        "amount_assessed",
        "label",
        fromNotification ? __("Gross Amount Assessed (before credit)") : __("Amount Assessed")
    );
    frm.set_df_property(
        "net_payable_amount",
        "label",
        fromNotification ? __("Balance Payable") : __("Balance")
    );
    frm.set_df_property(
        "amount_assessed",
        "description",
        fromNotification
            ? __("This is the gross amount from the source Tax Notification. Tax Credit Applied is deducted to calculate Balance Payable.")
            : ""
    );
    frm.toggle_display("source_tax_notification", fromNotification);

    const operational = assessmentType === "Self-Determination";

    frm.set_df_property("assessment_origin", "read_only", 1);
    if (operational) {
        frm.set_df_property("assessment_type", "read_only", 1);
        frm.set_df_property("assessment_decision", "read_only", 1);
    }

    const successorTypes = [
        "Reissue",
        "Additional",
        "Correction"
    ];

    const successor =
        successorTypes.includes(
            assessmentType
        );

    const pending =
        decision === "Pending";

    const accepted =
        decision === "Accepted";

    const disputed =
        decision === "Disputed";

    const agreement =
        decision === "Agreement";

    // =====================================================
    // EXISTING FISCAL OBLIGATION
    // =====================================================

    /*
     * Fiscal Obligation:
     *
     * From Fiscal Obligation
     *   -> visible and mandatory before first Save.
     *
     * Direct AGT Assessment
     *   -> hidden while creating the new Assessment.
     *   -> after Save, display the automatically-created
     *      Fiscal Obligation as a normal clickable Link.
     */
    const showFiscalObligation =
        fromObligation ||
        (
            !frm.is_new() &&
            !!frm.doc.fiscal_obligation
        );

    frm.toggle_display(
        "fiscal_obligation",
        showFiscalObligation
    );

    frm.toggle_reqd(
        "fiscal_obligation",
        fromObligation
    );

    if (
        frm.fields_dict["fiscal_obligation"]
    ) {
        frm.set_df_property(
            "fiscal_obligation",
            "read_only",
            !fromObligation
        );
    }

    // =====================================================
    // ASSESSMENT LINEAGE
    // =====================================================

    frm.toggle_display(
        "previous_assessment",
        successor
    );

    frm.toggle_reqd(
        "previous_assessment",
        successor
    );

    // =====================================================
    // UNIVERSAL ASSESSMENT CONTROL DATA
    // =====================================================

    [
        "assessment_decision",
        "assessment_reference",
        "attach",
        "responsible_user",
        "date_received",
        "legal_time_limit_days"
    ].forEach(fieldname => {
        if (frm.fields_dict[fieldname]) {
            frm.toggle_reqd(
                fieldname,
                !operational
            );
        }
    });

    // Calculated field.
    if (
        frm.fields_dict[
            "legal_time_limit_date"
        ]
    ) {
        frm.set_df_property(
            "legal_time_limit_date",
            "read_only",
            1
        );
    }

    // =====================================================
    // PENDING
    // =====================================================

    frm.toggle_reqd(
        "action_due_date",
        pending
    );

    // =====================================================
    // AMOUNT ASSESSED
    //
    // Pending may be saved without it.
    // All substantive decisions require it.
    // =====================================================

    frm.toggle_reqd(
        "amount_assessed",
        accepted ||
        disputed ||
        agreement
    );

    // =====================================================
    // ACCEPTED
    // =====================================================

    frm.toggle_reqd(
        "acceptance_date",
        accepted && !operational
    );

    // =====================================================
    // DISPUTED
    // =====================================================

    frm.toggle_reqd(
        "disputed_amount",
        disputed && !operational
    );

    // =====================================================
    // AGREEMENT
    // =====================================================

    [
        "agreement_date",
        "agreement_reference",
        "agreed_amount",
        "agreement_document"
    ].forEach(fieldname => {
        if (frm.fields_dict[fieldname]) {
            frm.toggle_reqd(
                fieldname,
                agreement
            );
        }
    });

    /*
     * IMPORTANT:
     *
     * No RUPE/NL field is made mandatory here.
     * No Provision field is made mandatory here.
     * No accounting field is made mandatory here.
     *
     * Those belong to their respective process stages /
     * downstream DocTypes.
     */
}


function calculate_legal_limit(frm) {
    if (
        !frm.doc.date_received ||
        frm.doc.legal_time_limit_days === null ||
        frm.doc.legal_time_limit_days === undefined ||
        frm.doc.legal_time_limit_days === ""
    ) {
        if (
            frm.doc.legal_time_limit_date
        ) {
            frm.set_value(
                "legal_time_limit_date",
                null
            );
        }

        return;
    }

    const days = cint(
        frm.doc.legal_time_limit_days
    );

    const deadline =
        frappe.datetime.add_days(
            frm.doc.date_received,
            days
        );

    if (
        frm.doc.legal_time_limit_date !==
        deadline
    ) {
        frm.set_value(
            "legal_time_limit_date",
            deadline
        );
    }
}


function calculate_undisputed_amount(frm) {
    if (
        frm.doc.assessment_decision !==
        "Disputed"
    ) {
        return;
    }

    const assessed =
        flt(
            frm.doc.amount_assessed
        );

    const disputed =
        flt(
            frm.doc.disputed_amount
        );

    frm.set_value(
        "undisputed_amount",
        Math.max(
            assessed - disputed,
            0
        )
    );
}


// =========================================================
// CONTROLLED ASSESSMENT DECISIONS
// =========================================================

frappe.ui.form.on("Tax Assessment", {
    refresh(frm) {
        if (
            frm.is_new()
            || frm.doc.docstatus !== 0
            || frm.doc.assessment_type === "Self-Determination"
        ) {
            return;
        }

        if (
            frm.doc.assessment_decision === "Pending"
        ) {
            frm.add_custom_button(
                __("Accept"),
                () => {
                    tax_assessment_decision_dialog(
                        frm,
                        "Accepted"
                    );
                },
                __("Decision")
            );

            frm.add_custom_button(
                __("Dispute"),
                () => {
                    tax_assessment_decision_dialog(
                        frm,
                        "Disputed"
                    );
                },
                __("Decision")
            );
        } else if (
            ["Accepted", "Disputed"].includes(
                frm.doc.assessment_decision
            )
            && (
                !frm.doc.decision_date
                || !frm.doc.decision_document
                || !frm.doc.decision_notes
            )
        ) {
            const decision = frm.doc.assessment_decision;
            frm.add_custom_button(
                __("Complete Decision Evidence"),
                () => tax_assessment_decision_dialog(frm, decision),
                __("Decision")
            );
        }
    }
});


function tax_assessment_decision_dialog(
    frm,
    new_decision
) {
    if (frm.is_dirty()) {
        frappe.msgprint(
            __(
                "Save the current Assessment changes before recording the decision."
            )
        );
        return;
    }

    frappe.prompt(
        [
            {
                fieldname: "decision_date",
                fieldtype: "Date",
                label: __("Decision Date"),
                reqd: 1,
                default:
                    frm.doc.decision_date
                    || frm.doc.acceptance_date
                    || frappe.datetime.get_today()
            },
            {
                fieldname: "reference",
                fieldtype: "Data",
                label: __("Reference")
            },
            {
                fieldname: "document",
                fieldtype: "Attach",
                label: __("Document"),
                reqd: 1
            },
            {
                fieldname: "notes",
                fieldtype: "Small Text",
                label: __("Comment"),
                reqd: 1
            }
        ],
        values => {
            frappe.call({
                method:
                    "angola_tax.angola_tax.doctype." +
                    "tax_assessment.tax_assessment." +
                    "transition_assessment",

                args: {
                    assessment_name:
                        frm.doc.name,

                    new_decision:
                        new_decision,

                    decision_date:
                        values.decision_date,

                    reference:
                        values.reference,

                    document:
                        values.document,

                    notes:
                        values.notes
                },

                freeze: true,

                freeze_message:
                    __("Recording assessment decision..."),

                callback(r) {
                    if (!r.message) {
                        return;
                    }

                    frm.reload_doc().then(() => {
                        if (new_decision === "Disputed" && r.message.tax_litigation) {
                            prompt_litigation_provision(r.message.tax_litigation);
                        }
                        let message = new_decision === "Accepted"
                            ? __("Assessment accepted. Review and Submit the Assessment.")
                            : __("Assessment disputed. No Settlement was created. Record the provision decision and submit the DAP.");
                        frappe.show_alert({message, indicator: "green"}, 10);
                    });
                }
            });
        },
        __("Record ") + __(new_decision),
        __("Confirm Decision")
    );
}


// ===== FISCAL OBLIGATION SOURCE FILTER =====

frappe.ui.form.on("Tax Assessment", {
    setup(frm) {
        frm.set_query(
            "fiscal_obligation",
            () => {
                if (
                    !["From Fiscal Obligation", "From Tax Notification"]
                        .includes(frm.doc.assessment_origin)
                ) {
                    return {};
                }

                const filters = [
                    [
                        "Fiscal Obligation",
                        "docstatus",
                        "=",
                        1
                    ],
                ];

                if (frm.doc.company) {
                    filters.push([
                        "Fiscal Obligation",
                        "company",
                        "=",
                        frm.doc.company
                    ]);
                }

                return {
                    filters: filters
                };
            }
        );
    }
});


// ===== FISCAL OBLIGATION INHERITANCE =====

frappe.ui.form.on("Tax Assessment", {
    refresh(frm) {
        set_inherited_tax_fields(frm);
    },

    assessment_origin(frm) {
        set_inherited_tax_fields(frm);
    },

    fiscal_obligation(frm) {
        set_inherited_tax_fields(frm);

        if (
            !["From Fiscal Obligation", "From Tax Notification"]
                .includes(frm.doc.assessment_origin) ||
            !frm.doc.fiscal_obligation
        ) {
            return;
        }

        frappe.call({
            method:
                "angola_tax.angola_tax.doctype." +
                "tax_assessment.tax_assessment." +
                "get_fiscal_obligation_context",

            args: {
                fiscal_obligation:
                    frm.doc.fiscal_obligation
            },

            callback(r) {
                if (!r.message) {
                    return;
                }

                frm.set_value(
                    "company",
                    r.message.company
                );

                frm.set_value(
                    "tax_type",
                    r.message.tax_type
                );

                frm.set_value(
                    "fiscal_period_from",
                    r.message.fiscal_period_from
                );

                frm.set_value(
                    "fiscal_period_to",
                    r.message.fiscal_period_to
                );

                if (r.message.currency) {
                    frm.set_value(
                        "currency",
                        r.message.currency
                    );
                }
            }
        });
    }
});


function set_inherited_tax_fields(frm) {
    const inherited = ["From Fiscal Obligation", "From Tax Notification"]
        .includes(frm.doc.assessment_origin);

    [
        "company",
        "tax_type",
        "fiscal_period_from",
        "fiscal_period_to",
        "currency"
    ].forEach(fieldname => {
        if (frm.fields_dict[fieldname]) {
            frm.set_df_property(
                fieldname,
                "read_only",
                inherited ? 1 : 0
            );
        }
    });
}


// ===== ASSESSMENT TYPE / PREDECESSOR INHERITANCE =====

frappe.ui.form.on("Tax Assessment", {
    refresh(frm) {
        configure_assessment_lineage(frm);
    },

    assessment_origin(frm) {
        configure_assessment_lineage(frm);
    },

    fiscal_obligation(frm) {
        configure_assessment_lineage(frm);

        if (
            !["From Fiscal Obligation", "From Tax Notification"]
                .includes(frm.doc.assessment_origin) ||
            !frm.doc.fiscal_obligation
        ) {
            return;
        }

        load_previous_assessment(frm);
    }
});


function configure_assessment_lineage(frm) {
    const from_obligation = ["From Fiscal Obligation", "From Tax Notification"]
        .includes(frm.doc.assessment_origin);

    const direct =
        frm.doc.assessment_origin ===
        "Direct AGT Assessment";

    // Previous Assessment is always system-controlled.
    frm.set_df_property(
        "previous_assessment",
        "read_only",
        1
    );

    if (direct) {
        frm.set_value(
            "assessment_type",
            "Notification"
        );

        frm.set_df_property(
            "assessment_type",
            "read_only",
            1
        );

        if (frm.doc.previous_assessment) {
            frm.set_value(
                "previous_assessment",
                null
            );
        }

        return;
    }

    if (from_obligation) {
        frm.set_df_property(
            "assessment_type",
            "read_only",
            frm.doc.assessment_type === "Self-Determination" ? 1 : 0
        );

        return;
    }

    frm.set_df_property(
        "assessment_type",
        "read_only",
        0
    );
}


function load_previous_assessment(frm) {
    frappe.call({
        method:
            "angola_tax.angola_tax.doctype." +
            "tax_assessment.tax_assessment." +
            "get_previous_assessment",

        args: {
            fiscal_obligation:
                frm.doc.fiscal_obligation,

            current_assessment:
                frm.is_new()
                    ? null
                    : frm.doc.name
        },

        callback(r) {
            if (!r.message) {
                frm.set_value(
                    "previous_assessment",
                    null
                );

                frm.set_value(
                    "previous_assessed_amount",
                    0
                );

                return;
            }

            frm.set_value(
                "previous_assessment",
                r.message.previous_assessment
            );

            frm.set_value(
                "previous_assessed_amount",
                r.message.previous_assessed_amount
            );
        }
    });
}


// ===== CONDITIONAL PREVIOUS ASSESSMENT REQUIREMENT =====

frappe.ui.form.on("Tax Assessment", {
    refresh(frm) {
        configure_previous_assessment_requirement(frm);
    },

    assessment_type(frm) {
        configure_previous_assessment_requirement(frm);
    }
});


function configure_previous_assessment_requirement(frm) {
    const successor_types = [
        "Reissue",
        "Additional",
        "Correction"
    ];

    const required =
        successor_types.includes(
            frm.doc.assessment_type
        );

    frm.set_df_property(
        "previous_assessment",
        "reqd",
        required ? 1 : 0
    );

    frm.set_df_property(
        "previous_assessment",
        "read_only",
        1
    );
}


// =========================================================
// POST-SUBMISSION ACTIONS
// =========================================================

frappe.ui.form.on("Tax Assessment", {
    refresh(frm) {
        if (
            frm.doc.docstatus !== 1
        ) {
            return;
        }

        // -------------------------------------------------
        // VIEW -> RECOGNITION JOURNAL ENTRY
        // -------------------------------------------------

        if (
            frm.doc.recognition_journal_entry
        ) {
            frm.add_custom_button(
                __("Journal Entry"),
                () => {
                    frappe.set_route(
                        "Form",
                        "Journal Entry",
                        frm.doc.recognition_journal_entry
                    );
                },
                __("View")
            );
        }

        // -------------------------------------------------
        // CREATE -> TAX SETTLEMENT
        // -------------------------------------------------

        if (
            ["Accepted", "Agreement"].includes(
                frm.doc.assessment_decision
            )
            && get_assessment_settlement_amount(frm) > 0
        ) {
            frappe.db.get_value("Tax Settlement", {
                tax_assessment: frm.doc.name,
                docstatus: ["!=", 2]
            }, "name").then(r => {
                if (!r.message || !r.message.name) {
                    frm.add_custom_button(
                        __("Tax Settlement"),
                        () => create_assessment_tax_settlement(frm),
                        __("Create")
                    );
                }
            });
        }
    }
});


function create_assessment_tax_settlement(frm) {
    frappe.db.get_value(
        "Tax Settlement",
        {
            tax_assessment: frm.doc.name,
            docstatus: ["!=", 2]
        },
        [
            "name",
            "docstatus",
            "status"
        ]
    ).then(r => {
        const existing =
            r.message && r.message.name
                ? r.message
                : null;

        if (existing) {
            frappe.msgprint({
                title:
                    __("Tax Settlement Already Exists"),

                message:
                    __(
                        "Tax Settlement {0} is already linked to this Assessment.",
                        [existing.name]
                    ),

                indicator: "orange",

                primary_action: {
                    label:
                        __("Open Tax Settlement"),

                    action() {
                        frappe.set_route(
                            "Form",
                            "Tax Settlement",
                            existing.name
                        );
                    }
                }
            });

            return;
        }

        let basis = frm.doc.assessment_type === "Self-Determination"
            ? "Self-Determined" : "Accepted Assessment";

        if (
            frm.doc.assessment_decision
            === "Agreement"
        ) {
            basis = "Agreement";
        }

        frappe.db.get_value("Fiscal Obligation", frm.doc.fiscal_obligation,
            ["determination_basis", "net_cash_requirement"]).then(value => {
            const obligation = value.message || {};
            const cashAmount = obligation.determination_basis === "Computation"
                ? flt(obligation.net_cash_requirement) : get_assessment_settlement_amount(frm);
            if (obligation.determination_basis === "Computation" && cashAmount <= 0) {
                frappe.msgprint(__("No cash liquidation is required. Record filing evidence on the obligation."));
                return;
            }
        frappe.new_doc(
            "Tax Settlement",
            {
                fiscal_obligation:
                    frm.doc.fiscal_obligation,

                tax_assessment:
                    frm.doc.name,

                settlement_basis:
                    basis,

                company:
                    frm.doc.company,

                tax_type:
                    frm.doc.tax_type,

                expected_amount:
                    cashAmount,

                liquidated_amount:
                    cashAmount
            }
        );
        });
    });
}


function get_assessment_settlement_amount(frm) {
    if (
        frm.doc.assessment_origin === "From Tax Notification"
        && frm.doc.assessment_decision === "Accepted"
    ) {
        return flt(frm.doc.net_payable_amount || 0);
    }

    return flt(
        frm.doc.effective_assessed_amount
        || frm.doc.amount_assessed
        || 0
    );
}


// The operational assessment compares a reviewed GL determination
// with the assessed amount. Its Journal Entry is prepared on Save.
frappe.ui.form.on("Tax Assessment", {
    setup(frm) {
        frm.set_query("adjustment_counterpart_account", () => ({
            filters: {company: frm.doc.company, is_group: 0}
        }));
        frm.set_query("adjustment_cost_center", () => ({
            filters: {company: frm.doc.company, is_group: 0}
        }));
    },

    refresh(frm) {
        load_operational_determined_amount(frm);
        if (frm.doc.adjustment_journal_entry) {
            frm.add_custom_button(__("Adjustment Journal Entry"), () => {
                frappe.set_route(
                    "Form", "Journal Entry", frm.doc.adjustment_journal_entry
                );
            }, __("View"));

            if (frm.doc.docstatus === 0 &&
                frm.doc.assessment_type === "Self-Determination" &&
                Math.abs(flt(frm.doc.assessment_variance)) > 0.01) {
                frappe.db.get_value(
                    "Journal Entry", frm.doc.adjustment_journal_entry, "docstatus"
                ).then(r => {
                    if (!r.message || r.message.docstatus !== 0 ||
                        frm.doc.docstatus !== 0) return;
                    frm.add_custom_button(
                        __("Submit Adjustment Journal Entry"),
                        () => {
                            if (frm.is_dirty()) {
                                frappe.msgprint(__("Save the Assessment changes first."));
                                return;
                            }
                            frappe.confirm(
                                __("Submit the linked Adjustment Journal Entry?"),
                                () => frappe.call({
                                    method: "angola_tax.angola_tax.doctype.tax_assessment.tax_assessment.submit_assessment_adjustment",
                                    args: {assessment_name: frm.doc.name},
                                    freeze: true,
                                    callback(r) {
                                        if (r.message) frm.reload_doc();
                                    }
                                })
                            );
                        },
                        __("Actions")
                    );
                });
            }
        }
    },

    assessment_type(frm) {
        load_operational_determined_amount(frm);
    },

    fiscal_obligation(frm) {
        load_operational_determined_amount(frm);
    },

    amount_assessed(frm) {
        show_operational_variance(frm);
    }
});


function load_operational_determined_amount(frm) {
    if (frm.doc.docstatus !== 0 ||
        frm.doc.assessment_type !== "Self-Determination" ||
        !frm.doc.fiscal_obligation) {
        frm._determination_request_id = (frm._determination_request_id || 0) + 1;
        frm._determination_loading = false;
        show_operational_variance(frm);
        return;
    }

    // Keep the original Assessment comparison point after its adjustment
    // has increased or decreased the obligation's current GL balance.
    if (!frm.is_new() && flt(frm.doc.determined_amount)) {
        frm._determination_request_id = (frm._determination_request_id || 0) + 1;
        frm._determination_loading = false;
        show_operational_variance(frm);
        return;
    }

    const obligation = frm.doc.fiscal_obligation;
    const requestId = (frm._determination_request_id || 0) + 1;
    frm._determination_request_id = requestId;
    frm._determination_loading = true;
    show_operational_variance(frm);
    frappe.db.get_value(
        "Fiscal Obligation", obligation, "amount_determined"
    ).then(r => {
        if (frm._determination_request_id !== requestId ||
            frm.doc.fiscal_obligation !== obligation ||
            frm.doc.docstatus !== 0) {
            return;
        }
        const amount = flt(r.message && r.message.amount_determined);
        if (flt(frm.doc.determined_amount) !== amount) {
            return frm.set_value("determined_amount", amount);
        }
    }).then(() => {
        if (frm._determination_request_id !== requestId) return;
        frm._determination_loading = false;
        show_operational_variance(frm);
    }).catch(() => {
        if (frm._determination_request_id !== requestId) return;
        frm._determination_loading = false;
        frappe.msgprint(__("Could not load the determined amount from the Fiscal Obligation."));
    });
}


function show_operational_variance(frm) {
    const operational = frm.doc.assessment_type === "Self-Determination";
    const variance = flt(frm.doc.amount_assessed) - flt(frm.doc.determined_amount);
    const hasVariance = operational && !frm._determination_loading &&
        Math.abs(variance) > 0.01;

    if (operational && !frm._determination_loading && frm.doc.docstatus === 0 &&
        flt(frm.doc.assessment_variance) !== variance) {
        frm.set_value("assessment_variance", variance);
    }

    ["determined_amount", "adjustment_counterpart_account",
        "adjustment_cost_center", "adjustment_journal_entry"].forEach(field => {
        frm.toggle_display(field, operational && (
            field === "determined_amount" || hasVariance ||
            !!frm.doc.adjustment_journal_entry
        ));
    });
    ["assessment_reason", "adjustment_counterpart_account",
        "adjustment_cost_center"].forEach(field => {
        frm.toggle_reqd(field, hasVariance);
    });
}


function prompt_litigation_provision(litigation_name) {
    const d = new frappe.ui.Dialog({
        title: __("Tax Provision Decision"),
        fields: [
            {fieldname:"provision_decision",fieldtype:"Select",label:__("Provision Decision"),options:"No Provision\nFull Provision\nPartial Provision\nAlready Recognised\nDisclosure Only",reqd:1},
            {fieldname:"provision_amount",fieldtype:"Currency",label:__("Provision Amount"),depends_on:"eval:doc.provision_decision === 'Partial Provision'"},
            {fieldname:"notes",fieldtype:"Small Text",label:__("Notes")},
        ],
        primary_action_label: __("Record Decision"),
        primary_action(values) {
            if (values.provision_decision === "Partial Provision" && !values.provision_amount) {
                frappe.msgprint(__("Enter a positive provision amount for a partial provision."));
                return;
            }
            frappe.call({
                method:"angola_tax.angola_tax.doctype.tax_litigation.tax_litigation.record_litigation_provision_decision",
                args:{litigation_name,provision_decision:values.provision_decision,provision_amount:values.provision_amount,notes:values.notes},
                freeze:true,
                callback(r) {
                    if (r.message) {
                        d.hide();
                        frappe.set_route("Form", "Tax Litigation", r.message.tax_litigation);
                    }
                }
            });
        }
    });
    d.show();
}


