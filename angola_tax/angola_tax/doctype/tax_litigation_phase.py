import frappe
from frappe.model.document import Document
from frappe.utils import flt


class TaxLitigationPhase(Document):
    phase_label = None
    next_doctype = None

    def validate(self):
        self.load_case_context()
        self.validate_phase_data()

    def load_case_context(self):
        if not self.tax_litigation:
            return
        case = frappe.get_doc("Tax Litigation", self.tax_litigation)
        self.tax_assessment = case.tax_assessment
        self.company = case.company
        self.tax_type = case.tax_type
        self.fiscal_obligation = case.get("fiscal_obligation")

    def validate_phase_data(self):
        if self.docstatus == 0 and (self.agt_response or self.company_decision):
            frappe.throw("Submit this phase before recording AGT's reply or the company's decision.")
        if self.agt_response == "Revised Amount Proposed" and flt(self.agt_revised_amount) <= 0:
            frappe.throw("AGT Revised Amount is required for a revised proposal.")
        if self.agt_response and (not self.agt_response_date or not self.agt_response_document):
            frappe.throw("Record the AGT response date and attach its document.")
        if self.company_decision and not self.company_decision_date:
            frappe.throw("Company Decision Date is required.")
        if self.company_decision == "Accept AGT Position" and self.agt_response not in ("Argument Rejected", "Revised Amount Proposed"):
            if self.agt_response != "Argument Accepted":
                frappe.throw("Record an AGT reply before deciding to accept its position.")
        if self.company_decision == "Accept AGT Position":
            default_amount = self.agt_revised_amount or frappe.db.get_value(
                "Tax Litigation", self.tax_litigation, "amount_under_dispute"
            )
            if not flt(self.settlement_amount):
                self.settlement_amount = flt(default_amount)
            if flt(self.settlement_amount) <= 0:
                frappe.throw("Accepted Settlement Amount must be greater than zero.")
        if self.company_decision == "Continue to Next Phase" and not self.next_doctype:
            frappe.throw("There is no further litigation phase after this phase.")
        if self.company_decision == "Continue Litigation" and self.next_doctype:
            frappe.throw("Use Continue to Next Phase for this stage.")

    def before_submit(self):
        if frappe.db.get_value("Tax Assessment", self.tax_assessment, "docstatus") != 1:
            frappe.throw("Submit the disputed Tax Assessment before filing its DAP or next litigation phase.")
        required = []
        if not self.submission_date:
            required.append("Company Submission Date")
        if not self.submission_reference:
            required.append("Company Submission Reference")
        if not self.submission_document:
            required.append("Company Submission Document")
        if required:
            frappe.throw("Litigation phase submission requires: " + ", ".join(required))

    def on_submit(self):
        self.phase_status = "Awaiting AGT Reply"
        self.db_set("phase_status", self.phase_status, update_modified=False)
        self.sync_case()

    def on_update_after_submit(self):
        if self.agt_response == "Argument Accepted":
            self.phase_status = "Closed"
            self.db_set("phase_status", self.phase_status, update_modified=False)
            self.update_case("Won", "Closed")
            return
        if not self.agt_response or not self.company_decision:
            self.update_case("Awaiting Decision", self.phase_label)
            return
        if self.company_decision == "Continue Litigation":
            self.phase_status = "Continue Dispute"
            self.db_set("phase_status", self.phase_status, update_modified=False)
            self.update_case("Open", self.phase_label)
            return
        if self.company_decision == "Accept AGT Position":
            self.phase_status = "Settlement Pending"
            self.db_set("phase_status", self.phase_status, update_modified=False)
            self.update_case("Settlement Pending", self.phase_label)
            return
        self.phase_status = "Continue Dispute"
        self.db_set("phase_status", self.phase_status, update_modified=False)
        self.create_next_phase()

    def sync_case(self):
        self.update_case("Awaiting AGT Reply", self.phase_label)

    def update_case(self, status, stage):
        if not self.tax_litigation:
            return
        values = {"status": status, "current_stage": stage, "current_phase_doctype": self.doctype, "current_phase": self.name}
        frappe.db.set_value("Tax Litigation", self.tax_litigation, values, update_modified=True)

    def create_next_phase(self):
        case = frappe.get_doc("Tax Litigation", self.tax_litigation)
        existing = frappe.db.get_value(self.next_doctype, {"tax_litigation": case.name, "docstatus": ["!=", 2]}, "name")
        if existing:
            next_phase = frappe.get_doc(self.next_doctype, existing)
        else:
            next_phase = frappe.new_doc(self.next_doctype)
            next_phase.tax_litigation = case.name
            next_phase.insert(ignore_permissions=True)
        self.next_phase_doctype = self.next_doctype
        self.next_phase = next_phase.name
        self.db_set({"next_phase_doctype": self.next_doctype, "next_phase": next_phase.name}, update_modified=False)
        stage_by_doctype = {
            "Tax Litigation Reclamacao Administrativa": "Reclamação Administrativa",
            "Tax Litigation Recurso Hierarquico": "Recurso Hierárquico",
            "Tax Litigation Impugnacao Judicial": "Impugnação Judicial",
        }
        case.db_set("current_stage", stage_by_doctype.get(self.next_doctype, self.next_doctype), update_modified=False)
        case.db_set("current_phase_doctype", self.next_doctype, update_modified=False)
        case.db_set("current_phase", next_phase.name, update_modified=False)
        case.db_set("status", "Open", update_modified=False)
    def before_cancel(self):
        frappe.throw("Submitted litigation phase records cannot be cancelled.")

    def on_trash(self):
        if self.docstatus == 1:
            frappe.throw("Submitted litigation phase records cannot be deleted.")
