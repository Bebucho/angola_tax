import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now


TOLERANCE = 0.01


class VATReconciliation(Document):

    def before_insert(self):
        self.load_from_period(
            copy_reconciling_items=True
        )

        if not self.prepared_by:
            self.prepared_by = frappe.session.user

        if not self.prepared_on:
            self.prepared_on = now()


    def validate(self):
        self.validate_unique_period()
        self.load_from_period(
            copy_reconciling_items=False
        )
        self.calculate_reconciliation()
        self.set_status()


    def validate_unique_period(self):
        if not self.vat_period_control:
            return

        existing = frappe.db.get_value(
            "VAT Reconciliation",
            {
                "vat_period_control":
                    self.vat_period_control,
                "docstatus":
                    ["!=", 2],
                "name":
                    ["!=", self.name or ""],
            },
            "name",
        )

        if existing:
            frappe.throw(
                _(
                    "VAT Period {0} already has "
                    "VAT Reconciliation {1}."
                ).format(
                    self.vat_period_control,
                    existing,
                )
            )


    def load_from_period(
        self,
        copy_reconciling_items=False,
    ):
        if not self.vat_period_control:
            return

        period = frappe.get_doc(
            "VAT Period Control",
            self.vat_period_control,
        )

        self.company = period.company
        self.period_start = period.period_start
        self.period_end = period.period_end

        self.modelo_7_confirmed = (
            period.modelo_7_confirmed
        )

        self.accounting_state_amount = (
            period.accounting_state_amount
        )
        self.accounting_taxpayer_amount = (
            period.accounting_taxpayer_amount
        )
        self.accounting_net_position = (
            period.accounting_net_position
        )

        self.declared_state_amount = (
            period.declared_state_amount
        )
        self.declared_taxpayer_amount = (
            period.declared_taxpayer_amount
        )
        self.previous_credit = (
            period.previous_credit
        )
        self.declared_payable = (
            period.declared_payable
        )
        self.declared_credit = (
            period.declared_credit
        )
        self.declaration_net_position = (
            period.declaration_net_position
        )

        self.declaration_reference = (
            period.declaration_reference
        )
        self.submission_date = (
            period.submission_date
        )
        self.declaration_document = (
            period.declaration_document
        )

        self.closing_journal_entry = (
            period.closing_journal_entry
        )
        self.fiscal_obligation = (
            period.fiscal_obligation
        )

        if copy_reconciling_items:
            self.set(
                "reconciling_items",
                [],
            )

            for source in (
                period.reconciling_items or []
            ):
                self.append(
                    "reconciling_items",
                    self.copy_reconciling_item(
                        source
                    ),
                )


    def copy_reconciling_item(
        self,
        source,
    ):
        child_meta = frappe.get_meta(
            "VAT Reconciling Item"
        )

        values = {}

        ignored = {
            "name",
            "owner",
            "creation",
            "modified",
            "modified_by",
            "parent",
            "parentfield",
            "parenttype",
            "idx",
            "docstatus",
        }

        for field in child_meta.fields:
            fieldname = field.fieldname

            if (
                not fieldname
                or fieldname in ignored
            ):
                continue

            if source.get(fieldname) is not None:
                values[fieldname] = (
                    source.get(fieldname)
                )

        return values


    def calculate_reconciliation(self):
        reconciliation_total = 0

        for row in (
            self.reconciling_items or []
        ):
            row.difference = flt(
                flt(row.declared_amount)
                - flt(row.accounting_amount),
                2,
            )

            corrected_posted = bool(
                row.resolution_status == "Corrected"
                and row.get("journal_entry")
                and frappe.db.get_value(
                    "Journal Entry",
                    row.get("journal_entry"),
                    "docstatus",
                ) == 1
            )

            if not corrected_posted:
                reconciliation_total += flt(
                    row.difference
                )

        self.reconciling_total = flt(
            reconciliation_total,
            2,
        )

        self.unexplained_difference = flt(
            flt(
                self.declaration_net_position
            )
            - flt(
                self.accounting_net_position
            )
            - flt(
                self.reconciling_total
            ),
            2,
        )

        self.is_reconciled = int(
            bool(self.modelo_7_confirmed)
            and abs(
                flt(
                    self.unexplained_difference
                )
            ) < TOLERANCE
        )


    def set_status(self):
        if self.docstatus == 2:
            self.status = "Cancelled"
            return

        if self.docstatus == 1:
            self.status = "Submitted"
            return

        if self.is_reconciled:
            self.status = "Reconciled"
        elif self.vat_period_control:
            self.status = "In Review"
        else:
            self.status = "Draft"


    def validate_reconciling_items(self):
        for row in (
            self.reconciling_items or []
        ):
            if (
                row.get(
                    "requires_gl_adjustment"
                )
                and not row.account
            ):
                frappe.throw(
                    _(
                        "Row #{0}: Account is required "
                        "when GL adjustment is required."
                    ).format(
                        row.idx
                    )
                )

            if (
                row.resolution_status
                == "Corrected"
                and row.get(
                    "requires_gl_adjustment"
                )
            ):
                if not row.get(
                    "journal_entry"
                ):
                    frappe.throw(
                        _(
                            "Row #{0}: Corrected GL "
                            "adjustment requires a "
                            "Journal Entry."
                        ).format(
                            row.idx
                        )
                    )

                if (
                    frappe.db.get_value(
                        "Journal Entry",
                        row.get(
                            "journal_entry"
                        ),
                        "docstatus",
                    )
                    != 1
                ):
                    frappe.throw(
                        _(
                            "Row #{0}: linked Journal "
                            "Entry must be submitted."
                        ).format(
                            row.idx
                        )
                    )


    def sync_to_period(self):
        period = frappe.get_doc(
            "VAT Period Control",
            self.vat_period_control,
        )

        if period.docstatus != 0:
            frappe.throw(
                _(
                    "VAT Period must remain Draft "
                    "while reconciliation is being prepared."
                )
            )

        period.set(
            "reconciling_items",
            [],
        )

        for source in (
            self.reconciling_items or []
        ):
            period.append(
                "reconciling_items",
                self.copy_reconciling_item(
                    source
                ),
            )

        period.calculate_modelo_7_lines()

        if hasattr(
            period,
            "set_declared_summary_from_modelo_7",
        ):
            period.set_declared_summary_from_modelo_7()

        period.calculate_reconciliation()
        period.save(
            ignore_permissions=True
        )

        return period


    def refresh_from_period_after_sync(
        self,
        period,
    ):
        self.accounting_state_amount = (
            period.accounting_state_amount
        )
        self.accounting_taxpayer_amount = (
            period.accounting_taxpayer_amount
        )
        self.accounting_net_position = (
            period.accounting_net_position
        )

        self.declared_state_amount = (
            period.declared_state_amount
        )
        self.declared_taxpayer_amount = (
            period.declared_taxpayer_amount
        )
        self.previous_credit = (
            period.previous_credit
        )
        self.declared_payable = (
            period.declared_payable
        )
        self.declared_credit = (
            period.declared_credit
        )
        self.declaration_net_position = (
            period.declaration_net_position
        )

        self.reconciling_total = (
            period.reconciling_total
        )
        self.unexplained_difference = (
            period.unexplained_difference
        )
        self.is_reconciled = (
            period.is_reconciled
        )

        self.set(
            "reconciling_items",
            [],
        )

        for source in (
            period.reconciling_items or []
        ):
            self.append(
                "reconciling_items",
                self.copy_reconciling_item(
                    source
                ),
            )


    def before_submit(self):
        self.validate_reconciling_items()

        period = self.sync_to_period()

        self.refresh_from_period_after_sync(
            period
        )

        if not period.modelo_7_confirmed:
            frappe.throw(
                _(
                    "Modelo 7 must be confirmed "
                    "before reconciliation submission."
                )
            )

        if not period.declaration_reference:
            frappe.throw(
                _(
                    "Declaration Reference is required "
                    "before reconciliation submission."
                )
            )

        if not period.submission_date:
            frappe.throw(
                _(
                    "Submission Date is required "
                    "before reconciliation submission."
                )
            )

        open_items = [
            row
            for row in (
                period.reconciling_items or []
            )
            if row.resolution_status == "Open"
        ]

        if open_items:
            frappe.throw(
                _(
                    "{0} reconciling item(s) remain Open."
                ).format(
                    len(open_items)
                )
            )

        if not period.is_reconciled:
            frappe.throw(
                _(
                    "VAT reconciliation is not complete. "
                    "Unexplained difference: {0}"
                ).format(
                    frappe.format_value(
                        period.unexplained_difference,
                        {
                            "fieldtype":
                                "Currency"
                        },
                    )
                )
            )

        self.is_reconciled = 1
        self.reviewed_by = (
            frappe.session.user
        )
        self.reviewed_on = now()
        self.status = "Submitted"


    def on_submit(self):
        frappe.db.set_value(
            "VAT Period Control",
            self.vat_period_control,
            "vat_reconciliation",
            self.name,
            update_modified=False,
        )


    def before_cancel(self):
        period = frappe.get_doc(
            "VAT Period Control",
            self.vat_period_control,
        )

        if (
            period.closing_journal_entry
            or period.fiscal_obligation
        ):
            frappe.throw(
                _(
                    "Cannot cancel VAT Reconciliation "
                    "after VAT closing or Fiscal Obligation "
                    "creation."
                )
            )


    def on_cancel(self):
        current = frappe.db.get_value(
            "VAT Period Control",
            self.vat_period_control,
            "vat_reconciliation",
        )

        if current == self.name:
            frappe.db.set_value(
                "VAT Period Control",
                self.vat_period_control,
                "vat_reconciliation",
                None,
                update_modified=False,
            )


    @frappe.whitelist()
    def apply_to_vat_period(self):
        if self.docstatus != 0:
            frappe.throw(
                _(
                    "Only Draft VAT Reconciliation "
                    "can be applied."
                )
            )

        self.validate_reconciling_items()

        period = self.sync_to_period()

        self.refresh_from_period_after_sync(
            period
        )

        self.save(
            ignore_permissions=True
        )

        return {
            "vat_period_control":
                period.name,
            "is_reconciled":
                period.is_reconciled,
            "unexplained_difference":
                period.unexplained_difference,
        }


    @frappe.whitelist()
    def create_adjustment(self):
        if self.docstatus != 0:
            frappe.throw(
                _(
                    "Reconciliation adjustments "
                    "must be created while Draft."
                )
            )

        candidates = [
            row
            for row in (
                self.reconciling_items or []
            )
            if (
                row.get(
                    "requires_gl_adjustment"
                )
                and row.resolution_status
                    != "Corrected"
            )
        ]

        if len(candidates) != 1:
            frappe.throw(
                _(
                    "Exactly one unresolved "
                    "GL-adjustment item must exist. "
                    "Resolve adjustments one at a time."
                )
            )

        period = self.sync_to_period()

        journal_entry = (
            period.create_reconciliation_adjustment()
        )

        period.reload()

        self.refresh_from_period_after_sync(
            period
        )

        self.save(
            ignore_permissions=True
        )

        return journal_entry


@frappe.whitelist()
def create_from_vat_period(
    vat_period_control,
):
    existing = frappe.db.get_value(
        "VAT Reconciliation",
        {
            "vat_period_control":
                vat_period_control,
            "docstatus":
                ["!=", 2],
        },
        "name",
    )

    if existing:
        return existing

    period = frappe.get_doc(
        "VAT Period Control",
        vat_period_control,
    )

    if period.closing_journal_entry:
        frappe.throw(
            _(
                "VAT Period is already closed."
            )
        )

    doc = frappe.get_doc(
        {
            "doctype":
                "VAT Reconciliation",
            "vat_period_control":
                period.name,
        }
    )

    doc.insert(
        ignore_permissions=True
    )

    return doc.name
