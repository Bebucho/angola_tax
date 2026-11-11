import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from angola_tax.payment_control import refresh_payment_control


def refresh_obligation_payment_status(fiscal_obligation):
    """Publish the confirmed balance and its deadline status together."""
    refresh_payment_control(fiscal_obligation)
    obligation = frappe.get_doc("Fiscal Obligation", fiscal_obligation)
    obligation.calculate_submission_status()
    obligation.calculate_filing_status()
    obligation.calculate_deadline_status()
    obligation.calculate_compliance_status()
    obligation.db_set(
        {
            "submission_status": obligation.submission_status,
            "is_submission_overdue": obligation.is_submission_overdue,
            "days_submission_overdue": obligation.days_submission_overdue,
            "filing_status": obligation.filing_status,
            "is_filing_overdue": obligation.is_filing_overdue,
            "days_filing_overdue": obligation.days_filing_overdue,
            "deadline_status": obligation.deadline_status,
            "effective_payment_due_date": obligation.effective_payment_due_date,
            "is_overdue": obligation.is_overdue,
            "days_overdue": obligation.days_overdue,
            "fully_paid_on": obligation.fully_paid_on,
            "paid_late": obligation.paid_late,
            "days_paid_late": obligation.days_paid_late,
            "compliance_status": obligation.compliance_status,
            "compliance_alert": obligation.compliance_alert,
            "compliance_message": obligation.compliance_message,
        },
        update_modified=False,
    )


def resolve_originating_gl_entry(origin, company):
    """Resolve a replaced GL ID using the originating accounting fact."""
    if origin.gl_entry:
        stored = frappe.db.get_value(
            "GL Entry",
            origin.gl_entry,
            [
                "company", "voucher_type", "voucher_no", "account",
                "debit", "credit", "is_cancelled",
            ],
            as_dict=True,
        )
        if stored:
            if (
                stored.company != company
                or stored.voucher_type != origin.voucher_type
                or stored.voucher_no != origin.voucher_no
                or stored.account != origin.account
                or abs(flt(stored.debit) - flt(origin.debit)) >= 0.01
                or abs(flt(stored.credit) - flt(origin.credit)) >= 0.01
                or stored.is_cancelled
            ):
                frappe.throw(
                    f"Originating GL Entry {origin.gl_entry} does not "
                    "match the active determination movement."
                )
            return origin.gl_entry

    rows = frappe.get_all(
        "GL Entry",
        filters={
            "company": company,
            "voucher_type": origin.voucher_type,
            "voucher_no": origin.voucher_no,
            "account": origin.account,
            "is_cancelled": 0,
        },
        fields=["name", "debit", "credit"],
        limit_page_length=0,
    )
    matches = [
        row for row in rows
        if abs(flt(row.debit) - flt(origin.debit)) < 0.01
        and abs(flt(row.credit) - flt(origin.credit)) < 0.01
    ]
    if len(matches) != 1:
        frappe.throw(
            f"Cannot uniquely resolve the originating GL Entry for "
            f"{origin.voucher_type} {origin.voucher_no}. "
            "Review the Fiscal Obligation movement."
        )

    current = matches[0].name
    if frappe.db.exists(
        "Fiscal Obligation Movement",
        {"gl_entry": current, "name": ["!=", origin.name]},
    ):
        frappe.throw(
            f"GL Entry {current} is already linked to another "
            "Fiscal Obligation movement."
        )
    frappe.db.set_value(
        "Fiscal Obligation Movement", origin.name,
        "gl_entry", current, update_modified=False,
    )
    return current


