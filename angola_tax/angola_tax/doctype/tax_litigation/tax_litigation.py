import frappe
from frappe.model.document import Document
from frappe.utils import flt


class TaxLitigation(Document):

    def validate(self):
        self.normalize_current_stage()
        self.load_assessment_context()
        self.calculate_amounts()
        self.derive_stage()

    def normalize_current_stage(self):
        legacy_stages = {
            "Direito de Audição Prévia": "DAP",
            "Recurso Administrativo": "Recurso Hierárquico",
            "End Process": "Closed",
        }
        stage = self.get("current_stage")
        if stage in legacy_stages and self.meta.has_field("current_stage"):
            self.set("current_stage", legacy_stages[stage])

    def load_assessment_context(self):
        if not self.tax_assessment:
            return

        assessment = frappe.get_doc(
            "Tax Assessment",
            self.tax_assessment,
        )

        if assessment.assessment_decision != "Disputed":
            frappe.throw(
                "Tax Litigation can only be created "
                "when Assessment Decision is Disputed."
            )

        self.company = assessment.company
        self.tax_type = assessment.tax_type
        self.fiscal_obligation = assessment.fiscal_obligation

        if not self.amount_under_dispute:
            self.amount_under_dispute = (
                assessment.disputed_amount
                or assessment.effective_assessed_amount
                or assessment.amount_assessed
                or 0
            )
        if not self.fiscal_obligation:
            self.fiscal_obligation = assessment.fiscal_obligation
        if not self.current_stage:
            self.current_stage = "DAP"
        # Use meta-guarded access so the controller remains safe during
        # deployment, before `bench migrate` has synchronized new fields.
        if self.meta.has_field("provision_decision") and not self.get("provision_decision"):
            self.set("provision_decision", "Pending")
        if self.meta.has_field("provision_review_status") and not self.get("provision_review_status"):
            self.set("provision_review_status", "Pending")

    def calculate_amounts(self):
        if not self.tax_assessment:
            return

        assessment = frappe.get_doc(
            "Tax Assessment",
            self.tax_assessment,
        )

        assessed = flt(
            assessment.disputed_amount
            or assessment.effective_assessed_amount
            or assessment.amount_assessed
        )

        disputed = flt(
            self.amount_under_dispute
        )

        if disputed < 0:
            frappe.throw(
                "Amount Under Dispute cannot be negative."
            )

        if disputed > assessed:
            frappe.throw(
                "Amount Under Dispute cannot exceed "
                "Amount Assessed."
            )

        self.undisputed_amount = (
            assessed - disputed
        )

    def derive_stage(self):
        # Litigation stages are now separate phase DocTypes. The old
        # Tax Dispute Action child table is retained only for legacy data.
        if not self.get("current_stage") and self.meta.has_field("current_stage"):
            self.set("current_stage", "DAP")

    def before_cancel(self):
        frappe.throw(
            "Submitted Tax Litigation records "
            "cannot be cancelled."
        )

def on_trash(self):
        if self.docstatus == 1:
            frappe.throw(
            "Submitted Tax Litigation records "
            "cannot be deleted."
        )


LITIGATION_PHASE_TYPES = (
    "Tax Litigation DAP",
    "Tax Litigation Reclamacao Administrativa",
    "Tax Litigation Recurso Hierarquico",
    "Tax Litigation Impugnacao Judicial",
)

LITIGATION_PHASE_BY_STAGE = {
    "DAP": "Tax Litigation DAP",
    "Direito de Audição Prévia": "Tax Litigation DAP",
    "Reclamação Administrativa": "Tax Litigation Reclamacao Administrativa",
    "Recurso Hierárquico": "Tax Litigation Recurso Hierarquico",
    "Recurso Administrativo": "Tax Litigation Recurso Hierarquico",
    "Impugnação Judicial": "Tax Litigation Impugnacao Judicial",
}


def get_active_litigation_phase(case):
    phase_doctype = case.get("current_phase_doctype")
    phase_name = case.get("current_phase")

    if (
        phase_doctype in LITIGATION_PHASE_TYPES
        and phase_name
        and frappe.db.exists("DocType", phase_doctype)
        and frappe.db.table_exists(phase_doctype)
        and frappe.db.exists(phase_doctype, phase_name)
    ):
        phase = frappe.get_doc(phase_doctype, phase_name)
        if phase.tax_litigation == case.name and phase.docstatus != 2:
            return phase

    # Recover older cases only from their current stage. Do not scan later
    # phases when their DocType or database table is unavailable.
    phase_doctype = LITIGATION_PHASE_BY_STAGE.get(case.get("current_stage"))
    if not phase_doctype:
        return None
    if not frappe.db.exists("DocType", phase_doctype):
        return None
    if not frappe.db.table_exists(phase_doctype):
        return None

    phase_name = frappe.db.get_value(
        phase_doctype,
        {"tax_litigation": case.name, "docstatus": ["!=", 2]},
        "name",
        order_by="creation desc",
    )
    if phase_name:
        return frappe.get_doc(phase_doctype, phase_name)
    return None

