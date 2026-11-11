import json
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now


ZERO_TOLERANCE = 0.01


class VATPeriodControl(Document):

    def autoname(self):
        if not self.company or not self.period_end:
            return

        abbr = frappe.db.get_value(
            "Company", self.company, "abbr"
        ) or self.company

        period = getdate(self.period_end)

        self.name = (
            f"VAT-{abbr}-{period.year:04d}{period.month:02d}"
        )

    def validate(self):
        self.validate_period()
        self.set_defaults()
        self.validate_unique_period()
        self.validate_settings()
        self.calculate_transaction_totals()
        self.calculate_modelo_7_lines()
        self.set_declared_summary_from_modelo_7()
        self.calculate_reconciliation()
        self.validate_reconciling_items()
        self.set_status()

    def validate_period(self):
        if (
            self.period_start
            and self.period_end
            and getdate(self.period_start) > getdate(self.period_end)
        ):
            frappe.throw(
                _("Period Start cannot be later than Period End.")
            )

    def set_defaults(self):
        if self.period_end:
            self.fiscal_year = str(
                getdate(self.period_end).year
            )

        if self.company:
            self.currency = frappe.db.get_value(
                "Company",
                self.company,
                "default_currency",
            )

        if self.company and not self.vat_settings:
            settings = frappe.db.get_value(
                "VAT Settings",
                {
                    "company": self.company,
                    "active": 1,
                },
                "name",
            )

            if settings:
                self.vat_settings = settings

    def validate_unique_period(self):
        if (
            not self.company
            or not self.period_start
            or not self.period_end
        ):
            return

        existing = frappe.db.exists(
            "VAT Period Control",
            {
                "company": self.company,
                "period_start": self.period_start,
                "period_end": self.period_end,
                "docstatus": ["!=", 2],
                "name": ["!=", self.name or ""],
            },
        )

        if existing:
            frappe.throw(
                _(
                    "VAT Period already exists for {0} "
                    "from {1} to {2}: {3}"
                ).format(
                    self.company,
                    self.period_start,
                    self.period_end,
                    existing,
                )
            )

    def validate_settings(self):
        if not self.vat_settings:
            return

        settings = frappe.get_doc(
            "VAT Settings",
            self.vat_settings,
        )

        if settings.company != self.company:
            frappe.throw(
                _("VAT Settings must belong to the same Company.")
            )

        if not settings.active:
            frappe.throw(
                _("VAT Settings are inactive.")
            )

    def get_vat_determination_transactions(self):
        """
        Return only GL transactions that constitute the VAT
        determination for this fiscal period.

        Included automatically:
        - Sales Invoice / Sales Return
        - Purchase Invoice / Purchase Return
        - recognised VAT Import Journal Entries
        - explicit VAT reconciliation adjustment Journal Entries

        Excluded:
        - opening balances
        - Payment Entries
        - historical settlements
        - unrelated/manual Journal Entries
        - VAT closing Journal Entry

        Accounting remains authoritative for amounts; this method
        controls only whether a GL movement belongs to the current
        VAT determination.
        """

        import_jes = set(
            frappe.get_all(
                "VAT Import Document",
                filters={
                    "company": self.company,
                    "recognition_journal_entry":
                        ["is", "set"],
                },
                pluck="recognition_journal_entry",
                limit_page_length=0,
            )
        )

        reconciliation_jes = {
            row.journal_entry
            for row in self.reconciling_items or []
            if row.journal_entry
        }

        result = []

        for row in self.transactions or []:

            # Opening balances are balance-sheet carry-forward,
            # never current-period VAT determination.
            gl = frappe.db.get_value(
                "GL Entry",
                row.gl_entry,
                [
                    "is_opening",
                    "voucher_type",
                    "voucher_no",
                ],
                as_dict=True,
            )

            if not gl:
                continue

            if gl.is_opening == "Yes":
                continue

            # The VAT closing JE is a post-determination transfer.
            if (
                self.closing_journal_entry
                and gl.voucher_type == "Journal Entry"
                and gl.voucher_no
                    == self.closing_journal_entry
            ):
                continue

            if gl.voucher_type in (
                "Sales Invoice",
                "Purchase Invoice",
            ):
                result.append(row)
                continue

            if (
                gl.voucher_type == "Journal Entry"
                and (
                    gl.voucher_no in import_jes
                    or gl.voucher_no
                        in reconciliation_jes
                )
            ):
                result.append(row)
                continue

            # Payment Entry and every other unclassified movement
            # intentionally remain outside determination.

        return result


    def calculate_transaction_totals(self):
        """
        Calculate the current-period VAT accounting position from
        determination movements only.

        Positive = VAT payable to State.
        Negative = VAT credit in favour of taxpayer.
        """

        state = 0
        taxpayer = 0

        mapping = self.get_account_mapping()

        # Keep vat_amount populated for all rows for audit/display,
        # but only determination transactions affect period totals.
        for row in self.transactions or []:

            config = mapping.get(
                row.account
            )

            if not config:
                continue

            if config.modelo_7_side == "State":

                row.vat_amount = (
                    flt(row.credit)
                    - flt(row.debit)
                )

            elif config.modelo_7_side == "Taxpayer":

                row.vat_amount = (
                    flt(row.debit)
                    - flt(row.credit)
                )

            else:

                row.vat_amount = (
                    flt(row.credit)
                    - flt(row.debit)
                )

            row.vat_nature = (
                config.vat_nature
            )

        for row in (
            self.get_vat_determination_transactions()
        ):

            config = mapping.get(
                row.account
            )

            if not config:
                continue

            if config.modelo_7_side == "State":

                state += (
                    flt(row.credit)
                    - flt(row.debit)
                )

            elif config.modelo_7_side == "Taxpayer":

                taxpayer += (
                    flt(row.debit)
                    - flt(row.credit)
                )

        self.accounting_state_amount = flt(
            state,
            2,
        )

        self.accounting_taxpayer_amount = flt(
            taxpayer,
            2,
        )

        self.accounting_net_position = flt(
            state - taxpayer,
            2,
        )



    def calculate_modelo_7_lines(self):
        """
        Modelo 7 line difference convention:

            declared - accounting source

        Positive:
            declaration is higher than accounting.

        Negative:
            declaration is lower than accounting.
        """

        for row in self.modelo_7_lines or []:
            row.difference = flt(
                flt(row.declared_amount)
                - flt(row.source_amount),
                2,
            )

    def set_declared_summary_from_modelo_7(self):
        """
        Derive the VAT Period declared summary from the actual
        Modelo 7 lines.

        32 = VAT in favour of taxpayer
        33 = VAT in favour of State
        36 = previous-period credit used
        37 = VAT payable
        38 = VAT credit
        """

        declared = {
            str(row.field_code):
                flt(row.declared_amount)
            for row in self.modelo_7_lines or []
        }

        self.declared_taxpayer_amount = flt(
            declared.get("32"),
            2,
        )

        self.declared_state_amount = flt(
            declared.get("33"),
            2,
        )

        self.previous_credit = flt(
            declared.get("36"),
            2,
        )

        self.declared_payable = flt(
            declared.get("37"),
            2,
        )

        self.declared_credit = flt(
            declared.get("38"),
            2,
        )


    def calculate_reconciliation(self):
        """
        Reconcile final accounting VAT against the submitted
        Modelo 7 position.

        Sign convention:

            difference = declared - accounting

        Corrected reconciling items backed by a submitted Journal
        Entry no longer contribute to reconciling_total, because
        the GL correction is already reflected in accounting.
        """

        self.declaration_net_position = flt(
            flt(self.declared_payable)
            - flt(self.declared_credit),
            2,
        )

        reconciling_total = 0

        for row in self.reconciling_items or []:

            row.difference = flt(
                flt(row.declared_amount)
                - flt(row.accounting_amount),
                2,
            )

            corrected_in_gl = False

            if (
                row.resolution_status == "Corrected"
                and row.journal_entry
            ):
                corrected_in_gl = (
                    frappe.db.get_value(
                        "Journal Entry",
                        row.journal_entry,
                        "docstatus",
                    )
                    == 1
                )

            if not corrected_in_gl:
                reconciling_total += flt(
                    row.difference
                )

        self.reconciling_total = flt(
            reconciling_total,
            2,
        )

        self.unexplained_difference = flt(
            flt(self.declaration_net_position)
            - flt(self.accounting_net_position)
            - flt(self.reconciling_total),
            2,
        )

        self.is_reconciled = int(
            bool(self.modelo_7_confirmed)
            and abs(
                flt(self.unexplained_difference)
            ) < ZERO_TOLERANCE
        )


    def set_status(self):
        if self.docstatus == 2:
            self.status = "Cancelled"
            return

        if self.docstatus == 1:
            self.status = (
                "Closed"
                if self.fiscal_obligation
                else "Submitted"
            )
            return

        if self.is_reconciled:
            self.status = "Reconciled"
            return

        if self.modelo_7_confirmed:
            self.status = "Reviewed"
            return

        if self.transactions:
            self.status = "Loaded"
            return

        self.status = "Draft"

    def get_vat_payable_configuration(self):
        """
        Resolve the VAT liability account from Tax Account
        Configuration.

        VAT Settings supplies the Tax Type.
        Tax Account Configuration supplies the payable account.

        Exactly one active/effective VAT configuration must exist
        for the Company / Tax Type / period.
        """

        if not self.vat_settings:
            frappe.throw(
                _("VAT Settings are required.")
            )

        settings = frappe.get_doc(
            "VAT Settings",
            self.vat_settings,
        )

        if not settings.tax_type:
            frappe.throw(
                _(
                    "VAT Tax Type must be configured "
                    "in VAT Settings."
                )
            )

        if not self.period_end:
            frappe.throw(
                _("Period End is required.")
            )

        period_end = getdate(
            self.period_end
        )

        configurations = frappe.get_all(
            "Tax Account Configuration",
            filters={
                "company":
                    self.company,
                "tax_type":
                    settings.tax_type,
                "source_module":
                    "VAT",
                "active":
                    1,
                "docstatus":
                    1,
                "effective_from":
                    ["<=", period_end],
            },
            fields=[
                "name",
                "company",
                "tax_type",
                "tax_name",
                "account",
                "determination_direction",
                "settlement_direction",
                "source_module",
                "effective_from",
                "effective_to",
            ],
            order_by="effective_from desc",
            limit_page_length=0,
        )

        matching = []

        for row in configurations:

            if (
                row.effective_to
                and getdate(row.effective_to)
                < period_end
            ):
                continue

            matching.append(row)

        if not matching:
            frappe.throw(
                _(
                    "No active Tax Account Configuration "
                    "exists for {0} / {1} / VAT "
                    "for period ending {2}."
                ).format(
                    self.company,
                    settings.tax_type,
                    period_end,
                )
            )

        if len(matching) > 1:
            frappe.throw(
                _(
                    "More than one active Tax Account "
                    "Configuration applies to {0} / {1} / VAT "
                    "for period ending {2}."
                ).format(
                    self.company,
                    settings.tax_type,
                    period_end,
                )
            )

        config = matching[0]

        if (
            config.determination_direction
            != "Credit"
        ):
            frappe.throw(
                _(
                    "VAT Tax Account Configuration must use "
                    "Determination Direction = Credit."
                )
            )

        if (
            config.settlement_direction
            != "Debit"
        ):
            frappe.throw(
                _(
                    "VAT Tax Account Configuration must use "
                    "Settlement Direction = Debit."
                )
            )

        if not config.account:
            frappe.throw(
                _(
                    "VAT Tax Account Configuration "
                    "has no Tax Account."
                )
            )

        account_company = frappe.db.get_value(
            "Account",
            config.account,
            "company",
        )

        if account_company != self.company:
            frappe.throw(
                _(
                    "VAT payable account {0} does not belong "
                    "to Company {1}."
                ).format(
                    config.account,
                    self.company,
                )
            )

        mapping = next(
            (
                row
                for row in settings.account_mappings or []
                if (
                    row.active
                    and row.account
                    == config.account
                )
            ),
            None,
        )

        if not mapping:
            frappe.throw(
                _(
                    "VAT payable account {0} from Tax Account "
                    "Configuration is not active in VAT "
                    "Account Mapping."
                ).format(
                    config.account
                )
            )

        if mapping.vat_nature != "IVA a Pagar":
            frappe.throw(
                _(
                    "VAT payable account {0} must have "
                    "VAT Nature = IVA a Pagar."
                ).format(
                    config.account
                )
            )

        if mapping.modelo_7_side != "Control":
            frappe.throw(
                _(
                    "VAT payable account {0} must have "
                    "Modelo 7 Side = Control."
                ).format(
                    config.account
                )
            )

        return config


    def get_or_create_vat_fiscal_obligation(
        self,
        payable_config,
    ):
        """
        Return the unique operational Fiscal Obligation belonging
        to this VAT Period.

        The VAT Period determines the fiscal period and source
        lineage. Tax Account Configuration determines the
        liability account.
        """

        settings = frappe.get_doc(
            "VAT Settings",
            self.vat_settings,
        )

        tax_type = frappe.get_doc(
            "Tax Type",
            settings.tax_type,
        )

        if tax_type.obligation_type != "Payable":
            frappe.throw(
                _(
                    "VAT Tax Type must have "
                    "Obligation Type = Payable."
                )
            )

        existing = frappe.get_all(
            "Fiscal Obligation",
            filters={
                "company":
                    self.company,
                "tax_type":
                    settings.tax_type,
                "obligation_origin":
                    "Operational",
                "source_document_type":
                    "VAT Period Control",
                "source_document":
                    self.name,
                "docstatus":
                    ["!=", 2],
            },
            pluck="name",
            limit_page_length=2,
        )

        if len(existing) > 1:
            frappe.throw(
                _(
                    "More than one active Fiscal Obligation "
                    "is linked to VAT Period {0}."
                ).format(
                    self.name
                )
            )

        if existing:

            obligation = frappe.get_doc(
                "Fiscal Obligation",
                existing[0],
            )

            if (
                obligation.liability_account
                != payable_config.account
            ):
                frappe.throw(
                    _(
                        "Existing VAT Fiscal Obligation {0} "
                        "uses liability account {1}, but current "
                        "Tax Account Configuration requires {2}."
                    ).format(
                        obligation.name,
                        obligation.liability_account,
                        payable_config.account,
                    )
                )

            if (
                obligation.journal_entry
                and obligation.journal_entry
                != self.closing_journal_entry
            ):
                frappe.throw(
                    _(
                        "Fiscal Obligation {0} is already linked "
                        "to Journal Entry {1}."
                    ).format(
                        obligation.name,
                        obligation.journal_entry,
                    )
                )

            return obligation

        currency = frappe.db.get_value(
            "Company",
            self.company,
            "default_currency",
        )

        obligation = frappe.get_doc(
            {
                "doctype":
                    "Fiscal Obligation",

                "obligation_origin":
                    "Operational",

                "company":
                    self.company,

                "tax_type":
                    settings.tax_type,

                "tax_name":
                    tax_type.tax_name,

                "obligation_type":
                    "Payable",

                "responsible_user":
                    frappe.session.user
                    or "Administrator",

                "fiscal_period_from":
                    self.period_start,

                "fiscal_period_to":
                    self.period_end,

                "fiscal_year":
                    str(
                        getdate(
                            self.period_start
                        ).year
                    ),

                "currency":
                    currency,

                "source_module":
                    "VAT",

                "source_document_type":
                    "VAT Period Control",

                "source_document":
                    self.name,

                "liability_account":
                    payable_config.account,

                "notes":
                    (
                        "Generated from reconciled VAT Period "
                        f"{self.name} after monthly VAT closing."
                    ),
            }
        )

        obligation.insert(
            ignore_permissions=True
        )

        return obligation


    def get_vat_rounding_account(self):
        """
        Resolve and validate the VAT statutory rounding account.

        Requirements:
        - same company
        - ledger account
        - Profit and Loss account
        - not any configured VAT control account
        """

        settings = frappe.get_doc(
            "VAT Settings",
            self.vat_settings,
        )

        account = settings.get(
            "vat_rounding_account"
        )

        if not account:

            frappe.throw(
                _(
                    "VAT Rounding Account must be "
                    "configured in VAT Settings."
                )
            )

        values = frappe.db.get_value(
            "Account",
            account,
            [
                "company",
                "is_group",
                "root_type",
            ],
            as_dict=True,
        )

        if not values:

            frappe.throw(
                _(
                    "VAT Rounding Account {0} "
                    "does not exist."
                ).format(
                    account
                )
            )

        if values.company != self.company:

            frappe.throw(
                _(
                    "VAT Rounding Account must belong "
                    "to Company {0}."
                ).format(
                    self.company
                )
            )

        if values.is_group:

            frappe.throw(
                _(
                    "VAT Rounding Account must be "
                    "a ledger account."
                )
            )

        if values.root_type not in (
            "Income",
            "Expense",
        ):

            frappe.throw(
                _(
                    "VAT Rounding Account must be "
                    "a Profit and Loss account."
                )
            )

        protected = {
            row.account
            for row in settings.account_mappings or []
            if (
                row.active
                and row.account
            )
        }

        protected.update(
            value
            for value in (
                settings.get(
                    "vat_recoverable_account"
                ),
                settings.get(
                    "vat_reconciling_account"
                ),
                settings.get(
                    "vat_customs_recoverable_account"
                ),
                settings.get(
                    "vat_customs_clearing_account"
                ),
            )
            if value
        )

        payable_config = (
            self.get_vat_payable_configuration()
        )

        protected.add(
            payable_config.account
        )

        if account in protected:

            frappe.throw(
                _(
                    "VAT Rounding Account cannot be "
                    "a VAT control or declaration account."
                )
            )

        return account


    def get_vat_recoverable_account(self):
        """
        Validate the configured VAT recoverable balance-sheet
        account.

        Recoverable VAT does not create a payable Fiscal
        Obligation, so it belongs to VAT Settings rather than
        Tax Account Configuration.
        """

        settings = frappe.get_doc(
            "VAT Settings",
            self.vat_settings,
        )

        account = settings.get(
            "vat_recoverable_account"
        )

        if not account:
            frappe.throw(
                _(
                    "VAT Recoverable Account must be "
                    "configured in VAT Settings."
                )
            )

        if (
            frappe.db.get_value(
                "Account",
                account,
                "company",
            )
            != self.company
        ):
            frappe.throw(
                _(
                    "VAT Recoverable Account must belong "
                    "to the VAT Period Company."
                )
            )

        mapping = next(
            (
                row
                for row in settings.account_mappings or []
                if (
                    row.active
                    and row.account == account
                )
            ),
            None,
        )

        if not mapping:
            frappe.throw(
                _(
                    "VAT Recoverable Account is not active "
                    "in VAT Account Mapping."
                )
            )

        if mapping.vat_nature != "IVA a Recuperar":
            frappe.throw(
                _(
                    "VAT Recoverable Account must have "
                    "VAT Nature = IVA a Recuperar."
                )
            )

        if mapping.modelo_7_side != "Control":
            frappe.throw(
                _(
                    "VAT Recoverable Account must have "
                    "Modelo 7 Side = Control."
                )
            )

        return account


    def validate_vat_closing_prerequisites(self):
        """
        Closing is accounting execution after VAT reconciliation.
        It is not another determination calculation.
        """

        if not self.vat_reconciliation:
            frappe.throw(
                _(
                    "A submitted VAT Reconciliation is "
                    "required before VAT closing."
                )
            )

        reconciliation = frappe.get_doc(
            "VAT Reconciliation",
            self.vat_reconciliation,
        )

        if (
            reconciliation.docstatus != 1
            or reconciliation.vat_period_control
                != self.name
            or not reconciliation.is_reconciled
        ):
            frappe.throw(
                _(
                    "VAT Reconciliation {0} must be "
                    "submitted and reconciled before "
                    "VAT closing."
                ).format(
                    reconciliation.name
                )
            )


        if self.docstatus != 0:
            frappe.throw(
                _(
                    "VAT closing must be created while "
                    "VAT Period Control is Draft."
                )
            )

        if not self.modelo_7_confirmed:
            frappe.throw(
                _(
                    "Modelo 7 must be confirmed before "
                    "VAT closing."
                )
            )

        if not self.is_reconciled:
            frappe.throw(
                _(
                    "VAT Period must be reconciled before "
                    "VAT closing."
                )
            )

        open_items = [
            row
            for row in self.reconciling_items or []
            if row.resolution_status == "Open"
        ]

        if open_items:
            frappe.throw(
                _(
                    "{0} VAT reconciling item(s) remain Open."
                ).format(
                    len(open_items)
                )
            )

        if self.closing_journal_entry:

            status = frappe.db.get_value(
                "Journal Entry",
                self.closing_journal_entry,
                "docstatus",
            )

            if status in (0, 1):
                frappe.throw(
                    _(
                        "VAT Closing Journal Entry {0} "
                        "already exists."
                    ).format(
                        self.closing_journal_entry
                    )
                )


    def create_vat_rounding_journal_entry(
        self,
        obligation,
        payable_config,
    ):
        """
        Post the statutory VAT rounding difference.

        Example:

            Raw VAT            50,000.25
            Legal payable      50,001.00
            Difference              0.75

            DR VAT Rounding       0.75
            CR IVA a Pagar        0.75

        The payable line is a Reclassification movement and does
        not modify the original tax determination.
        """

        difference = flt(
            obligation.rounding_difference,
            2,
        )

        if abs(
            difference
        ) < ZERO_TOLERANCE:

            return None

        if difference < 0:

            frappe.throw(
                _(
                    "VAT statutory rounding difference "
                    "cannot be negative under the current "
                    "ceiling rule."
                )
            )

        rounding_account = (
            self.get_vat_rounding_account()
        )

        payable_account = (
            payable_config.account
        )

        existing = frappe.get_all(
            "Journal Entry",
            filters={
                "company":
                    self.company,
                "docstatus":
                    ["!=", 2],
                "user_remark":
                    (
                        "VAT statutory rounding "
                        f"for {self.name}"
                    ),
            },
            pluck="name",
            limit_page_length=2,
        )

        if len(existing) > 1:

            frappe.throw(
                _(
                    "Multiple VAT rounding Journal "
                    "Entries exist for {0}."
                ).format(
                    self.name
                )
            )

        if existing:

            je = frappe.get_doc(
                "Journal Entry",
                existing[0],
            )

            if je.docstatus != 1:

                frappe.throw(
                    _(
                        "Existing VAT rounding Journal "
                        "Entry must be submitted."
                    )
                )

            return je.name

        je = frappe.get_doc(
            {
                "doctype":
                    "Journal Entry",

                "voucher_type":
                    "Journal Entry",

                "company":
                    self.company,

                "posting_date":
                    self.period_end,

                "user_remark":
                    (
                        "VAT statutory rounding "
                        f"for {self.name}"
                    ),

                "accounts": [
                    {
                        "account":
                            rounding_account,

                        "debit_in_account_currency":
                            difference,

                        "credit_in_account_currency":
                            0,

                        "user_remark":
                            (
                                "VAT statutory rounding "
                                f"{self.name}"
                            ),
                    },
                    {
                        "account":
                            payable_account,

                        "debit_in_account_currency":
                            0,

                        "credit_in_account_currency":
                            difference,

                        "user_remark":
                            (
                                "VAT statutory rounding "
                                f"{self.name}"
                            ),
                    },
                ],
            }
        )

        je.insert(
            ignore_permissions=True
        )

        from angola_tax.gl_ingestion import (
            submit_vat_rounding_journal,
        )

        submit_vat_rounding_journal(
            je=je,
            obligation=obligation.name,
            company=self.company,
            tax_type=payable_config.tax_type,
            vat_period=self.name,
            tax_account=payable_account,
        )

        obligation.reload()

        # Determination must remain exactly the original raw VAT.
        if abs(
            flt(
                obligation.amount_determined_raw,
                2,
            )
            - flt(
                self.accounting_net_position,
                2,
            )
        ) >= ZERO_TOLERANCE:

            frappe.throw(
                _(
                    "VAT rounding Journal Entry changed "
                    "the raw VAT determination. "
                    "This is not permitted."
                )
            )

        return je.name


    @frappe.whitelist()
    def create_vat_closing_journal_entry(self):
        """
        Clear the period's transactional VAT accounts into exactly
        one final control account:

        positive final position:
            VAT payable account resolved from
            Tax Account Configuration.

        negative final position:
            VAT recoverable account from VAT Settings.

        The closing JE itself is excluded from subsequent VAT
        determination for this period.
        """

        # Refresh accounting first so closing is based on the
        # latest reconciled GL, including any reconciliation JE.
        self.load_accounting()

        self.calculate_modelo_7_lines()
        self.set_declared_summary_from_modelo_7()
        self.calculate_reconciliation()

        self.validate_vat_closing_prerequisites()

        payable_config = (
            self.get_vat_payable_configuration()
        )

        payable_account = (
            payable_config.account
        )

        recoverable_account = (
            self.get_vat_recoverable_account()
        )

        if (
            payable_account
            == recoverable_account
        ):
            frappe.throw(
                _(
                    "VAT Payable Account and VAT Recoverable "
                    "Account cannot be the same account."
                )
            )

        settings = frappe.get_doc(
            "VAT Settings",
            self.vat_settings,
        )

        mappings = {
            row.account: row
            for row in settings.account_mappings or []
            if row.active
        }

        # ----------------------------------------------------
        # Aggregate only transactional declaration-side VAT
        # accounts.
        #
        # Control accounts are intentionally excluded:
        # - IVA a Pagar
        # - IVA a Recuperar
        # - VAT Reconciling Items
        # - customs timing/clearing accounts
        # ----------------------------------------------------

        balances = {}

        for row in self.get_vat_determination_transactions():

            mapping = mappings.get(
                row.account
            )

            if not mapping:
                continue

            if mapping.modelo_7_side not in (
                "State",
                "Taxpayer",
            ):
                continue

            balance = balances.setdefault(
                row.account,
                {
                    "debit": 0,
                    "credit": 0,
                },
            )

            balance["debit"] += flt(
                row.debit
            )

            balance["credit"] += flt(
                row.credit
            )

        accounts = []

        total_debit = 0
        total_credit = 0

        for account in sorted(balances):

            values = balances[account]

            # Actual period GL balance.
            net_debit = flt(
                flt(values["debit"])
                - flt(values["credit"]),
                2,
            )

            if abs(net_debit) < ZERO_TOLERANCE:
                continue

            # Reverse the transactional VAT movement.
            if net_debit > 0:

                accounts.append(
                    {
                        "account":
                            account,
                        "debit_in_account_currency":
                            0,
                        "credit_in_account_currency":
                            net_debit,
                        "user_remark":
                            (
                                "VAT monthly closing "
                                f"{self.name}"
                            ),
                    }
                )

                total_credit += net_debit

            else:

                amount = abs(
                    net_debit
                )

                accounts.append(
                    {
                        "account":
                            account,
                        "debit_in_account_currency":
                            amount,
                        "credit_in_account_currency":
                            0,
                        "user_remark":
                            (
                                "VAT monthly closing "
                                f"{self.name}"
                            ),
                    }
                )

                total_debit += amount

        if not accounts:
            frappe.throw(
                _(
                    "No transactional VAT balances exist "
                    "to close."
                )
            )

        # Net position implied by the closing reversals.
        closing_position = flt(
            total_debit
            - total_credit,
            2,
        )

        final_position = flt(
            self.accounting_net_position,
            2,
        )

        if abs(
            closing_position
            - final_position
        ) >= ZERO_TOLERANCE:
            frappe.throw(
                _(
                    "VAT closing position {0} does not match "
                    "the reconciled accounting position {1}."
                ).format(
                    closing_position,
                    final_position,
                )
            )

        declaration_position = flt(
            self.declaration_net_position,
            2,
        )

        if abs(
            final_position
            - declaration_position
        ) >= ZERO_TOLERANCE:
            frappe.throw(
                _(
                    "Accounting VAT position {0} does not match "
                    "Modelo 7 position {1}. "
                    "Reconcile the period before closing."
                ).format(
                    final_position,
                    declaration_position,
                )
            )

        # ----------------------------------------------------
        # Final counterpart
        # ----------------------------------------------------

        if final_position > ZERO_TOLERANCE:

            # Reversal lines carry net debit;
            # counterpart is credit to VAT payable.
            accounts.append(
                {
                    "account":
                        payable_account,
                    "debit_in_account_currency":
                        0,
                    "credit_in_account_currency":
                        final_position,
                    "user_remark":
                        (
                            "VAT payable after monthly "
                            f"closing {self.name}"
                        ),
                }
            )

            total_credit += final_position

        elif final_position < -ZERO_TOLERANCE:

            amount = abs(
                final_position
            )

            # Reversal lines carry net credit;
            # counterpart is debit to VAT recoverable.
            accounts.append(
                {
                    "account":
                        recoverable_account,
                    "debit_in_account_currency":
                        amount,
                    "credit_in_account_currency":
                        0,
                    "user_remark":
                        (
                            "VAT recoverable after monthly "
                            f"closing {self.name}"
                        ),
                }
            )

            total_debit += amount

        # Zero-position periods can still require a closing JE:
        # output VAT and input/cativo VAT clear each other.

        if abs(
            flt(
                total_debit
                - total_credit,
                2,
            )
        ) >= ZERO_TOLERANCE:
            frappe.throw(
                _(
                    "VAT Closing Journal Entry is not balanced. "
                    "Debit {0}; Credit {1}."
                ).format(
                    total_debit,
                    total_credit,
                )
            )

        je = frappe.get_doc(
            {
                "doctype":
                    "Journal Entry",
                "voucher_type":
                    "Journal Entry",
                "company":
                    self.company,
                "posting_date":
                    self.period_end,
                "user_remark":
                    (
                        "VAT monthly closing for "
                        f"{self.name}"
                    ),
                "accounts":
                    accounts,
            }
        )

        je.insert()

        obligation = None

        if final_position > ZERO_TOLERANCE:

            obligation = (
                self.get_or_create_vat_fiscal_obligation(
                    payable_config
                )
            )

            from angola_tax.gl_ingestion import (
                submit_vat_closing_journal,
            )

            submit_vat_closing_journal(
                je=je,
                obligation=obligation.name,
                company=self.company,
                tax_type=payable_config.tax_type,
                vat_period=self.name,
                tax_account=payable_account,
            )

            obligation.reload()

            obligation.journal_entry = (
                je.name
            )

            obligation.save(
                ignore_permissions=True
            )

            # Fiscal Obligation now owns the raw VAT
            # determination and statutory rounding difference.
            # Bring the accounting liability to the exact
            # legally payable rounded amount without changing
            # the tax determination.
            self.create_vat_rounding_journal_entry(
                obligation,
                payable_config,
            )

            self.fiscal_obligation = (
                obligation.name
            )

        else:

            # Zero and recoverable VAT positions do not create
            # a payable Fiscal Obligation.
            je.submit()

        self.closing_journal_entry = (
            je.name
        )

        self.save()

        return je.name


    def get_account_mapping(self):
        if not self.vat_settings:
            return {}

        settings = frappe.get_doc(
            "VAT Settings",
            self.vat_settings,
        )

        return {
            row.account: row
            for row in settings.account_mappings or []
            if row.active
        }

    def validate_reconciling_items(self):
        """
        Reconciling items that require a GL correction must have
        an affected VAT account.

        Corrected status requires a submitted Journal Entry.
        """

        for row in self.reconciling_items or []:

            if not row.requires_gl_adjustment:
                continue

            if not row.account:
                frappe.throw(
                    f"VAT Reconciling Item row {row.idx}: "
                    "Account is required when a GL adjustment "
                    "is required."
                )

            if row.resolution_status == "Corrected":

                if not row.journal_entry:
                    frappe.throw(
                        f"VAT Reconciling Item row {row.idx}: "
                        "Corrected status requires a Journal Entry."
                    )

                docstatus = frappe.db.get_value(
                    "Journal Entry",
                    row.journal_entry,
                    "docstatus",
                )

                if docstatus != 1:
                    frappe.throw(
                        f"VAT Reconciling Item row {row.idx}: "
                        f"Journal Entry {row.journal_entry} "
                        "must be submitted."
                    )


    def before_submit(self):
        if not self.transactions:
            frappe.throw(
                _("Load the accounting VAT movements before submission.")
            )

        if not self.modelo_7_confirmed:
            frappe.throw(
                _("Modelo 7 must be confirmed before submission.")
            )

        if not self.declaration_reference:
            frappe.throw(
                _("Declaration Reference is required before submission.")
            )

        if not self.submission_date:
            frappe.throw(
                _("Submission Date is required before submission.")
            )

        if not self.is_reconciled:
            frappe.throw(
                _(
                    "VAT Period is not reconciled. "
                    "Unexplained difference: {0}"
                ).format(
                    frappe.format_value(
                        self.unexplained_difference,
                        {"fieldtype": "Currency"},
                    )
                )
            )

        open_items = [
            row
            for row in self.reconciling_items or []
            if row.resolution_status == "Open"
        ]

        if open_items:
            frappe.throw(
                _(
                    "{0} VAT reconciling item(s) remain Open."
                ).format(len(open_items))
            )

        self.reviewed_by = frappe.session.user
        self.reviewed_on = now()

    def before_update_after_submit(self):
        stored = frappe.get_doc(
            "VAT Period Control",
            self.name,
        )

        locked_fields = (
            "company",
            "vat_settings",
            "period_start",
            "period_end",
            "accounting_state_amount",
            "accounting_taxpayer_amount",
            "accounting_net_position",
            "declared_payable",
            "declared_credit",
            "declaration_net_position",
        )

        for fieldname in locked_fields:
            old = stored.get(fieldname)
            new = self.get(fieldname)

            if str(old or "") != str(new or ""):
                frappe.throw(
                    _(
                        "Submitted VAT Period cannot change {0}. "
                        "Cancel/amend the period or use the formal "
                        "correction process."
                    ).format(
                        self.meta.get_label(fieldname)
                    )
                )

    def before_cancel(self):
        if self.fiscal_obligation:
            frappe.throw(
                _(
                    "Cannot cancel VAT Period while Fiscal Obligation "
                    "{0} is linked."
                ).format(self.fiscal_obligation)
            )

    def load_accounting(self):
        if self.docstatus != 0:
            frappe.throw(
                _("Accounting can only be loaded into a Draft VAT Period.")
            )

        if not self.vat_settings:
            frappe.throw(
                _("VAT Settings are required.")
            )

        settings = frappe.get_doc(
            "VAT Settings",
            self.vat_settings,
        )

        mappings = {
            row.account: row
            for row in settings.account_mappings or []
            if row.active
        }

        if not mappings:
            frappe.throw(
                _("No active VAT account mappings are configured.")
            )

        accounts = list(mappings)

        entries = frappe.get_all(
            "GL Entry",
            filters={
                "company": self.company,
                "posting_date": [
                    "between",
                    [self.period_start, self.period_end],
                ],
                "account": ["in", accounts],
                "is_cancelled": 0,
            },
            fields=[
                "name",
                "posting_date",
                "account",
                "voucher_type",
                "voucher_no",
                "party_type",
                "party",
                "debit",
                "credit",
                "is_opening",
                "remarks",
            ],
            order_by=(
                "posting_date, voucher_type, "
                "voucher_no, account, name"
            ),
            limit_page_length=0,
        )

        self.set("transactions", [])

        for entry in entries:

            # VAT closing is a balance-sheet transfer performed
            # after reconciliation. It must never feed back into
            # the VAT determination for the same period.
            if (
                self.closing_journal_entry
                and entry.voucher_type == "Journal Entry"
                and entry.voucher_no == self.closing_journal_entry
            ):
                continue

            config = mappings[entry.account]

            row = self.append(
                "transactions",
                {
                    "posting_date": entry.posting_date,
                    "transaction_type":
                        self.get_transaction_type(entry),
                    "vat_nature": config.vat_nature,
                    "voucher_type": entry.voucher_type,
                    "voucher_no": entry.voucher_no,
                    "party_type": entry.party_type,
                    "party": entry.party,
                    "tax_id":
                        self.get_party_tax_id(
                            entry.party_type,
                            entry.party,
                        ),
                    "account": entry.account,
                    "debit": entry.debit,
                    "credit": entry.credit,
                    "gl_entry": entry.name,
                    "classification_status":
                        "Accounting Only",
                    "remarks": entry.remarks,
                },
            )

            if config.modelo_7_side == "State":
                row.vat_amount = (
                    flt(entry.credit)
                    - flt(entry.debit)
                )

            elif config.modelo_7_side == "Taxpayer":
                row.vat_amount = (
                    flt(entry.debit)
                    - flt(entry.credit)
                )

            else:
                row.vat_amount = (
                    flt(entry.credit)
                    - flt(entry.debit)
                )

        self.calculate_transaction_totals()
        self.build_declaration_details()
        self.seed_modelo_7_control_lines()
        self.calculate_modelo_7_lines()
        self.set_declared_summary_from_modelo_7()
        self.calculate_reconciliation()


    def build_declaration_details(self):
        """
        Build declarative VAT detail from posted accounting sources.

        Accounting remains authoritative for VAT amount.
        Source documents provide declaration classification.
        """
        self.set("declaration_details", [])

        processed_sales = set()
        processed_purchases = set()

        for transaction in self.transactions or []:

            if (
                transaction.voucher_type == "Sales Invoice"
                and transaction.voucher_no
            ):
                key = transaction.voucher_no

                if key not in processed_sales:
                    self.build_sales_invoice_declaration_details(
                        transaction.voucher_no
                    )
                    processed_sales.add(key)

            elif (
                transaction.voucher_type == "Purchase Invoice"
                and transaction.voucher_no
            ):
                key = transaction.voucher_no

                if key not in processed_purchases:
                    self.build_purchase_invoice_declaration_details(
                        transaction.voucher_no
                    )
                    processed_purchases.add(key)

        # Customs/import VAT is not inferred from Purchase Invoice.
        # It has its own statutory/accounting control document.
        self.build_import_vat_declaration_details()


    def build_sales_invoice_declaration_details(
        self,
        voucher_no,
    ):
        """
        Build sales-side VAT declaration detail.

        Accounting remains authoritative:
        - IVA Liquidado is read from the posted VAT GL transaction;
        - IVA Cativo suffered is read from the posted VAT GL transaction.

        Sales Invoice item/tax detail is used only to distribute those
        accounting totals into declaration lines.
        """

        if not frappe.db.exists(
            "Sales Invoice",
            voucher_no,
        ):
            return

        invoice = frappe.get_doc(
            "Sales Invoice",
            voucher_no,
        )

        if invoice.docstatus != 1:
            return

        if invoice.is_return:
            return self.build_sales_return_declaration_details(
                invoice
            )

        # ----------------------------------------------------
        # ACCOUNTING AMOUNTS
        # ----------------------------------------------------

        output_amount = 0
        cativo_amount = 0

        for transaction in self.transactions or []:

            if (
                transaction.voucher_type != "Sales Invoice"
                or transaction.voucher_no != voucher_no
            ):
                continue

            account = (
                transaction.account
                or ""
            ).lower()

            debit = flt(
                transaction.debit
            )

            credit = flt(
                transaction.credit
            )

            # IVA Liquidado:
            # normal accounting direction is credit.
            if (
                "liquidado" in account
                and "cativo" not in account
            ):
                output_amount += (
                    credit - debit
                )

            # IVA Cativo suffered:
            # normal accounting direction is debit.
            elif "cativo" in account:
                cativo_amount += (
                    debit - credit
                )

        output_amount = flt(
            max(output_amount, 0),
            2,
        )

        cativo_amount = flt(
            max(cativo_amount, 0),
            2,
        )

        # ----------------------------------------------------
        # OUTPUT VAT
        # ----------------------------------------------------

        if output_amount > 0:

            breakdown = (
                self.get_sales_tax_breakdown(
                    invoice,
                    output_amount,
                    cativo=False,
                )
            )

            for row in breakdown:

                self.append(
                    "declaration_details",
                    {
                        "source_document_type":
                            "Sales Invoice",
                        "source_document":
                            voucher_no,
                        "source_item":
                            row.get("item_code"),
                        "item_name":
                            row.get("item_name"),
                        "party":
                            invoice.customer,
                        "taxable_base":
                            flt(
                                row.get(
                                    "taxable_base"
                                ),
                                2,
                            ),
                        "vat_rate":
                            flt(
                                row.get(
                                    "vat_rate"
                                ),
                                6,
                            ),
                        "vat_amount":
                            flt(
                                row.get(
                                    "vat_amount"
                                ),
                                2,
                            ),
                        "declaration_component":
                            "Output VAT",
                        "classification_status":
                            "Derived",
                        "remarks":
                            "Sales VAT from posted accounting",
                    },
                )

        # ----------------------------------------------------
        # VAT CATIVO SUFFERED
        # ----------------------------------------------------

        if cativo_amount > 0:

            breakdown = (
                self.get_sales_tax_breakdown(
                    invoice,
                    cativo_amount,
                    cativo=True,
                )
            )

            cativo_percentage = (
                flt(
                    cativo_amount
                    / output_amount
                    * 100,
                    6,
                )
                if output_amount
                else 0
            )

            for row in breakdown:

                self.append(
                    "declaration_details",
                    {
                        "source_document_type":
                            "Sales Invoice",
                        "source_document":
                            voucher_no,
                        "source_item":
                            row.get("item_code"),
                        "item_name":
                            row.get("item_name"),
                        "party":
                            invoice.customer,
                        "taxable_base":
                            flt(
                                row.get(
                                    "taxable_base"
                                ),
                                2,
                            ),
                        "vat_rate":
                            flt(
                                row.get(
                                    "vat_rate"
                                ),
                                6,
                            ),
                        "vat_amount":
                            flt(
                                row.get(
                                    "vat_amount"
                                ),
                                2,
                            ),
                        "cativo_percentage":
                            cativo_percentage,
                        "declaration_component":
                            "Cativo Suffered",
                        "classification_status":
                            "Derived",
                        "remarks":
                            "VAT cativo suffered from posted accounting",
                    },
                )


    def build_purchase_invoice_declaration_details(
        self,
        voucher_no,
    ):
        """
        Build purchase-side VAT declaration detail.

        Rules:
        - VAT amount comes from mapped GL VAT transactions.
        - Purchase Invoice tax data is used only for allocation.
        - Purchase classification comes from VAT Settings.
        """
        if not frappe.db.exists(
            "Purchase Invoice",
            voucher_no,
        ):
            return

        invoice = frappe.get_doc(
            "Purchase Invoice",
            voucher_no,
        )

        if invoice.docstatus != 1:
            return


        if invoice.is_return:
            return self.build_purchase_return_declaration_details(
                invoice
            )

        vat_transactions = [
            row
            for row in self.transactions or []
            if (
                row.voucher_type == "Purchase Invoice"
                and row.voucher_no == voucher_no
                and flt(row.debit) > 0
            )
        ]

        if not vat_transactions:
            return

        expected_amount = flt(
            sum(
                flt(row.debit) - flt(row.credit)
                for row in vat_transactions
            ),
            2,
        )

        if expected_amount <= 0:
            return

        breakdown = self.get_purchase_tax_breakdown(
            invoice,
            expected_amount,
        )

        for row in breakdown:

            classification = (
                self.get_purchase_vat_classification(
                    row.get("item_code"),
                    row.get("item_group"),
                    invoice.posting_date,
                )
            )

            if classification:
                modelo_field = (
                    f"{classification['base_field']} / "
                    f"{classification['vat_field']}"
                )
                status = "Derived"
            else:
                modelo_field = None
                status = "Pending Modelo 7 Mapping"

            self.append(
                "declaration_details",
                {
                    "source_document_type":
                        "Purchase Invoice",
                    "source_document":
                        voucher_no,
                    "source_item":
                        row.get("item_code"),
                    "item_name":
                        row.get("item_name"),
                    "party":
                        invoice.supplier,
                    "taxable_base":
                        flt(row.get("taxable_base"), 2),
                    "vat_rate":
                        flt(row.get("vat_rate"), 6),
                    "vat_amount":
                        flt(row.get("vat_amount"), 2),
                    "declaration_component":
                        "Input VAT",
                    "modelo_7_field":
                        modelo_field,
                    "classification_status":
                        status,
                    "remarks":
                        (
                            classification["category"]
                            if classification
                            else (
                                "No VAT purchase classification "
                                "found for Item / Item Group."
                            )
                        ),
                },
            )


    def build_purchase_return_declaration_details(
        self,
        invoice,
    ):
        """
        Supplier credit note / Purchase Invoice return.

        Normal input VAT accounting:
            DR IVA Suportado / IVA Dedutivel.

        Return accounting:
            CR IVA Suportado / IVA Dedutivel

        This reduces deductible VAT and therefore produces a
        taxpayer regularisation in favour of the State:
            Modelo 7 field 29.

        Posted GL remains authoritative.
        """

        vat_transactions = []

        for row in self.transactions or []:

            if (
                row.voucher_type
                != "Purchase Invoice"
                or row.voucher_no
                != invoice.name
            ):
                continue

            reversal = (
                flt(row.credit)
                - flt(row.debit)
            )

            if reversal > 0:
                vat_transactions.append(
                    row
                )

        if not vat_transactions:
            return

        expected_amount = flt(
            sum(
                flt(row.credit)
                - flt(row.debit)
                for row in vat_transactions
            ),
            2,
        )

        if expected_amount <= 0:
            return

        breakdown = self.get_purchase_tax_breakdown(
            invoice,
            expected_amount,
        )

        for row in breakdown:

            self.append(
                "declaration_details",
                {
                    "source_document_type":
                        "Purchase Invoice",
                    "source_document":
                        invoice.name,
                    "source_item":
                        row.get("item_code"),
                    "item_name":
                        row.get("item_name"),
                    "party":
                        invoice.supplier,
                    "taxable_base":
                        abs(
                            flt(
                                row.get(
                                    "taxable_base"
                                ),
                                2,
                            )
                        ),
                    "vat_rate":
                        abs(
                            flt(
                                row.get(
                                    "vat_rate"
                                ),
                                6,
                            )
                        ),
                    "vat_amount":
                        abs(
                            flt(
                                row.get(
                                    "vat_amount"
                                ),
                                2,
                            )
                        ),
                    "declaration_component":
                        "Supplier Regularisation",
                    "modelo_7_field":
                        "29",
                    "classification_status":
                        "Derived",
                    "remarks":
                        (
                            "Purchase return / supplier credit note; "
                            "reversal of deductible input VAT."
                        ),
                },
            )


    def get_purchase_tax_breakdown(
        self,
        invoice,
        expected_amount,
    ):
        """
        Allocate GL-backed input VAT across taxable Purchase Invoice items.

        Accounting rule:
        - mapped GL VAT is the authoritative monetary amount.

        Tax classification rule:
        - Purchase Invoice Item determines exemption / VAT rate;
        - Item master is used only as fallback;
        - exempt items never receive deductible VAT;
        - ERPNext item_wise_tax_detail is allocation metadata only
          and cannot override an explicit exemption.
        """

        item_tax_signals = {}

        # ----------------------------------------------------
        # READ ERPNext ITEM-WISE TAX INFORMATION
        # ----------------------------------------------------
        for tax in invoice.taxes or []:

            item_detail = {}

            if tax.item_wise_tax_detail:
                try:
                    item_detail = json.loads(
                        tax.item_wise_tax_detail
                    )
                except Exception:
                    item_detail = {}

            for item_code, values in (
                item_detail.items()
                if item_detail
                else []
            ):

                if not isinstance(
                    values,
                    (list, tuple),
                ):
                    continue

                rate = (
                    abs(flt(values[0]))
                    if len(values) > 0
                    else 0
                )

                amount = (
                    abs(flt(values[1]))
                    if len(values) > 1
                    else 0
                )

                signal = item_tax_signals.setdefault(
                    item_code,
                    {
                        "rate": 0,
                        "amount": 0,
                    },
                )

                # Keep an explicit non-zero rate where available.
                if rate:
                    signal["rate"] = rate

                signal["amount"] = (
                    flt(signal["amount"])
                    + flt(amount)
                )

        taxable_items = []

        # ----------------------------------------------------
        # IDENTIFY TAXABLE ITEMS
        # ----------------------------------------------------
        for item in invoice.items or []:

            item_code = item.item_code

            if not item_code:
                continue

            base = flt(
                item.net_amount,
                2,
            )

            if not base:
                continue

            # Purchase Invoice Item is first authority for
            # transactional tax treatment.
            exempt = bool(
                item.get("isento_iva")
            )

            line_rate = abs(
                flt(
                    item.get("iva")
                    or 0
                )
            )

            # Item master is only fallback where invoice line
            # itself does not carry the custom fields/value.
            if not exempt:
                master_exempt = frappe.db.get_value(
                    "Item",
                    item_code,
                    "iva_isencao",
                )

                exempt = bool(
                    master_exempt
                )

            if exempt:
                # Explicitly exempt acquisitions do not receive
                # deductible VAT and are not included in fields
                # 16/18/20/22 of the deductible-purchase section.
                continue

            signal = item_tax_signals.get(
                item_code,
                {},
            )

            signal_rate = abs(
                flt(
                    signal.get("rate")
                    or 0
                )
            )

            rate = (
                line_rate
                or signal_rate
            )

            signal_amount = abs(
                flt(
                    signal.get("amount")
                    or 0
                )
            )

            # Prefer statutory/configured VAT rate as allocation
            # weight. If unavailable, retain valid ERPNext tax
            # allocation metadata. Final amount is still forced
            # back to GL.
            if rate:
                allocation_weight = (
                    flt(base)
                    * flt(rate)
                    / 100
                )
            elif signal_amount:
                allocation_weight = (
                    signal_amount
                )
            else:
                allocation_weight = 0

            taxable_items.append(
                {
                    "item_code":
                        item_code,
                    "item_name":
                        item.item_name,
                    "item_group":
                        item.item_group,
                    "taxable_base":
                        base,
                    "configured_rate":
                        rate,
                    "allocation_weight":
                        allocation_weight,
                }
            )

        if not taxable_items:
            return []

        # ----------------------------------------------------
        # ALLOCATE GL VAT
        # ----------------------------------------------------
        total_weight = sum(
            flt(row["allocation_weight"])
            for row in taxable_items
        )

        # Conservative fallback:
        # if no tax-rate information survives, allocate only
        # among non-exempt items by taxable base.
        if not total_weight:
            total_weight = sum(
                flt(row["taxable_base"])
                for row in taxable_items
            )

            for row in taxable_items:
                row["allocation_weight"] = (
                    flt(row["taxable_base"])
                )

        results = []

        for row in taxable_items:

            vat_amount = flt(
                flt(expected_amount)
                * flt(row["allocation_weight"])
                / flt(total_weight),
                2,
            )

            effective_rate = (
                flt(
                    flt(vat_amount)
                    / flt(row["taxable_base"])
                    * 100,
                    6,
                )
                if flt(row["taxable_base"])
                else 0
            )

            results.append(
                {
                    "item_code":
                        row["item_code"],
                    "item_name":
                        row["item_name"],
                    "item_group":
                        row["item_group"],
                    "taxable_base":
                        flt(
                            row["taxable_base"],
                            2,
                        ),
                    "vat_rate":
                        effective_rate,
                    "vat_amount":
                        vat_amount,
                }
            )

        # Exact cent reconciliation to mapped GL VAT.
        delta = flt(
            flt(expected_amount)
            - sum(
                flt(row["vat_amount"])
                for row in results
            ),
            2,
        )

        if results:
            results[-1]["vat_amount"] = flt(
                flt(
                    results[-1]["vat_amount"]
                )
                + delta,
                2,
            )

            # Recalculate last effective rate after cent adjustment.
            if flt(
                results[-1]["taxable_base"]
            ):
                results[-1]["vat_rate"] = flt(
                    flt(
                        results[-1]["vat_amount"]
                    )
                    / flt(
                        results[-1]["taxable_base"]
                    )
                    * 100,
                    6,
                )

        # ----------------------------------------------------
        # HARD CONTROL
        # ----------------------------------------------------
        allocated_total = flt(
            sum(
                flt(row["vat_amount"])
                for row in results
            ),
            2,
        )

        if abs(
            allocated_total
            - flt(expected_amount, 2)
        ) >= 0.01:
            frappe.throw(
                f"Purchase Invoice {invoice.name}: "
                f"Input VAT detail {allocated_total} "
                f"does not reconcile to mapped GL VAT "
                f"{flt(expected_amount, 2)}."
            )

        return results


    def build_import_vat_declaration_details(self):
        """
        Include customs VAT recognised from ASYCUDA / AGT evidence.

        Customs recognition:
            DR VAT Customs Recoverable
            CR VAT Customs Clearing

        The freight forwarder debit note may arrive later and does
        not determine the VAT declaration period.
        """

        imports = frappe.get_all(
            "VAT Import Document",
            filters={
                "company": self.company,
                "customs_date": [
                    "between",
                    [
                        self.period_start,
                        self.period_end,
                    ],
                ],
                "recognition_journal_entry":
                    ["is", "set"],
            },
            pluck="name",
            limit_page_length=0,
        )

        for name in imports:

            import_doc = frappe.get_doc(
                "VAT Import Document",
                name,
            )

            if not import_doc.customs_recognition_is_valid():
                continue

            base = flt(
                import_doc.import_taxable_base,
                2,
            )

            vat = flt(
                import_doc.customs_vat_amount,
                2,
            )

            rate = (
                flt(
                    vat / base * 100,
                    6,
                )
                if base
                else 0
            )

            self.append(
                "declaration_details",
                {
                    "source_document_type":
                        "VAT Import Document",
                    "source_document":
                        import_doc.name,
                    "party":
                        import_doc.freight_forwarder,
                    "taxable_base":
                        base,
                    "vat_rate":
                        rate,
                    "vat_amount":
                        vat,
                    "declaration_component":
                        "Import VAT",
                    "modelo_7_field":
                        "24 / 25",
                    "classification_status":
                        "Derived",
                    "remarks":
                        (
                            "Customs / ASYCUDA: "
                            + import_doc.customs_reference
                        ),
                },
            )


    def get_purchase_vat_classification(
        self,
        item_code,
        item_group,
        posting_date,
    ):
        """
        Resolve VAT purchase classification.

        Precedence:
        1. Item Override
        2. Exact Item Group
        3. All Item Groups fallback
        4. Unclassified
        """
        settings_name = (
            self.vat_settings
            if self.meta.has_field("vat_settings")
            and self.vat_settings
            else frappe.db.get_value(
                "VAT Settings",
                {
                    "company": self.company,
                    "active": 1,
                },
                "name",
            )
        )

        if not settings_name:
            return None

        settings = frappe.get_doc(
            "VAT Settings",
            settings_name,
        )

        valid_rows = []

        for row in (
            settings.purchase_classifications
            or []
        ):

            if not row.active:
                continue

            if (
                row.effective_from
                and posting_date < row.effective_from
            ):
                continue

            if (
                row.effective_to
                and posting_date > row.effective_to
            ):
                continue

            valid_rows.append(row)

        # 1. Item Override
        for row in valid_rows:
            if (
                row.item
                and row.item == item_code
            ):
                return {
                    "category":
                        row.modelo_7_category,
                    "base_field":
                        row.modelo_7_base_field,
                    "vat_field":
                        row.modelo_7_vat_field,
                }

        # 2. Exact Item Group
        for row in valid_rows:
            if (
                not row.item
                and row.item_group
                and row.item_group == item_group
                and row.item_group
                    != "All Item Groups"
            ):
                return {
                    "category":
                        row.modelo_7_category,
                    "base_field":
                        row.modelo_7_base_field,
                    "vat_field":
                        row.modelo_7_vat_field,
                }

        # 3. All Item Groups fallback
        for row in valid_rows:
            if (
                not row.item
                and row.item_group
                    == "All Item Groups"
            ):
                return {
                    "category":
                        row.modelo_7_category,
                    "base_field":
                        row.modelo_7_base_field,
                    "vat_field":
                        row.modelo_7_vat_field,
                }

        return None


    def build_sales_return_declaration_details(
        self,
        invoice,
    ):
        """
        Sales credit note / return regularisation.

        Normal sales accounting:
            CR IVA Liquidado
            DR IVA Cativo a Recuperar, where applicable.

        Return accounting reverses those directions:
            DR IVA Liquidado
                -> regularisation in favour of taxpayer
                -> Modelo 7 field 28

            CR IVA Cativo a Recuperar
                -> reduction of taxpayer-side cativo
                -> Modelo 7 field 27

        Posted GL remains the monetary source of truth.
        """

        output_reversal = 0
        cativo_reversal = 0

        for transaction in self.transactions or []:

            if (
                transaction.voucher_type
                != "Sales Invoice"
                or transaction.voucher_no
                != invoice.name
            ):
                continue

            account = (
                transaction.account
                or ""
            ).lower()

            debit = flt(
                transaction.debit
            )

            credit = flt(
                transaction.credit
            )

            if (
                "liquidado" in account
                and "cativo" not in account
            ):
                # Return direction:
                # DR IVA Liquidado.
                output_reversal += (
                    debit - credit
                )

            elif "cativo" in account:
                # Original cativo suffered is debit.
                # Return/reversal is therefore credit.
                cativo_reversal += (
                    credit - debit
                )

        output_reversal = flt(
            max(
                output_reversal,
                0,
            ),
            2,
        )

        cativo_reversal = flt(
            max(
                cativo_reversal,
                0,
            ),
            2,
        )

        # ----------------------------------------------------
        # OUTPUT VAT REVERSAL -> FIELD 28
        # ----------------------------------------------------

        if output_reversal > 0:

            breakdown = self.get_sales_tax_breakdown(
                invoice,
                output_reversal,
                cativo=False,
            )

            for row in breakdown:

                self.append(
                    "declaration_details",
                    {
                        "source_document_type":
                            "Sales Invoice",
                        "source_document":
                            invoice.name,
                        "source_item":
                            row.get("item_code"),
                        "item_name":
                            row.get("item_name"),
                        "party":
                            invoice.customer,
                        "taxable_base":
                            abs(
                                flt(
                                    row.get(
                                        "taxable_base"
                                    ),
                                    2,
                                )
                            ),
                        "vat_rate":
                            abs(
                                flt(
                                    row.get(
                                        "vat_rate"
                                    ),
                                    6,
                                )
                            ),
                        "vat_amount":
                            abs(
                                flt(
                                    row.get(
                                        "vat_amount"
                                    ),
                                    2,
                                )
                            ),
                        "declaration_component":
                            "Customer Regularisation",
                        "modelo_7_field":
                            "28",
                        "classification_status":
                            "Derived",
                        "remarks":
                            (
                                "Sales return / credit note; "
                                "reversal of output VAT."
                            ),
                    },
                )

        # ----------------------------------------------------
        # CATIVO REVERSAL -> FIELD 27
        # ----------------------------------------------------

        if cativo_reversal > 0:

            breakdown = self.get_sales_tax_breakdown(
                invoice,
                cativo_reversal,
                cativo=True,
            )

            for row in breakdown:

                self.append(
                    "declaration_details",
                    {
                        "source_document_type":
                            "Sales Invoice",
                        "source_document":
                            invoice.name,
                        "source_item":
                            row.get("item_code"),
                        "item_name":
                            row.get("item_name"),
                        "party":
                            invoice.customer,
                        "taxable_base":
                            abs(
                                flt(
                                    row.get(
                                        "taxable_base"
                                    ),
                                    2,
                                )
                            ),
                        "vat_rate":
                            abs(
                                flt(
                                    row.get(
                                        "vat_rate"
                                    ),
                                    6,
                                )
                            ),
                        "vat_amount":
                            abs(
                                flt(
                                    row.get(
                                        "vat_amount"
                                    ),
                                    2,
                                )
                            ),
                        "declaration_component":
                            "Cativo Regularisation",
                        "modelo_7_field":
                            "27",
                        "classification_status":
                            "Derived",
                        "remarks":
                            (
                                "Sales return / credit note; "
                                "reversal of VAT cativo suffered."
                            ),
                    },
                )


    def get_sales_tax_breakdown(
        self,
        invoice,
        expected_amount,
        cativo=False,
    ):
        """
        Read ERPNext item-wise tax detail where available.

        For legacy/custom 'Actual' tax rows with no structured
        item-wise allocation, allocate against invoice items
        proportionally by net amount solely for declaration detail.
        The GL amount remains authoritative.
        """
        candidates = []

        for tax in invoice.taxes or []:
            account = tax.account_head or ""

            if cativo:
                is_candidate = (
                    "cativo" in account.lower()
                    or "cativo" in (tax.description or "").lower()
                )
            else:
                is_candidate = (
                    "liquidado" in account.lower()
                    and "cativo" not in account.lower()
                )

            if not is_candidate:
                continue

            item_detail = {}

            if tax.item_wise_tax_detail:
                try:
                    item_detail = json.loads(
                        tax.item_wise_tax_detail
                    )
                except Exception:
                    item_detail = {}

            if item_detail:
                for item_code, values in item_detail.items():
                    if not isinstance(values, (list, tuple)):
                        continue

                    rate = flt(values[0]) if len(values) > 0 else 0
                    amount = flt(values[1]) if len(values) > 1 else 0

                    item_row = next(
                        (
                            item
                            for item in invoice.items
                            if item.item_code == item_code
                        ),
                        None,
                    )

                    taxable_base = (
                        flt(item_row.net_amount)
                        if item_row
                        else 0
                    )

                    # Some legacy/custom ERPNext tax rows use
                    # charge_type = Actual and store rate = 0 even
                    # though the item-wise VAT amount is valid.
                    #
                    # In that case derive the effective rate only as
                    # declaration metadata. The posted VAT amount
                    # remains the accounting source of truth.
                    effective_rate = abs(rate)

                    if (
                        not effective_rate
                        and taxable_base
                        and amount
                    ):
                        effective_rate = abs(
                            flt(amount)
                            / flt(taxable_base)
                            * 100
                        )

                    candidates.append(
                        {
                            "item_code": item_code,
                            "item_name":
                                item_row.item_name
                                if item_row
                                else item_code,
                            "taxable_base": flt(
                                taxable_base,
                                2,
                            ),
                            "vat_rate": flt(
                                effective_rate,
                                6,
                            ),
                            "vat_amount": abs(
                                flt(amount, 2)
                            ),
                        }
                    )

        if candidates:
            total = sum(
                flt(row["vat_amount"])
                for row in candidates
            )

            if total:
                factor = (
                    flt(expected_amount) / total
                    if expected_amount
                    else 1
                )

                for row in candidates:
                    row["vat_amount"] = flt(
                        flt(row["vat_amount"]) * factor,
                        2,
                    )

                # Currency rounding must still reconcile exactly
                # to the posted GL amount.
                delta = flt(
                    flt(expected_amount)
                    - sum(
                        flt(row["vat_amount"])
                        for row in candidates
                    ),
                    2,
                )

                if candidates:
                    candidates[-1]["vat_amount"] = flt(
                        flt(candidates[-1]["vat_amount"])
                        + delta,
                        2,
                    )

                # Recalculate effective rate after any proportional
                # scaling/rounding where the source row carried 0%.
                for row in candidates:
                    if (
                        not flt(row["vat_rate"])
                        and flt(row["taxable_base"])
                    ):
                        row["vat_rate"] = flt(
                            flt(row["vat_amount"])
                            / flt(row["taxable_base"])
                            * 100,
                            6,
                        )

            return candidates

        # Fallback for custom / Actual rows where ERPNext does
        # not provide a usable structured item-wise breakdown.
        total_base = sum(
            flt(item.net_amount)
            for item in invoice.items or []
        )

        if not total_base:
            return []

        results = []

        for item in invoice.items or []:
            base = flt(item.net_amount)

            allocated = flt(
                flt(expected_amount)
                * base
                / total_base,
                2,
            )

            rate = (
                flt(
                    abs(allocated / base * 100),
                    6,
                )
                if base
                else 0
            )

            results.append(
                {
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "taxable_base": flt(base, 2),
                    "vat_rate": rate,
                    "vat_amount": abs(allocated),
                }
            )

        # Force final cent reconciliation onto last line.
        if results:
            delta = (
                flt(expected_amount)
                - sum(
                    flt(row["vat_amount"])
                    for row in results
                )
            )

            results[-1]["vat_amount"] = flt(
                flt(results[-1]["vat_amount"])
                + flt(delta),
                2,
            )

        return results

    def get_transaction_type(self, entry):
        if entry.voucher_type == "Sales Invoice":
            is_return = frappe.db.get_value(
                "Sales Invoice",
                entry.voucher_no,
                "is_return",
            )

            return (
                "Sales Credit Note"
                if is_return
                else "Sales"
            )

        if entry.voucher_type == "Purchase Invoice":
            is_return = frappe.db.get_value(
                "Purchase Invoice",
                entry.voucher_no,
                "is_return",
            )

            return (
                "Purchase Credit Note"
                if is_return
                else "Purchase"
            )

        if entry.voucher_type == "Journal Entry":
            return "Journal Entry"

        return "Other"

    def get_party_tax_id(self, party_type, party):
        if not party_type or not party:
            return None

        if party_type not in ("Customer", "Supplier"):
            return None

        return frappe.db.get_value(
            party_type,
            party,
            "tax_id",
        )

    def seed_modelo_7_control_lines(self):
        """
        Build the Modelo 7 source position from VAT Declaration Detail.

        Phase 1 scope:
        - ordinary 14% sales
        - VAT cativo suffered by the taxpayer

        Other rates and transaction classes remain pending until their
        source and declaration rules are individually validated.
        """

        # Preserve any AGT/declaration values already entered manually.
        declared = {
            row.field_code: flt(row.declared_amount)
            for row in self.modelo_7_lines or []
        }

        amounts = {}

        def add(code, amount):
            amounts[code] = (
                flt(amounts.get(code))
                + flt(amount)
            )

        for row in self.declaration_details or []:

            # ------------------------------------------------------
            # NORMAL OUTPUT VAT - 14%
            # ------------------------------------------------------
            if row.declaration_component == "Output VAT":

                if abs(flt(row.vat_rate) - 14) < 0.01:
                    add("1", row.taxable_base)
                    add("2", row.vat_amount)

                    row.modelo_7_field = "1 / 2"
                    row.classification_status = "Derived"

                else:
                    # Do not guess reduced-rate treatment yet.
                    row.modelo_7_field = None
                    row.classification_status = (
                        "Pending Modelo 7 Mapping"
                    )

            # ------------------------------------------------------
            # VAT CATIVO SUFFERED
            # ------------------------------------------------------
            elif row.declaration_component == "Cativo Suffered":

                add("8", row.taxable_base)
                add("9", row.vat_amount)

                row.modelo_7_field = "8 / 9"
                row.classification_status = "Derived"

            # ------------------------------------------------------
            # INPUT VAT
            # ------------------------------------------------------
            elif row.declaration_component == "Input VAT":

                if not row.modelo_7_field:
                    row.classification_status = (
                        "Pending Modelo 7 Mapping"
                    )
                    continue

                try:
                    base_field, vat_field = [
                        part.strip()
                        for part in row.modelo_7_field.split("/")
                    ]
                except Exception:
                    row.classification_status = (
                        "Pending Modelo 7 Mapping"
                    )
                    continue

                if (
                    base_field not in (
                        "16",
                        "18",
                        "20",
                        "22",
                    )
                    or vat_field not in (
                        "17",
                        "19",
                        "21",
                        "23",
                    )
                ):
                    row.classification_status = (
                        "Pending Modelo 7 Mapping"
                    )
                    continue

                add(
                    base_field,
                    row.taxable_base,
                )

                add(
                    vat_field,
                    row.vat_amount,
                )

                row.classification_status = "Derived"

            # ------------------------------------------------------
            # IMPORT VAT
            # ------------------------------------------------------
            elif row.declaration_component == "Import VAT":

                add(
                    "24",
                    row.taxable_base,
                )

                add(
                    "25",
                    row.vat_amount,
                )

                row.modelo_7_field = "24 / 25"
                row.classification_status = "Derived"


            # ------------------------------------------------------
            # CATIVO REGULARISATION
            # ------------------------------------------------------
            elif (
                row.declaration_component
                == "Cativo Regularisation"
            ):

                if row.modelo_7_field == "26":
                    add(
                        "26",
                        row.vat_amount,
                    )

                elif row.modelo_7_field == "27":
                    add(
                        "27",
                        row.vat_amount,
                    )

                else:
                    row.classification_status = (
                        "Pending Modelo 7 Mapping"
                    )
                    continue

                row.classification_status = "Derived"

            # ------------------------------------------------------
            # CUSTOMER REGULARISATION
            # ------------------------------------------------------
            elif (
                row.declaration_component
                == "Customer Regularisation"
            ):

                if row.modelo_7_field != "28":
                    row.classification_status = (
                        "Pending Modelo 7 Mapping"
                    )
                    continue

                add(
                    "28",
                    row.vat_amount,
                )

                row.classification_status = "Derived"

            # ------------------------------------------------------
            # SUPPLIER REGULARISATION
            # ------------------------------------------------------
            elif (
                row.declaration_component
                == "Supplier Regularisation"
            ):

                if row.modelo_7_field != "29":
                    row.classification_status = (
                        "Pending Modelo 7 Mapping"
                    )
                    continue

                add(
                    "29",
                    row.vat_amount,
                )

                row.classification_status = "Derived"

        # ----------------------------------------------------------
        # MODELO 7 SUMMARY FORMULAS - CURRENT IMPLEMENTED SCOPE
        # ----------------------------------------------------------

        # Campo 31 is the sum of taxable-base control fields.
        # For the current sales/cativo implementation this means
        # field 1 + field 8.
        amounts["31"] = sum(
            flt(amounts.get(code))
            for code in (
                "1",
                "8",
                "16",
                "18",
                "20",
                "22",
                "24",
            )
        )

        # Taxpayer-side VAT.
        amounts["32"] = sum(
            flt(amounts.get(code))
            for code in (
                "9",
                "17",
                "19",
                "21",
                "23",
                "25",
                "26",
                "28",
            )
        )

        # State-side VAT.
        amounts["33"] = sum(
            flt(amounts.get(code))
            for code in (
                "2",
                "27",
                "29",
            )
        )

        if flt(amounts["32"]) > flt(amounts["33"]):
            amounts["34"] = (
                flt(amounts["32"])
                - flt(amounts["33"])
            )
            amounts["35"] = 0
        else:
            amounts["34"] = 0
            amounts["35"] = (
                flt(amounts["33"])
                - flt(amounts["32"])
            )

        # These depend on prior-period balances / declaration data
        # and are deliberately not inferred at this stage.
        for code in ("36", "37", "38", "39", "40"):
            amounts.setdefault(code, 0)

        definitions = [
            ("1", "Taxable sales/services with VAT liquidated", "Base"),
            ("2", "Output VAT in favour of State", "State"),

            ("8", "Operations where VAT was cativo by customer", "Base"),
            ("9", "VAT cativo in favour of taxpayer", "Taxpayer"),

            (
                "16",
                "Fixed Assets and Investments - Base",
                "Base",
            ),
            (
                "17",
                "Fixed Assets and Investments - VAT",
                "Taxpayer",
            ),
            (
                "18",
                "Inventory - Base",
                "Base",
            ),
            (
                "19",
                "Inventory - VAT",
                "Taxpayer",
            ),
            (
                "20",
                "Other Consumption Goods - Base",
                "Base",
            ),
            (
                "21",
                "Other Consumption Goods - VAT",
                "Taxpayer",
            ),
            (
                "22",
                "Services - Base",
                "Base",
            ),
            (
                "23",
                "Services - VAT",
                "Taxpayer",
            ),

            (
                "24",
                "Imports - Taxable Base",
                "Base",
            ),
            (
                "25",
                "Imports - Deductible VAT",
                "Taxpayer",
            ),

            (
                "26",
                "VAT Cativo Regularisation - Taxpayer",
                "Taxpayer",
            ),
            (
                "27",
                "VAT Cativo Regularisation - State",
                "State",
            ),
            (
                "28",
                "Taxpayer Regularisation - Taxpayer",
                "Taxpayer",
            ),
            (
                "29",
                "Taxpayer Regularisation - State",
                "State",
            ),

            ("31", "Total Base Tributável", "Base"),
            (
                "32",
                "Total Imposto a Favor do Sujeito Passivo",
                "Taxpayer",
            ),
            (
                "33",
                "Total Imposto a Favor do Estado",
                "State",
            ),
            (
                "34",
                "Crédito do Período",
                "Control",
            ),
            (
                "35",
                "Imposto do Período a Favor do Estado",
                "Control",
            ),
            (
                "36",
                "Excesso / Crédito de Períodos Anteriores",
                "Control",
            ),
            (
                "37",
                "IVA a Pagar ao Estado",
                "Control",
            ),
            (
                "38",
                "Crédito do Imposto",
                "Control",
            ),
            (
                "39",
                "Reporte para o Período Seguinte",
                "Control",
            ),
            (
                "40",
                "Pedido de Reembolso",
                "Control",
            ),
        ]

        self.set("modelo_7_lines", [])

        for code, description, side in definitions:
            self.append(
                "modelo_7_lines",
                {
                    "field_code": code,
                    "description": description,
                    "side": side,
                    "source_amount": flt(
                        amounts.get(code),
                        2,
                    ),
                    "declared_amount": flt(
                        declared.get(code),
                        2,
                    ),
                },
            )


    @frappe.whitelist()
    def create_reconciliation_adjustment(self):
        """
        Create one Journal Entry for all unresolved reconciling
        items marked as requiring a GL adjustment.

        Positive difference:
            declaration net > accounting net
            -> credit affected VAT account
            -> debit VAT Reconciling Account

        Negative difference:
            declaration net < accounting net
            -> debit affected VAT account
            -> credit VAT Reconciling Account
        """

        if self.docstatus != 0:
            frappe.throw(
                "Reconciliation adjustment can only be created "
                "while the VAT Period is Draft."
            )

        settings = frappe.get_doc(
            "VAT Settings",
            self.vat_settings,
        )

        recon_account = settings.get(
            "vat_reconciling_account"
        )

        if not recon_account:
            frappe.throw(
                "VAT Reconciling Account is not configured "
                "in VAT Settings."
            )

        if (
            frappe.db.get_value(
                "Account",
                recon_account,
                "company",
            )
            != self.company
        ):
            frappe.throw(
                "VAT Reconciling Account belongs "
                "to another company."
            )

        mapped_accounts = {
            row.account: row
            for row in settings.account_mappings or []
            if row.active
        }

        eligible = []

        for row in self.reconciling_items or []:

            if not row.requires_gl_adjustment:
                continue

            if (
                row.journal_entry
                and frappe.db.get_value(
                    "Journal Entry",
                    row.journal_entry,
                    "docstatus",
                )
                == 1
            ):
                continue

            difference = flt(
                flt(row.declared_amount)
                - flt(row.accounting_amount),
                2,
            )

            if abs(difference) < ZERO_TOLERANCE:
                continue

            if not row.account:
                frappe.throw(
                    f"VAT Reconciling Item row {row.idx}: "
                    "affected VAT account is required."
                )

            if row.account == recon_account:
                frappe.throw(
                    f"VAT Reconciling Item row {row.idx}: "
                    "the affected VAT account cannot itself be "
                    "the VAT Reconciling Account."
                )

            if (
                frappe.db.get_value(
                    "Account",
                    row.account,
                    "company",
                )
                != self.company
            ):
                frappe.throw(
                    f"VAT Reconciling Item row {row.idx}: "
                    "affected account belongs to another company."
                )

            mapping = mapped_accounts.get(
                row.account
            )

            if not mapping:
                frappe.throw(
                    f"VAT Reconciling Item row {row.idx}: "
                    f"Account {row.account} is not active in "
                    "VAT Account Mapping."
                )

            if mapping.modelo_7_side not in (
                "State",
                "Taxpayer",
            ):
                frappe.throw(
                    f"VAT Reconciling Item row {row.idx}: "
                    f"Account {row.account} must be mapped as "
                    "State or Taxpayer, not Control."
                )

            eligible.append(
                (
                    row,
                    difference,
                )
            )

        if not eligible:
            frappe.throw(
                "No reconciling item requires a new "
                "GL adjustment."
            )

        accounts = []

        total_debit = 0
        total_credit = 0

        for row, difference in eligible:

            amount = abs(
                flt(
                    difference,
                    2,
                )
            )

            if difference > 0:

                accounts.append(
                    {
                        "account":
                            row.account,
                        "debit_in_account_currency":
                            0,
                        "credit_in_account_currency":
                            amount,
                        "user_remark":
                            row.description,
                    }
                )

                total_credit += amount

            else:

                accounts.append(
                    {
                        "account":
                            row.account,
                        "debit_in_account_currency":
                            amount,
                        "credit_in_account_currency":
                            0,
                        "user_remark":
                            row.description,
                    }
                )

                total_debit += amount

        net = flt(
            total_credit
            - total_debit,
            2,
        )

        if net > ZERO_TOLERANCE:

            accounts.append(
                {
                    "account":
                        recon_account,
                    "debit_in_account_currency":
                        net,
                    "credit_in_account_currency":
                        0,
                    "user_remark":
                        "VAT reconciliation contra account",
                }
            )

        elif net < -ZERO_TOLERANCE:

            accounts.append(
                {
                    "account":
                        recon_account,
                    "debit_in_account_currency":
                        0,
                    "credit_in_account_currency":
                        abs(net),
                    "user_remark":
                        "VAT reconciliation contra account",
                }
            )

        je = frappe.get_doc(
            {
                "doctype":
                    "Journal Entry",
                "voucher_type":
                    "Journal Entry",
                "company":
                    self.company,
                "posting_date":
                    self.period_end,
                "user_remark":
                    (
                        "VAT reconciliation adjustment "
                        f"for {self.name}"
                    ),
                "accounts":
                    accounts,
            }
        )

        je.insert()
        je.submit()

        for row, difference in eligible:

            row.journal_entry = je.name
            row.resolution_status = "Corrected"

        # The reconciliation JE is linked on each
        # VAT Reconciling Item. closing_journal_entry is reserved
        # exclusively for the final monthly VAT closing JE.

        self.save()

        # The reconciliation JE is now part of accounting.
        self.load_accounting()

        self.save()

        return je.name


    @frappe.whitelist()
    def reload_accounting(self):
        self.load_accounting()
        self.save()

        return {
            "transactions": len(self.transactions or []),
            "state_amount": self.accounting_state_amount,
            "taxpayer_amount": self.accounting_taxpayer_amount,
            "net_position": self.accounting_net_position,
        }


@frappe.whitelist()
def reload_accounting(name):
    doc = frappe.get_doc(
        "VAT Period Control",
        name,
    )

    return doc.reload_accounting()


@frappe.whitelist()
def create_vat_closing_from_reconciliation(
    vat_period_control,
):
    period = frappe.get_doc(
        "VAT Period Control",
        vat_period_control,
    )

    return period.create_vat_closing_journal_entry()
