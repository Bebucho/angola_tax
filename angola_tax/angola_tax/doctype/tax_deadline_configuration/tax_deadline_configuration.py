import frappe
from frappe.model.document import Document
from frappe.utils import cint


TIMING_OFFSETS = {
    "Current Month": 0,
    "Next Month": 1,
    "Second Following Month": 2,
    "Third Following Month": 3,
}


class TaxDeadlineConfiguration(Document):

    def validate(self):
        self.apply_legal_rule_engine_values()
        self.apply_process_schedule_engine_values()

        self.validate_legal_deadline()
        self.validate_process_schedule()

    def apply_legal_rule_engine_values(self):
        self.legal_due_month_offset = (
            TIMING_OFFSETS.get(
                self.legal_due_timing,
                0,
            )
        )

        self.legal_use_end_of_month = (
            1
            if self.legal_due_day_rule
            == "End of Month"
            else 0
        )

        if self.legal_use_end_of_month:
            self.legal_due_day = 0

    def apply_process_schedule_engine_values(self):
        for row in self.process_schedule or []:

            row.target_month_offset = (
                TIMING_OFFSETS.get(
                    row.target_timing,
                    0,
                )
            )

            row.target_use_end_of_month = (
                1
                if row.target_day_rule
                == "End of Month"
                else 0
            )

            if row.target_use_end_of_month:
                row.target_day = 0

            if row.critical_rule == "Legal Deadline":
                row.critical_month_offset = 0
                row.critical_use_end_of_month = 0
                row.critical_day = 0
                continue

            row.critical_month_offset = (
                TIMING_OFFSETS.get(
                    row.critical_timing,
                    0,
                )
            )

            row.critical_use_end_of_month = (
                1
                if row.critical_day_rule
                == "End of Month"
                else 0
            )

            if row.critical_use_end_of_month:
                row.critical_day = 0

    def validate_legal_deadline(self):
        if self.legal_use_end_of_month:
            return

        day = cint(
            self.legal_due_day
        )

        if day < 1 or day > 31:
            frappe.throw(
                "Legal Due Day must be between 1 and 31."
            )

    def validate_process_schedule(self):
        rows = self.process_schedule or []
        required_stages = {"Determination", "Liquidation", "Payment"}
        stages = [row.process_stage for row in rows]

        if len(stages) != 3 or set(stages) != required_stages:
            frappe.throw(
                "Process Schedule requires exactly one row each for "
                "Determination, Liquidation and Payment."
            )

        seen = set()

        for row in rows:

            if row.process_stage in seen:
                frappe.throw(
                    f"Process Stage {row.process_stage} "
                    "may only appear once."
                )

            seen.add(
                row.process_stage
            )

            if not row.target_use_end_of_month:
                day = cint(
                    row.target_day
                )

                if day < 1 or day > 31:
                    frappe.throw(
                        f"{row.process_stage}: "
                        "Target Day must be between 1 and 31."
                    )

            if row.critical_rule == "Legal Deadline":
                continue

            if not row.critical_use_end_of_month:
                day = cint(
                    row.critical_day
                )

                if day < 1 or day > 31:
                    frappe.throw(
                        f"{row.process_stage}: "
                        "Critical Day must be between 1 and 31."
                    )