@frappe.whitelist()
def get_litigation_current_phase(litigation_name):
    case = frappe.get_doc("Tax Litigation", litigation_name)
    case.check_permission("read")
    phase = get_active_litigation_phase(case)
    return phase.as_dict() if phase else None


@frappe.whitelist()
def create_dap_phase_from_litigation(litigation_name):
    case = frappe.get_doc("Tax Litigation", litigation_name)
    case.check_permission("write")
    existing_phase = get_active_litigation_phase(case)
    if existing_phase:
        return {"phase": existing_phase.name}

    assessment = frappe.get_doc("Tax Assessment", case.tax_assessment)
    if assessment.assessment_decision != "Disputed":
        frappe.throw("A DAP phase can only be created for a disputed Tax Assessment.")

    legacy_action = frappe.db.get_value(
        "Tax Dispute Action",
        {"parent": case.name, "parenttype": "Tax Litigation", "parentfield": "dispute_actions"},
        ["action_date", "reference", "document"],
        as_dict=True,
        order_by="idx desc",
    )
    dap = frappe.new_doc("Tax Litigation DAP")
    dap.tax_litigation = case.name
    dap.tax_assessment = assessment.name
    dap.company = case.company
    dap.tax_type = case.tax_type
    dap.fiscal_obligation = case.get("fiscal_obligation")
    dap.company_submission_due_date = assessment.legal_time_limit_date
    if legacy_action:
        dap.submission_date = legacy_action.action_date
        dap.submission_reference = legacy_action.reference
        dap.submission_document = legacy_action.document
    dap.insert(ignore_permissions=True)

    case.db_set("current_stage", "DAP", update_modified=False)
    case.db_set("current_phase_doctype", dap.doctype, update_modified=False)
    case.db_set("current_phase", dap.name, update_modified=False)
    return {"phase": dap.name}


@frappe.whitelist()
def record_litigation_agt_reply(
    litigation_name,
    agt_response,
    response_date,
    document,
    reference=None,
    revised_amount=None,
):
    case = frappe.get_doc("Tax Litigation", litigation_name)
    case.check_permission("write")
    phase = get_active_litigation_phase(case)
    if not phase:
        frappe.throw("This Tax Litigation case has no active phase.")
    phase.check_permission("write")
    if phase.docstatus != 1:
        frappe.throw("Submit the active litigation phase before recording the AGT reply.")
    if phase.agt_response:
        frappe.throw("An AGT reply has already been recorded for this phase.")
    if agt_response not in {"Argument Accepted", "Argument Rejected", "Revised Amount Proposed"}:
        frappe.throw("Choose a valid AGT response.")
    if not response_date or not document:
        frappe.throw("AGT Response Date and AGT Reply Document are required.")
    if agt_response == "Revised Amount Proposed" and flt(revised_amount) <= 0:
        frappe.throw("AGT Revised Amount is required for a revised proposal.")

    phase.agt_response = agt_response
    phase.agt_response_date = response_date
    phase.agt_response_reference = reference
    phase.agt_response_document = document
    phase.agt_revised_amount = flt(revised_amount) if revised_amount not in (None, "") else 0
    phase.save(ignore_permissions=True)
    return {"tax_litigation": case.name, "phase": phase.name}


@frappe.whitelist()
def record_litigation_company_decision(
    litigation_name,
    company_decision,
    decision_date,
    settlement_amount=None,
    notes=None,
):
    case = frappe.get_doc("Tax Litigation", litigation_name)
    case.check_permission("write")

    phase = get_active_litigation_phase(case)
    if not phase:
        frappe.throw("This Tax Litigation case has no active phase.")
    phase.check_permission("write")
    if phase.tax_litigation != case.name:
        frappe.throw("The active phase does not belong to this Tax Litigation case.")
    if phase.docstatus != 1:
        frappe.throw("Submit the active litigation phase before recording a decision.")
    if not phase.agt_response:
        frappe.throw("Record the AGT reply before making the company decision.")
    if phase.agt_response == "Argument Accepted":
        frappe.throw("AGT accepted the company argument; no further decision is required.")
    if phase.company_decision:
        frappe.throw("A company decision has already been recorded for this phase.")

    if company_decision not in {
        "Accept AGT Position",
        "Continue to Next Phase",
        "Continue Litigation",
    }:
        frappe.throw("Choose a valid company decision.")
    if company_decision == "Continue to Next Phase" and not phase.next_doctype:
        frappe.throw("There is no further litigation phase after this phase.")
    if company_decision == "Continue Litigation" and phase.next_doctype:
        frappe.throw("Continue the case to the next administrative phase.")

    phase.company_decision = company_decision
    phase.company_decision_date = decision_date
    phase.company_decision_notes = notes
    if company_decision == "Accept AGT Position":
        phase.settlement_amount = flt(settlement_amount)
        if phase.settlement_amount <= 0:
            frappe.throw("Accepted Settlement Amount must be greater than zero.")

    phase.save(ignore_permissions=True)
    return {
        "tax_litigation": case.name,
        "phase": phase.name,
        "status": frappe.db.get_value("Tax Litigation", case.name, "status"),
    }


