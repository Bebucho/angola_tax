import frappe

from frappe.model.document import Document
from frappe.utils import flt


TOLERANCE = 0.01


class VATImportDocument(Document):

    def validate(self):
        self.load_vat_settings()
        self.load_purchase_invoice_context()
        self.validate_amounts()
        self.validate_accounts()
        self.validate_stock_import_route()
        self.validate_existing_journal_entries()
        self.calculate_effective_rate()
        self.set_status()


    # ======================================================
    # SETTINGS
    # ======================================================

    def load_vat_settings(self):

        rows = frappe.get_all(
            "VAT Settings",
            filters={
                "company": self.company,
                "active": 1,
            },
            fields=[
                "name",
                "vat_customs_recoverable_account",
                "vat_customs_clearing_account",
                "import_landed_cost_clearing_account",
                "import_landed_cost_clearing_account",
            ],
            limit_page_length=2,
        )

        if not rows:
            frappe.throw(
                f"No active VAT Settings exists for "
                f"{self.company}."
            )

        if len(rows) > 1:
            frappe.throw(
                f"More than one active VAT Settings exists "
                f"for {self.company}."
            )

        settings = rows[0]

        self.vat_settings = settings.name

        self.vat_customs_recoverable_account = (
            settings.vat_customs_recoverable_account
        )

        self.vat_customs_clearing_account = (
            settings.vat_customs_clearing_account
        )

        self.import_landed_cost_clearing_account = (
            settings.import_landed_cost_clearing_account
        )

        self.import_landed_cost_clearing_account = (
            settings.import_landed_cost_clearing_account
        )


    # ======================================================
    # PURCHASE INVOICE / BROKER CONTEXT
    # ======================================================

    def load_purchase_invoice_context(self):

        if not self.source_purchase_invoice:
            return

        pi = frappe.get_doc(
            "Purchase Invoice",
            self.source_purchase_invoice,
        )

        if pi.company != self.company:
            frappe.throw(
                "Source Purchase Invoice belongs to a "
                "different company."
            )

        if not self.freight_forwarder:
            self.freight_forwarder = pi.supplier

        if not self.debit_note_reference:
            self.debit_note_reference = (
                pi.bill_no
                or pi.name
            )

        if not self.debit_note_date:
            self.debit_note_date = (
                pi.bill_date
                or pi.posting_date
            )


    # ======================================================
    # BASIC VALIDATION
    # ======================================================

    def validate_amounts(self):

        if flt(self.import_taxable_base) <= 0:
            frappe.throw(
                "Import Taxable Base must be greater than zero."
            )

        if flt(self.customs_vat_amount) <= 0:
            frappe.throw(
                "Customs VAT Amount must be greater than zero."
            )


    def calculate_effective_rate(self):

        if flt(self.import_taxable_base):
            self.effective_vat_rate = flt(
                flt(self.customs_vat_amount)
                / flt(self.import_taxable_base)
                * 100,
                6,
            )
        else:
            self.effective_vat_rate = 0


    def validate_accounts(self):

        required = {
            "VAT Customs Recoverable Account":
                self.vat_customs_recoverable_account,
            "VAT Customs Clearing Account":
                self.vat_customs_clearing_account,
        }

        missing = [
            label
            for label, account in required.items()
            if not account
        ]

        if missing:
            frappe.throw(
                "Import VAT accounting configuration is "
                "incomplete: "
                + ", ".join(missing)
            )

        for label, account in required.items():
            self.validate_account(
                account,
                label,
            )

        if (
            self.vat_customs_recoverable_account
            == self.vat_customs_clearing_account
        ):
            frappe.throw(
                "VAT Customs Recoverable Account and "
                "VAT Customs Clearing Account cannot "
                "be the same account."
            )

        if self.source_cost_stock_account:
            self.validate_account(
                self.source_cost_stock_account,
                "Source Cost / Stock Account",
            )

            if self.source_cost_stock_account in (
                self.vat_customs_recoverable_account,
                self.vat_customs_clearing_account,
            ):
                frappe.throw(
                    "Source Cost / Stock Account cannot be "
                    "one of the Import VAT control accounts."
                )

        if (
            self.cost_treatment
            == "Stock / Landed Cost Voucher"
        ):
            if not self.import_landed_cost_clearing_account:
                frappe.throw(
                    "Import / Landed Cost Clearing Account "
                    "must be configured in VAT Settings "
                    "for stock imports."
                )

            self.validate_account(
                self.import_landed_cost_clearing_account,
                "Import / Landed Cost Clearing Account",
            )

            if self.import_landed_cost_clearing_account in (
                self.vat_customs_recoverable_account,
                self.vat_customs_clearing_account,
            ):
                frappe.throw(
                    "Import / Landed Cost Clearing Account "
                    "cannot be one of the customs VAT accounts."
                )

        if (
            self.cost_treatment
            == "Stock / Landed Cost Voucher"
        ):
            if not self.import_landed_cost_clearing_account:
                frappe.throw(
                    "Import / Landed Cost Clearing Account "
                    "must be configured in VAT Settings "
                    "for stock imports."
                )

            self.validate_account(
                self.import_landed_cost_clearing_account,
                "Import / Landed Cost Clearing Account",
            )

            if self.import_landed_cost_clearing_account in (
                self.vat_customs_recoverable_account,
                self.vat_customs_clearing_account,
            ):
                frappe.throw(
                    "Import / Landed Cost Clearing Account "
                    "cannot be one of the customs VAT accounts."
                )


    def validate_account(
        self,
        account,
        label,
    ):

        if not account:
            frappe.throw(
                f"{label} is required."
            )

        row = frappe.db.get_value(
            "Account",
            account,
            [
                "company",
                "is_group",
                "account_currency",
            ],
            as_dict=True,
        )

        if not row:
            frappe.throw(
                f"{label} {account} does not exist."
            )

        if row.company != self.company:
            frappe.throw(
                f"{label} belongs to another company."
            )

        if row.is_group:
            frappe.throw(
                f"{label} must be a ledger account."
            )


    # ======================================================
    # STOCK IMPORT / LANDED COST ROUTE
    # ======================================================

    def validate_stock_import_route(self):
        """
        Metagest standard for imported stock:

            Purchase Receipt
                -> goods enter stock

            Customs VAT
                -> VAT Import Document
                -> VAT recoverable / customs clearing

            Capitalisable import costs
                -> Landed Cost Voucher
                -> inventory valuation

        Direct Journal Entry reclassification against stock
        accounts is not permitted by this workflow.
        """

        if (
            self.cost_treatment
            != "Stock / Landed Cost Voucher"
        ):
            return

        if self.source_cost_stock_account:
            frappe.throw(
                "Source Cost / Stock Account must not be "
                "used for the Stock / Landed Cost Voucher route. "
                "Inventory valuation must be adjusted through "
                "Landed Cost Voucher."
            )

        if self.debit_note_reclassification_journal_entry:
            frappe.throw(
                "Direct Debit Note reclassification Journal Entry "
                "is not permitted for stock imports. "
                "Use Landed Cost Voucher."
            )

        if self.purchase_receipt:

            pr = frappe.get_doc(
                "Purchase Receipt",
                self.purchase_receipt,
            )

            if pr.company != self.company:
                frappe.throw(
                    "Purchase Receipt belongs to another company."
                )

            if pr.docstatus != 1:
                frappe.throw(
                    "Purchase Receipt must be submitted."
                )

        if self.landed_cost_voucher:

            lcv = frappe.get_doc(
                "Landed Cost Voucher",
                self.landed_cost_voucher,
            )

            if lcv.company != self.company:
                frappe.throw(
                    "Landed Cost Voucher belongs to another company."
                )

            if lcv.docstatus != 1:
                frappe.throw(
                    "Landed Cost Voucher must be submitted."
                )

            if not self.purchase_receipt:
                frappe.throw(
                    "Purchase Receipt is required when a "
                    "Landed Cost Voucher is linked."
                )


    # ======================================================
    # JOURNAL ENTRY VALIDATION
    # ======================================================

    def validate_existing_journal_entries(self):

        if self.recognition_journal_entry:
            self.validate_customs_recognition_entry()

        if self.debit_note_reclassification_journal_entry:
            self.validate_debit_note_reclassification_entry()


    def get_submitted_je(self, name):

        je = frappe.get_doc(
            "Journal Entry",
            name,
        )

        if je.docstatus != 1:
            frappe.throw(
                f"Journal Entry {name} must be submitted."
            )

        if je.company != self.company:
            frappe.throw(
                f"Journal Entry {name} belongs to another company."
            )

        return je


    def account_totals(
        self,
        je,
        account,
    ):

        debit = 0
        credit = 0

        for row in je.accounts or []:
            if row.account != account:
                continue

            debit += flt(
                row.debit_in_account_currency
            )

            credit += flt(
                row.credit_in_account_currency
            )

        return (
            flt(debit, 2),
            flt(credit, 2),
        )


    def validate_customs_recognition_entry(self):

        je = self.get_submitted_je(
            self.recognition_journal_entry
        )

        amount = flt(
            self.customs_vat_amount,
            2,
        )

        rec_debit, rec_credit = self.account_totals(
            je,
            self.vat_customs_recoverable_account,
        )

        clr_debit, clr_credit = self.account_totals(
            je,
            self.vat_customs_clearing_account,
        )

        if (
            abs(rec_debit - amount) >= TOLERANCE
            or abs(rec_credit) >= TOLERANCE
            or abs(clr_credit - amount) >= TOLERANCE
            or abs(clr_debit) >= TOLERANCE
        ):
            frappe.throw(
                "Customs VAT Recognition Journal Entry "
                "must contain exactly:\n"
                f"DR {self.vat_customs_recoverable_account} "
                f"{amount}\n"
                f"CR {self.vat_customs_clearing_account} "
                f"{amount}"
            )

        return True


    def validate_debit_note_reclassification_entry(self):

        if not self.source_cost_stock_account:
            frappe.throw(
                "Source Cost / Stock Account is required "
                "for Debit Note reclassification."
            )

        je = self.get_submitted_je(
            self.debit_note_reclassification_journal_entry
        )

        amount = flt(
            self.customs_vat_amount,
            2,
        )

        clr_debit, clr_credit = self.account_totals(
            je,
            self.vat_customs_clearing_account,
        )

        src_debit, src_credit = self.account_totals(
            je,
            self.source_cost_stock_account,
        )

        if (
            abs(clr_debit - amount) >= TOLERANCE
            or abs(clr_credit) >= TOLERANCE
            or abs(src_credit - amount) >= TOLERANCE
            or abs(src_debit) >= TOLERANCE
        ):
            frappe.throw(
                "Debit Note Reclassification Journal Entry "
                "must contain exactly:\n"
                f"DR {self.vat_customs_clearing_account} "
                f"{amount}\n"
                f"CR {self.source_cost_stock_account} "
                f"{amount}"
            )

        return True


    # ======================================================
    # STATUS
    # ======================================================

    def set_status(self):

        recognition = bool(
            self.recognition_journal_entry
        )

        debit_note = bool(
            self.debit_note_reference
            or self.source_purchase_invoice
            or self.debit_note_reclassification_journal_entry
        )

        reclassification = bool(
            self.debit_note_reclassification_journal_entry
        )

        stock_lcv_matched = bool(
            self.cost_treatment
            == "Stock / Landed Cost Voucher"
            and self.purchase_receipt
            and self.landed_cost_voucher
        )

        if (
            recognition
            and (
                reclassification
                or stock_lcv_matched
            )
        ):
            self.status = "Matched"

        elif recognition:
            self.status = "Customs Recognised"

        elif debit_note:
            self.status = "Debit Note Received"

        else:
            self.status = "Draft"


    # ======================================================
    # ACCOUNTING ACTIONS
    # ======================================================

    def create_customs_recognition(self):

        if self.recognition_journal_entry:
            frappe.throw(
                "Customs VAT has already been recognised."
            )

        self.validate()

        amount = flt(
            self.customs_vat_amount,
            2,
        )

        je = frappe.new_doc(
            "Journal Entry"
        )

        je.voucher_type = "Journal Entry"
        je.company = self.company
        je.posting_date = self.customs_date

        je.user_remark = (
            f"Customs VAT recognition - "
            f"{self.customs_reference} - "
            f"{self.name}"
        )

        je.append(
            "accounts",
            {
                "account":
                    self.vat_customs_recoverable_account,
                "debit_in_account_currency":
                    amount,
                "credit_in_account_currency":
                    0,
            },
        )

        je.append(
            "accounts",
            {
                "account":
                    self.vat_customs_clearing_account,
                "debit_in_account_currency":
                    0,
                "credit_in_account_currency":
                    amount,
            },
        )

        je.insert(
            ignore_permissions=True
        )

        je.submit()

        self.db_set(
            "recognition_journal_entry",
            je.name,
            update_modified=False,
        )

        self.reload()
        self.save(
            ignore_permissions=True
        )

        return je.name


    def create_debit_note_reclassification(self):

        if (
            self.cost_treatment
            == "Stock / Landed Cost Voucher"
        ):
            frappe.throw(
                "Direct Journal Entry reclassification is "
                "not permitted for stock imports. "
                "Use Purchase Receipt and Landed Cost Voucher."
            )

        if self.debit_note_reclassification_journal_entry:
            frappe.throw(
                "Debit Note VAT has already been reclassified."
            )

        if not self.debit_note_reference:
            frappe.throw(
                "Debit Note Reference is required."
            )

        if not self.debit_note_date:
            frappe.throw(
                "Debit Note Date is required."
            )

        if not self.cost_treatment:
            frappe.throw(
                "Cost Treatment is required."
            )

        if not self.source_cost_stock_account:
            frappe.throw(
                "Source Cost / Stock Account is required."
            )

        self.validate()

        amount = flt(
            self.customs_vat_amount,
            2,
        )

        je = frappe.new_doc(
            "Journal Entry"
        )

        je.voucher_type = "Journal Entry"
        je.company = self.company
        je.posting_date = self.debit_note_date

        je.user_remark = (
            f"Customs VAT clearing from broker debit note "
            f"{self.debit_note_reference} - "
            f"{self.name}"
        )

        je.append(
            "accounts",
            {
                "account":
                    self.vat_customs_clearing_account,
                "debit_in_account_currency":
                    amount,
                "credit_in_account_currency":
                    0,
            },
        )

        je.append(
            "accounts",
            {
                "account":
                    self.source_cost_stock_account,
                "debit_in_account_currency":
                    0,
                "credit_in_account_currency":
                    amount,
            },
        )

        je.insert(
            ignore_permissions=True
        )

        je.submit()

        self.db_set(
            "debit_note_reclassification_journal_entry",
            je.name,
            update_modified=False,
        )

        self.reload()
        self.save(
            ignore_permissions=True
        )

        return je.name


    def customs_recognition_is_valid(self):

        if not self.recognition_journal_entry:
            return False

        self.validate_customs_recognition_entry()

        return True


@frappe.whitelist()
def create_customs_recognition(name):

    doc = frappe.get_doc(
        "VAT Import Document",
        name,
    )

    return doc.create_customs_recognition()


@frappe.whitelist()
def create_debit_note_reclassification(name):

    doc = frappe.get_doc(
        "VAT Import Document",
        name,
    )

    return doc.create_debit_note_reclassification()