class TaxPayment(Document):

    # ======================================================
    # LIFECYCLE
    # ======================================================

    def _validate_links(self):
        # A saved draft may still contain an old originating GL ID.
        # Refresh it before Frappe checks Link fields.
        if self.collection_reference:
            self.load_collection_reference()
        super()._validate_links()

    def validate(self):
        self.load_collection_reference()
        self.validate_collection_reference()
        self.validate_accounts()
        self.validate_dc_pago()

        if self.payment_mode == "Link Existing Voucher":
            self.validate_existing_voucher()

    def before_submit(self):
        self.load_collection_reference()
        self.validate_collection_reference()
        self.validate_accounts()
        self.validate_dc_pago()

        if self.payment_mode == "Create Journal Entry":
            self.ensure_journal_entry()

        elif self.payment_mode == "Link Existing Voucher":
            self.validate_existing_voucher()

        else:
            frappe.throw("Unsupported Payment Mode.")

        self.find_tax_gl_entry()

    def on_submit(self):
        # Submission means the company has recorded/executed
        # the payment. It does NOT yet mean AGT confirmation.
        self.db_set(
            "status",
            "Awaiting DC Pago",
            update_modified=False,
        )

        frappe.db.set_value(
            "Tax Collection Reference",
            self.collection_reference,
            {
                "status": "Awaiting DC Pago",
                "tax_payment": self.name,
                "payment_date": self.payment_date,
            },
            update_modified=True,
        )

        if self.dc_pago:
            self.db_set(
                {
                    "status": "Confirmed",
                    "confirmed_by": frappe.session.user,
                    "confirmed_at": now_datetime(),
                },
                update_modified=False,
            )

            frappe.db.set_value(
                "Tax Collection Reference",
                self.collection_reference,
                {
                    "status": "Confirmed",
                    "tax_payment": self.name,
                    "payment_date": self.payment_date,
                },
                update_modified=True,
            )

            refresh_obligation_payment_status(
                self.fiscal_obligation
            )

        else:
            frappe.msgprint(
                "Por favor insira o DC Pago assim que disponível.",
                title="DC Pago Pendente",
                indicator="orange",
            )

    def before_update_after_submit(self):
        """
        DC Pago is a later statutory confirmation event.

        The Tax Payment remains accounting-locked after Submit,
        but DC Pago evidence may be added afterwards.
        """

        if self.status == "Confirmed":
            return

        if not self.dc_pago:
            return

        if self.status != "Awaiting DC Pago":
            frappe.throw(
                "DC Pago can only confirm a Tax Payment "
                "that is Awaiting DC Pago."
            )

        collection = frappe.get_doc(
            "Tax Collection Reference",
            self.collection_reference,
        )

        if collection.tax_payment != self.name:
            frappe.throw(
                "Collection Reference is not linked to "
                "this Tax Payment."
            )

        if collection.status != "Awaiting DC Pago":
            frappe.throw(
                "Collection Reference must be Awaiting "
                "DC Pago before confirmation."
            )

        self.confirmed_by = frappe.session.user
        self.confirmed_at = now_datetime()
        self.status = "Confirmed"

    def on_update_after_submit(self):
        """
        Once this submitted Tax Payment has itself been saved
        as Confirmed, propagate confirmation to the collection
        control layer and Fiscal Obligation roll-up.
        """

        if self.status != "Confirmed":
            return

        if not self.dc_pago:
            return

        collection = frappe.get_doc(
            "Tax Collection Reference",
            self.collection_reference,
        )

        if collection.status != "Confirmed":
            frappe.db.set_value(
                "Tax Collection Reference",
                collection.name,
                {
                    "status": "Confirmed",
                    "tax_payment": self.name,
                    "payment_date": self.payment_date,
                },
                update_modified=True,
            )

        refresh_obligation_payment_status(
            self.fiscal_obligation
        )

    def before_cancel(self):
        collection = frappe.get_doc(
            "Tax Collection Reference",
            self.collection_reference,
        )

        if collection.status == "Confirmed":
            frappe.throw(
                "A Tax Payment confirmed by DC Pago cannot "
                "be cancelled directly."
            )

    def on_cancel(self):
        self.db_set(
            "status",
            "Cancelled",
            update_modified=False,
        )

        if self.collection_reference:
            linked_payment = frappe.db.get_value(
                "Tax Collection Reference",
                self.collection_reference,
                "tax_payment",
            )

            if linked_payment == self.name:
                frappe.db.set_value(
                    "Tax Collection Reference",
                    self.collection_reference,
                    {
                        "status": "Issued",
                        "tax_payment": None,
                        "payment_date": None,
                    },
                    update_modified=True,
                )

        if self.fiscal_obligation:
            refresh_obligation_payment_status(
                self.fiscal_obligation
            )

    # ======================================================
    # COLLECTION REFERENCE
    # ======================================================

    def load_collection_reference(self):
        if not self.collection_reference:
            return

        collection = frappe.get_doc(
            "Tax Collection Reference",
            self.collection_reference,
        )

        if collection.status in (
            "Cancelled",
            "Superseded",
            "Expired",
        ):
            frappe.throw(
                f"RUPE {collection.rupe} is "
                f"{collection.status} and cannot be paid."
            )

        self.fiscal_obligation = (
            collection.fiscal_obligation
        )
        self.tax_settlement = (
            collection.tax_settlement
        )
        self.payment_plan = (
            collection.payment_plan
        )
        self.instalment_no = (
            collection.instalment_no
        )

        self.nota_liquidacao = (
            collection.nota_liquidacao
        )
        self.rupe = collection.rupe
        self.rupe_amount = flt(collection.amount)
        self.rupe_due_date = collection.due_date

        # RUPE is atomic: payment amount is never entered
        # independently by the user.
        self.amount = flt(collection.amount)

        settlement = frappe.get_doc(
            "Tax Settlement",
            collection.tax_settlement,
        )

        if settlement.docstatus != 1:
            frappe.throw(
                "Tax Collection Reference must belong to a "
                "submitted Tax Settlement."
            )

        if (
            settlement.fiscal_obligation
            != collection.fiscal_obligation
        ):
            frappe.throw(
                "Collection Reference and Tax Settlement "
                "refer to different Fiscal Obligations."
            )

        obligation = frappe.get_doc(
            "Fiscal Obligation",
            collection.fiscal_obligation,
        )

        if obligation.docstatus != 1:
            frappe.throw(
                "Fiscal Obligation must be submitted before "
                "a Tax Payment can be recorded."
            )

        self.company = obligation.company
        self.currency = obligation.currency

        # --------------------------------------------------
        # ORIGINATING TAX LIABILITY VOUCHER
        # --------------------------------------------------
        #
        # The Fiscal Obligation Movement is the authoritative
        # audit trail for the accounting entry that created
        # the tax determination. Do not duplicate this logic
        # with manually entered Tax Payment fields.
        #
        origin = frappe.get_all(
            "Fiscal Obligation Movement",
            filters={
                "parent": obligation.name,
                "parenttype": "Fiscal Obligation",
                "movement_type": "Determination",
            },
            fields=[
                "name",
                "voucher_type",
                "voucher_no",
                "gl_entry",
                "account",
                "debit",
                "credit",
                "idx",
            ],
            order_by="idx asc",
            limit=1,
        )

        if origin:
            origin = origin[0]

            self.originating_voucher_type = (
                origin.voucher_type
            )
            self.originating_voucher = (
                origin.voucher_no
            )
            self.originating_gl_entry = (
                resolve_originating_gl_entry(origin, obligation.company)
            )
        else:
            self.originating_voucher_type = None
            self.originating_voucher = None
            self.originating_gl_entry = None

        self.settlement_adjustment_journal_entry = (
            settlement.adjustment_journal_entry
            or None
        )

        self.tax_type = (
            settlement.tax_type
            or obligation.get("tax_type")
        )

        self.tax_assessment = (
            settlement.tax_assessment
            or obligation.get("latest_tax_assessment")
        )

        # --------------------------------------------------
        # ACCOUNT TO SETTLE
        # --------------------------------------------------

        if collection.source_type == "Tax Settlement":
            if collection.payment_plan:
                frappe.throw(
                    "A Settlement Collection Reference "
                    "cannot have a Payment Plan."
                )

            if collection.instalment_no:
                frappe.throw(
                    "A Settlement Collection Reference "
                    "cannot have an Instalment No."
                )

            # ==================================================
            # AUTHORITY-ASSESSMENT SETTLEMENT
            # ==================================================
            #
            # The formal Nota de Liquidação has already moved
            # the accepted/provisioned tax exposure into:
            #
            #     CR Tax Settlement Control
            #
            # Therefore payment must clear that account:
            #
            #     DR Tax Settlement Control
            #     CR Bank
            #
            # Never fall back to the original operational tax
            # liability account for this route.
            # ==================================================

            if (
                settlement.tax_assessment
                and settlement.settlement_basis in (
                    "Accepted Assessment",
                    "Agreement",
                )
            ):
                self.tax_account = (
                    settlement.tax_settlement_control_account
                )

                if not self.tax_account:
                    frappe.throw(
                        "Authority Tax Settlement does not "
                        "have a Tax Settlement Control Account."
                    )

            # ==================================================
            # REGULAR SELF-DETERMINED SETTLEMENT
            # ==================================================

            else:
                self.tax_account = (
                    settlement.tax_account
                    or obligation.get("liability_account")
                    or obligation.get("receivable_account")
                )

        elif (
            collection.source_type
            == "Payment Plan Instalment"
        ):
            if not collection.payment_plan:
                frappe.throw(
                    "Payment Plan is required for a Payment "
                    "Plan Instalment Collection Reference."
                )

            if not collection.instalment_no:
                frappe.throw(
                    "Instalment No. is required for a "
                    "Payment Plan Collection Reference."
                )

            plan = frappe.get_doc(
                "Tax Payment Plan",
                collection.payment_plan,
            )

            if plan.docstatus != 1:
                frappe.throw(
                    "Tax Payment Plan must be submitted."
                )

            if (
                plan.fiscal_obligation
                != collection.fiscal_obligation
            ):
                frappe.throw(
                    "Payment Plan and Collection Reference "
                    "refer to different Fiscal Obligations."
                )

            if (
                plan.tax_settlement
                != collection.tax_settlement
            ):
                frappe.throw(
                    "Payment Plan and Collection Reference "
                    "refer to different Tax Settlements."
                )

            if plan.status not in (
                "Active",
                "In Arrears",
            ):
                frappe.throw(
                    f"Payment Plan status {plan.status} does "
                    "not permit instalment payment."
                )

            instalment = None

            for row in plan.instalments:
                if (
                    int(row.instalment_no or 0)
                    == int(collection.instalment_no or 0)
                ):
                    instalment = row
                    break

            if not instalment:
                frappe.throw(
                    f"Instalment "
                    f"{collection.instalment_no} does not "
                    f"exist in Payment Plan {plan.name}."
                )

            self.tax_account = (
                plan.payment_plan_control_account
            )

        else:
            frappe.throw(
                "Unsupported Collection Reference source."
            )

        if not self.tax_account:
            frappe.throw(
                "No accounting liability/control account "
                "could be resolved for this RUPE."
            )

    def validate_collection_reference(self):
        if not self.collection_reference:
            frappe.throw(
                "Tax Collection Reference is required."
            )

        collection = frappe.get_doc(
            "Tax Collection Reference",
            self.collection_reference,
        )

        if flt(collection.amount) <= 0:
            frappe.throw(
                "RUPE Amount must be greater than zero."
            )

        if abs(
            flt(self.amount) - flt(collection.amount)
        ) > 0.01:
            frappe.throw(
                "Tax Payment Amount must equal the full "
                "RUPE Amount. Partial RUPE payments are not "
                "permitted."
            )

        if collection.tax_payment:
            if collection.tax_payment != self.name:
                existing_status = frappe.db.get_value(
                    "Tax Payment",
                    collection.tax_payment,
                    "docstatus",
                )

                if existing_status != 2:
                    frappe.throw(
                        f"RUPE {collection.rupe} is already "
                        f"linked to Tax Payment "
                        f"{collection.tax_payment}."
                    )

        existing = frappe.get_all(
            "Tax Payment",
            filters={
                "collection_reference":
                    self.collection_reference,
                "docstatus": ["!=", 2],
                "name": ["!=", self.name or ""],
            },
            pluck="name",
            limit=1,
        )

        if existing:
            frappe.throw(
                f"RUPE {collection.rupe} already has Tax "
                f"Payment {existing[0]}."
            )

    # ======================================================
    # DC PAGO
    # ======================================================

    def validate_dc_pago(self):
        has_confirmation_data = any(
            [
                self.dc_pago,
                self.dc_pago_reference,
                self.dc_pago_date,
            ]
        )

        # Confirmation is performed explicitly after submit.
        # Draft/submission may contain no DC Pago yet.
        if has_confirmation_data and not self.dc_pago:
            frappe.throw(
                "DC Pago attachment is required when AGT "
                "confirmation data is recorded."
            )

    # ======================================================
    # ACCOUNTS
    # ======================================================

    def validate_accounts(self):
        if not self.tax_account:
            return

        tax_company = frappe.db.get_value(
            "Account",
            self.tax_account,
            "company",
        )

        if tax_company != self.company:
            frappe.throw(
                "Tax Account does not belong to the Tax "
                "Payment company."
            )

        if self.payment_mode == "Create Journal Entry":
            if not self.bank_cash_account:
                frappe.throw(
                    "Bank / Cash Account is required when "
                    "creating a Journal Entry."
                )

            result = frappe.db.get_value(
                "Account",
                self.bank_cash_account,
                [
                    "company",
                    "account_type",
                    "is_group",
                ],
            )

            if not result:
                frappe.throw(
                    "Bank / Cash Account does not exist."
                )

            bank_company, account_type, is_group = result

            if bank_company != self.company:
                frappe.throw(
                    "Bank / Cash Account must belong to the "
                    "same company."
                )

            if is_group:
                frappe.throw(
                    "Bank / Cash Account must be a ledger "
                    "account."
                )

            if account_type != "Bank":
                frappe.throw(
                    "Selected account must have Account "
                    "Type Bank."
                )

    # ======================================================
    # JOURNAL ENTRY
    # ======================================================

    def ensure_journal_entry(self):
        if self.journal_entry:
            je = frappe.get_doc(
                "Journal Entry",
                self.journal_entry,
            )

            if je.docstatus != 1:
                frappe.throw(
                    "The generated Journal Entry must be "
                    "submitted before the Tax Payment."
                )

            self.source_voucher_type = "Journal Entry"
            self.source_voucher = je.name
            return

        obligation = frappe.get_doc(
            "Fiscal Obligation",
            self.fiscal_obligation,
        )

        amount = flt(self.amount)

        je = frappe.new_doc("Journal Entry")
        je.company = self.company
        je.posting_date = self.payment_date
        je.user_remark = (
            f"Tax Payment {self.name or ''}"
            f" / RUPE {self.rupe}"
            f" / Fiscal Obligation "
            f"{self.fiscal_obligation}"
        )

        tax_row = je.append("accounts", {})
        tax_row.account = self.tax_account

        bank_row = je.append("accounts", {})
        bank_row.account = self.bank_cash_account

        if obligation.obligation_type == "Payable":
            tax_row.debit_in_account_currency = amount
            tax_row.credit_in_account_currency = 0

            bank_row.debit_in_account_currency = 0
            bank_row.credit_in_account_currency = amount

        elif obligation.obligation_type == "Receivable":
            tax_row.debit_in_account_currency = 0
            tax_row.credit_in_account_currency = amount

            bank_row.debit_in_account_currency = amount
            bank_row.credit_in_account_currency = 0

        else:
            frappe.throw(
                "Unsupported obligation type."
            )

        je.insert(ignore_permissions=True)
        self.journal_entry = je.name
        self.source_voucher_type = "Journal Entry"
        self.source_voucher = je.name

        # The Journal Entry on_submit GL hook runs before this Tax Payment
        # is stored with its JE link. Give that hook the obligation and
        # tax account explicitly for this submission only.
        previous_context = frappe.flags.get("angola_tax_payment_submission")
        frappe.flags.angola_tax_payment_submission = {
            "journal_entry": je.name,
            "fiscal_obligation": self.fiscal_obligation,
            "tax_account": self.tax_account,
            "company": self.company,
            "amount": amount,
        }
        try:
            je.submit()
        finally:
            if previous_context is None:
                frappe.flags.pop("angola_tax_payment_submission", None)
            else:
                frappe.flags.angola_tax_payment_submission = previous_context

    # ======================================================
    # EXISTING ACCOUNTING VOUCHER
    # ======================================================

    def validate_existing_voucher(self):
        if (
            not self.source_voucher_type
            or not self.source_voucher
        ):
            frappe.throw(
                "Source Voucher Type and Source Voucher "
                "are required."
            )

        if not frappe.db.exists(
            self.source_voucher_type,
            self.source_voucher,
        ):
            frappe.throw(
                "The selected accounting voucher does not "
                "exist."
            )

        docstatus = frappe.db.get_value(
            self.source_voucher_type,
            self.source_voucher,
            "docstatus",
        )

        if docstatus != 1:
            frappe.throw(
                "The linked accounting voucher must be "
                "submitted."
            )

        matching = frappe.get_all(
            "GL Entry",
            filters={
                "company": self.company,
                "voucher_type":
                    self.source_voucher_type,
                "voucher_no":
                    self.source_voucher,
                "account": self.tax_account,
                "is_cancelled": 0,
            },
            fields=[
                "name",
                "debit",
                "credit",
            ],
        )

        if not matching:
            frappe.throw(
                "The linked voucher does not post to the "
                "required Tax / Payment Plan Account."
            )

        obligation = frappe.get_doc(
            "Fiscal Obligation",
            self.fiscal_obligation,
        )

        expected = flt(self.amount)
        valid = False

        for row in matching:
            if obligation.obligation_type == "Payable":
                movement = (
                    flt(row.debit)
                    - flt(row.credit)
                )
            else:
                movement = (
                    flt(row.credit)
                    - flt(row.debit)
                )

            if abs(movement - expected) < 0.01:
                valid = True
                break

        if not valid:
            frappe.throw(
                "The linked voucher does not contain an "
                "accounting settlement matching the full "
                "RUPE amount."
            )

    def find_tax_gl_entry(self):
        if (
            not self.source_voucher_type
            or not self.source_voucher
        ):
            return

        rows = frappe.get_all(
            "GL Entry",
            filters={
                "company": self.company,
                "voucher_type":
                    self.source_voucher_type,
                "voucher_no":
                    self.source_voucher,
                "account": self.tax_account,
                "is_cancelled": 0,
            },
            fields=[
                "name",
                "debit",
                "credit",
            ],
        )

        obligation = frappe.get_doc(
            "Fiscal Obligation",
            self.fiscal_obligation,
        )

        amount = flt(self.amount)

        for row in rows:
            if obligation.obligation_type == "Payable":
                movement = (
                    flt(row.debit)
                    - flt(row.credit)
                )
            else:
                movement = (
                    flt(row.credit)
                    - flt(row.debit)
                )

            if abs(movement - amount) < 0.01:
                self.gl_entry = row.name
                return

        frappe.throw(
            "Could not identify the accounting GL Entry "
            "for this Tax Payment."
        )


