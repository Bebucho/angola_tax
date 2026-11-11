import frappe
from frappe.model.document import Document
from frappe.utils import flt


class TaxCollectionReference(Document):

    def validate(self):
        self.validate_rupe()
        self.load_settlement_context()
        self.validate_source()
        self.validate_amount()
        self.protect_payment_state()

    # ======================================================
    # RUPE
    # ======================================================

    def validate_rupe(self):
        if not self.rupe:
            frappe.throw("RUPE is required.")

        # Keep as text: leading zeroes, if any, must never be lost.
        self.rupe = str(self.rupe).strip()

        if not self.rupe:
            frappe.throw("RUPE is required.")

    # ======================================================
    # SETTLEMENT / FISCAL OBLIGATION
    # ======================================================

    def load_settlement_context(self):
        if not self.tax_settlement:
            frappe.throw(
                "Tax Settlement is required."
            )

        settlement = frappe.get_doc(
            "Tax Settlement",
            self.tax_settlement,
        )

        if settlement.docstatus != 1:
            frappe.throw(
                "Tax Collection Reference requires a "
                "submitted Tax Settlement."
            )

        if not settlement.fiscal_obligation:
            frappe.throw(
                "Tax Settlement does not have a Fiscal "
                "Obligation."
            )

        self.fiscal_obligation = (
            settlement.fiscal_obligation
        )

        obligation = frappe.get_doc(
            "Fiscal Obligation",
            self.fiscal_obligation,
        )

        if obligation.docstatus != 1:
            frappe.throw(
                "Fiscal Obligation must be submitted."
            )

    # ======================================================
    # SOURCE
    # ======================================================

    def validate_source(self):
        if self.source_type == "Tax Settlement":
            self.payment_plan = None
            self.instalment_no = None

            self.validate_settlement_collection()

        elif (
            self.source_type
            == "Payment Plan Instalment"
        ):
            self.validate_plan_collection()

        else:
            frappe.throw(
                "Collection Source must be Tax Settlement "
                "or Payment Plan Instalment."
            )

    def validate_settlement_collection(self):
        settlement = frappe.get_doc(
            "Tax Settlement",
            self.tax_settlement,
        )

        if settlement.status == "Cancelled":
            frappe.throw(
                "Cannot create a Collection Reference for "
                "a cancelled Tax Settlement."
            )

        # A direct Settlement RUPE cannot coexist with an
        # operative payment plan for the same collectible
        # debt. The debt has been restructured.
        operative_plan = frappe.get_all(
            "Tax Payment Plan",
            filters={
                "tax_settlement": self.tax_settlement,
                "docstatus": 1,
                "status": [
                    "in",
                    [
                        "Active",
                        "In Arrears",
                        "Awaiting Reissue",
                    ],
                ],
            },
            pluck="name",
            limit=1,
        )

        if operative_plan:
            frappe.throw(
                "This Tax Settlement is under Payment Plan "
                f"{operative_plan[0]}. Create the RUPE "
                "against the relevant Plan Instalment."
            )

    def validate_plan_collection(self):
        if not self.payment_plan:
            frappe.throw(
                "Tax Payment Plan is required for a "
                "Payment Plan Instalment Collection "
                "Reference."
            )

        if not self.instalment_no:
            frappe.throw(
                "Instalment No. is required for a Payment "
                "Plan Instalment Collection Reference."
            )

        plan = frappe.get_doc(
            "Tax Payment Plan",
            self.payment_plan,
        )

        if plan.docstatus != 1:
            frappe.throw(
                "Tax Payment Plan must be submitted."
            )

        if plan.tax_settlement != self.tax_settlement:
            frappe.throw(
                "Tax Payment Plan and Tax Collection "
                "Reference must belong to the same Tax "
                "Settlement."
            )

        if plan.fiscal_obligation != self.fiscal_obligation:
            frappe.throw(
                "Tax Payment Plan and Tax Collection "
                "Reference must belong to the same Fiscal "
                "Obligation."
            )

        if plan.status not in (
            "Active",
            "In Arrears",
        ):
            frappe.throw(
                f"Payment Plan status {plan.status} does "
                "not permit new payable Collection "
                "References."
            )

        instalment = self.get_plan_instalment(plan)

        if not instalment:
            frappe.throw(
                f"Instalment {self.instalment_no} does not "
                f"exist in Payment Plan {plan.name}."
            )

        if instalment.payment_status == "Paid":
            frappe.throw(
                f"Instalment {self.instalment_no} is "
                "already paid."
            )

    def get_plan_instalment(self, plan):
        for row in plan.instalments:
            if (
                int(row.instalment_no or 0)
                == int(self.instalment_no or 0)
            ):
                return row

        return None

    # ======================================================
    # AMOUNT
    # ======================================================

    def validate_amount(self):
        amount = flt(self.amount)

        if amount <= 0:
            frappe.throw(
                "RUPE Amount must be greater than zero."
            )

        if (
            self.source_type
            == "Payment Plan Instalment"
        ):
            plan = frappe.get_doc(
                "Tax Payment Plan",
                self.payment_plan,
            )

            instalment = self.get_plan_instalment(plan)

            if not instalment:
                return

            # Multiple RUPEs may exist for one instalment,
            # but their total cannot exceed that instalment's
            # amount unless AGT formally replaces/reissues it.
            other_refs = frappe.get_all(
                "Tax Collection Reference",
                filters={
                    "payment_plan": self.payment_plan,
                    "instalment_no": self.instalment_no,
                    "name": ["!=", self.name or ""],
                    "status": [
                        "not in",
                        [
                            "Cancelled",
                            "Superseded",
                        ],
                    ],
                },
                fields=["amount"],
            )

            total_refs = amount + sum(
                flt(row.amount)
                for row in other_refs
            )

            if (
                total_refs
                - flt(instalment.amount)
                > 0.01
            ):
                frappe.throw(
                    "Active RUPE amounts for Instalment "
                    f"{self.instalment_no} cannot exceed "
                    f"the instalment amount of "
                    f"{flt(instalment.amount)}."
                )

        elif self.source_type == "Tax Settlement":
            settlement = frappe.get_doc(
                "Tax Settlement",
                self.tax_settlement,
            )

            other_refs = frappe.get_all(
                "Tax Collection Reference",
                filters={
                    "tax_settlement":
                        self.tax_settlement,
                    "source_type":
                        "Tax Settlement",
                    "name": ["!=", self.name or ""],
                    "status": [
                        "not in",
                        [
                            "Cancelled",
                            "Superseded",
                        ],
                    ],
                },
                fields=["amount"],
            )

            total_refs = amount + sum(
                flt(row.amount)
                for row in other_refs
            )

            if (
                total_refs
                - flt(settlement.liquidated_amount)
                > 0.01
            ):
                frappe.throw(
                    "Active RUPE amounts cannot exceed the "
                    "Tax Settlement Liquidated Amount of "
                    f"{flt(settlement.liquidated_amount)}."
                )

    # ======================================================
    # PAYMENT PROTECTION
    # ======================================================

    def protect_payment_state(self):
        if self.is_new():
            return

        old = self.get_doc_before_save()

        if not old:
            return

        if not old.tax_payment:
            return

        protected_fields = (
            "tax_settlement",
            "source_type",
            "payment_plan",
            "instalment_no",
            "nota_liquidacao",
            "rupe",
            "amount",
            "due_date",
        )

        changed = [
            fieldname
            for fieldname in protected_fields
            if self.get(fieldname)
            != old.get(fieldname)
        ]

        if changed:
            frappe.throw(
                "A Collection Reference already linked to "
                "a Tax Payment cannot have its collection "
                "identity or amount changed."
            )
