import frappe
from frappe.model.document import Document
from frappe.utils import getdate
from angola_tax.industrial_tax.model import company_currency, accepts_tax_expense_account, accepts_tax_credit_account


def get_company_currency(company):
    try:
        return company_currency(frappe.get_doc("Company", company).default_currency)
    except ValueError as exc:
        frappe.throw(str(exc))


class IndustrialTaxRule(Document):
    def validate(self):
        if self.regime != "General" or float(self.ordinary_rate or 0) != 25:
            frappe.throw("Only the ordinary general-regime 25% annual rule is supported.")
        if self.rounding_policy != "Half Up" or self.amount_precision not in (0, 2):
            frappe.throw("Choose and approve an explicit Half Up precision (0 or 2).")
        if getdate(self.effective_from) > getdate(self.effective_to):
            frappe.throw("Invalid rule effective dates.")

    def before_submit(self):
        if not self.legal_reference or not self.approval_evidence:
            frappe.throw("Legal reference and approval evidence are required.")


class IndustrialTaxProfile(Document):
    def validate(self):
        if getdate(self.effective_from) > getdate(self.effective_to):
            frappe.throw("Invalid profile effective dates.")
        roles = [r.role for r in self.account_roles or []]
        if len(roles) != len(set(roles)):
            frappe.throw("An accounting role may only be mapped once.")
        for row in self.account_roles or []:
            config = frappe.get_doc("Tax Account Configuration", row.configuration)
            if config.company != self.company or config.tax_type != self.tax_type:
                frappe.throw("All role configurations must belong to the profile company and tax.")
            if config.account_role != row.role:
                frappe.throw("Profile role and Tax Account Configuration role must agree.")

    def before_submit(self):
        rule = frappe.get_doc("Industrial Tax Rule", self.rule_version)
        if rule.docstatus != 1 or not self.review_evidence:
            frappe.throw("Submit the legal rule and attach profile review evidence first.")
        if not self.general_ordinary_only:
            frappe.throw("Confirm eligibility for an exclusively ordinary general-regime calculation.")
        for role in ("Annual Liability", "Current Tax Expense"):
            role_account(self, role, self.effective_from)
        deadline = frappe.get_doc("Tax Deadline Configuration", self.deadline_configuration)
        if (deadline.docstatus != 1 or deadline.company != self.company
                or deadline.tax_type != self.tax_type
                or deadline.legal_due_timing != "Fifth Following Month"
                or deadline.legal_due_day_rule != "Last Working Day"):
            frappe.throw("Select a submitted company/tax deadline: fifth following month, last working day.")
        tax = frappe.get_doc("Tax Type", self.tax_type)
        if tax.docstatus != 1 or not tax.active or tax.periodicity != "Annual":
            frappe.throw("Select the existing active, submitted annual Tax Type.")