# ==========================================================
# AGT CONFIRMATION
# ==========================================================

@frappe.whitelist()
def confirm_dc_pago(
    tax_payment,
    dc_pago,
    dc_pago_reference=None,
    dc_pago_date=None,
):
    payment = frappe.get_doc(
        "Tax Payment",
        tax_payment,
    )

    if payment.docstatus != 1:
        frappe.throw(
            "Only a submitted Tax Payment can be confirmed."
        )

    if payment.status == "Confirmed":
        frappe.throw(
            "Tax Payment is already confirmed."
        )

    if not dc_pago:
        frappe.throw(
            "DC Pago attachment is required."
        )

    collection = frappe.get_doc(
        "Tax Collection Reference",
        payment.collection_reference,
    )

    if collection.tax_payment != payment.name:
        frappe.throw(
            "Collection Reference is not linked to this "
            "Tax Payment."
        )

    if collection.status != "Awaiting DC Pago":
        frappe.throw(
            "Collection Reference must be Awaiting DC Pago "
            "before confirmation."
        )

    payment.db_set(
        {
            "dc_pago": dc_pago,
            "dc_pago_reference": dc_pago_reference,
            "dc_pago_date": dc_pago_date,
            "confirmed_by": frappe.session.user,
            "confirmed_at": now_datetime(),
            "status": "Confirmed",
        },
        update_modified=True,
    )

    frappe.db.set_value(
        "Tax Collection Reference",
        collection.name,
        {
            "status": "Confirmed",
            "tax_payment": payment.name,
            "payment_date": payment.payment_date,
        },
        update_modified=True,
    )

    refresh_obligation_payment_status(
        payment.fiscal_obligation
    )

    return {
        "tax_payment": payment.name,
        "collection_reference": collection.name,
        "rupe": collection.rupe,
        "status": "Confirmed",
    }