@frappe.whitelist()
def record_litigation_provision_decision(
    litigation_name, provision_decision, provision_amount=None, notes=None,
    phase_doctype=None, phase_name=None
):
    allowed = {"No Provision", "Full Provision", "Partial Provision", "Already Recognised", "Disclosure Only"}
    if provision_decision not in allowed:
        frappe.throw("Choose a valid provision decision.")
    case = frappe.get_doc("Tax Litigation", litigation_name)
    assessment = frappe.get_doc("Tax Assessment", case.tax_assessment)
    phase = None
    if phase_doctype or phase_name:
        allowed_phases = {
            "Tax Litigation DAP",
            "Tax Litigation Reclamacao Administrativa",
            "Tax Litigation Recurso Hierarquico",
            "Tax Litigation Impugnacao Judicial",
        }
        if phase_doctype not in allowed_phases or not phase_name:
            frappe.throw("Choose a valid linked litigation phase.")
        phase = frappe.get_doc(phase_doctype, phase_name)
        if phase.tax_litigation != case.name:
            frappe.throw("The selected phase belongs to another Tax Litigation case.")
    else:
        case.provision_decision = provision_decision
        case.provision_review_status = "Recorded"
    if provision_decision == "Partial Provision" and flt(provision_amount) <= 0:
        frappe.throw("A positive Provision Amount is required for a partial provision.")

    existing = None
    if phase:
        existing = frappe.db.get_value(
            "Tax Provision",
            {"litigation_phase_doctype": phase_doctype, "litigation_phase": phase_name},
            "name",
        )
    else:
        existing = case.get("tax_provision")
    provision = frappe.get_doc("Tax Provision", existing) if existing else frappe.new_doc("Tax Provision")
    if provision.docstatus == 1:
        provision = frappe.new_doc("Tax Provision")
    provision.tax_assessment = assessment.name
    provision.tax_litigation = case.name
    if phase:
        provision.litigation_phase_doctype = phase_doctype
        provision.litigation_phase = phase_name
    provision.company = case.company
    provision.tax_type = case.tax_type
    provision.provision_decision = provision_decision
    provision.provision_amount = flt(provision_amount) if provision_amount not in (None, "") else 0
    provision.provision_date = frappe.utils.today()
    provision.provision_notes = notes
    provision.save(ignore_permissions=True)

    if provision.docstatus == 0:
        provision.submit()
    case.db_set(
        {
            "provision_decision": provision_decision,
            "provision_review_status": "Recorded",
            "tax_provision": provision.name,
        },
        update_modified=False,
    )
    case.provision_decision = provision_decision
    case.provision_review_status = "Recorded"
    case.tax_provision = provision.name

    if phase:
        phase.db_set(
            "tax_provision",
            provision.name,
            update_modified=False,
        )
        phase.tax_provision = provision.name

    return {"tax_litigation": case.name, "tax_provision": provision.name}


@frappe.whitelist()
def close_litigation_case(
    litigation_name,
    outcome="Closed",
):
    allowed = {
        "Closed",
        "Won",
        "Lost",
        "Agreement",
    }

    if outcome not in allowed:
        frappe.throw(
            "Outcome must be Closed, Won, Lost or Agreement."
        )

    case = frappe.get_doc(
        "Tax Litigation",
        litigation_name,
    )
    case.check_permission("write")

    phase = get_active_litigation_phase(case)

    if phase and phase.meta.has_field("phase_status"):
        phase.db_set(
            "phase_status",
            "Closed",
            update_modified=False,
        )

    case.db_set(
        {
            "current_stage": "Closed",
            "status": outcome,
        },
        update_modified=True,
    )

    return {
        "tax_litigation": case.name,
        "status": outcome,
        "current_stage": "Closed",
        "phase": phase.name if phase else None,
    }