def role_account(profile, role, at_date, required=True):
    rows = [r for r in profile.account_roles or [] if r.role == role]
    if not rows:
        if required:
            frappe.throw(f"Missing accounting role: {role}.")
        return None
    if len(rows) != 1:
        frappe.throw(f"Ambiguous accounting role: {role}.")
    config = frappe.get_doc("Tax Account Configuration", rows[0].configuration)
    day = getdate(at_date)
    if (config.docstatus != 1 or not config.active or config.company != profile.company
            or config.tax_type != profile.tax_type or config.account_role != role
            or day < getdate(config.effective_from)
            or (config.effective_to and day > getdate(config.effective_to))):
        frappe.throw(f"No submitted, effective configuration for {role} on {day}.")
    liability_roles = ("Annual Liability", "Provisional Liability", "Collected Resident Withholding Liability", "Collected Nonresident Withholding Liability")
    expected_policy = "Computation Only" if role in liability_roles else "Disabled"
    if config.ingestion_policy != expected_policy:
        frappe.throw(f"{role} requires ingestion policy {expected_policy}.")
    account = frappe.get_doc("Account", config.account)
    currency = get_company_currency(profile.company)
    if (account.company != profile.company or account.is_group or account.disabled
            or account.account_currency != currency):
        frappe.throw(f"{config.account} must be an active company leaf account in {currency}.")
    if role in liability_roles and account.root_type != "Liability":
        frappe.throw("Annual liability must be a Liability account.")
    account_number = account.account_number or account.name.split(" - ", 1)[0]
    if role in ("Current Tax Expense", "Deferred Tax Expense") and not accepts_tax_expense_account(
            account.root_type, account_number):
        frappe.throw("Income tax expense roles require an Expense account or an explicitly mapped PGC class 87 results account under Equity.")
    if role in liability_roles and (config.determination_direction != "Credit"
                                       or config.settlement_direction != "Debit"):
        frappe.throw("Annual liability requires Credit determination / Debit settlement.")
    if role in ("Provisional Asset", "Suffered Withholding Asset", "Pending Suffered Withholding Asset", "Recognised Carryforward Credit") and not accepts_tax_credit_account(
            account.root_type, account_number, role):
        frappe.throw("Tax credit roles require an Asset account or an explicitly mapped PGC 3414 pending / 3415 withholding / 3419 provisional debit account under Liability.")
    return config.account


def validate_account_configuration(doc, method=None):
    if doc.ingestion_policy == "Legacy":
        return
    account = frappe.get_doc("Account", doc.account)
    if account.company != doc.company or account.is_group or account.disabled:
        frappe.throw("Tax account must be an active leaf account of the company.")
    if not doc.account_role or doc.account_role == "Legacy":
        frappe.throw("Select an accounting role for non-legacy configurations.")
    liability_roles = ("Annual Liability", "Provisional Liability", "Collected Resident Withholding Liability", "Collected Nonresident Withholding Liability")
    if doc.account_role in liability_roles and doc.ingestion_policy != "Computation Only":
        frappe.throw("Annual II liability must use Computation Only ingestion.")
    if doc.account_role not in liability_roles and doc.ingestion_policy != "Disabled":
        frappe.throw("Expense and asset roles must disable ordinary GL ingestion.")


def protect_ii_assessment(doc, method=None):
    if not doc.fiscal_obligation:
        return
    basis = frappe.db.get_value("Fiscal Obligation", doc.fiscal_obligation, "determination_basis")
    if basis == "Computation" and (
            doc.assessment_origin != "From Fiscal Obligation"
            or doc.assessment_type != "Self-Determination"):
        frappe.throw("II authority corrections require the forthcoming controlled revision adapter; first package supports Self-Determination only.")
    if basis == "Computation":
        from angola_tax.industrial_tax.model import money
        obligation = frappe.get_doc("Fiscal Obligation", doc.fiscal_obligation)
        if money(doc.amount_assessed) != money(obligation.amount_determined):
            frappe.throw("First II package supports an unchanged self-determination assessment only.")
        frappe.db.sql("select name from `tabFiscal Obligation` where name=%s for update", doc.fiscal_obligation)
        if frappe.db.exists("Tax Assessment", {"fiscal_obligation": doc.fiscal_obligation,
                "docstatus": 1, "name": ["!=", doc.name]}):
            frappe.throw("An active assessment already exists; controlled revision is required.")


def protect_ii_settlement(doc, method=None):
    if not doc.fiscal_obligation:
        return
    basis = frappe.db.get_value("Fiscal Obligation", doc.fiscal_obligation, "determination_basis")
    if basis != "Computation":
        return
    frappe.db.sql("select name from `tabFiscal Obligation` where name=%s for update", doc.fiscal_obligation)
    if frappe.db.exists("Tax Settlement", {"fiscal_obligation": doc.fiscal_obligation,
            "docstatus": 1, "name": ["!=", doc.name]}):
        frappe.throw("An active II liquidation already exists; use its collection references/payment plan.")
