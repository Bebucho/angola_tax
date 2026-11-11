import frappe
from frappe.utils import flt
from angola_tax.angola_tax.doctype.tax_assessment.tax_assessment import TaxAssessment
from angola_tax.angola_tax.doctype.tax_settlement.tax_settlement import TaxSettlement
from angola_tax.industrial_tax.model import money
from angola_tax.industrial_tax.adapters import is_ii, source


class IndustrialTaxAssessment(TaxAssessment):
    def publish_operational_assessment(self):
        obligation=frappe.get_doc('Fiscal Obligation',self.fiscal_obligation)
        if not is_ii(obligation):return super().publish_operational_assessment()
        comp=source(obligation)
        gross=money(self.effective_assessed_amount)
        collectible=max(gross-money(comp.credit_applied_amount),money(0))
        frappe.db.set_value('Fiscal Obligation',obligation.name,dict(amount_assessed=float(gross),
            effective_obligation_amount=float(collectible),outstanding_amount=float(max(collectible-money(obligation.amount_paid),0)),
            latest_tax_assessment=self.name))


class IndustrialTaxSettlement(TaxSettlement):
    def load_obligation_context(self):
        super().load_obligation_context()
        if not self.fiscal_obligation:return
        obligation=frappe.get_doc('Fiscal Obligation',self.fiscal_obligation)
        if not is_ii(obligation):return
        comp=source(obligation)
        if not self.tax_assessment:frappe.throw('Submit gross self-determination assessment before positive cash liquidation.')
        assessment=frappe.get_doc('Tax Assessment',self.tax_assessment)
        if assessment.assessment_type!='Self-Determination' or assessment.assessment_origin!='From Fiscal Obligation':
            frappe.throw('Authority revisions require the controlled correction adapter.')
        if money(assessment.effective_assessed_amount)!=money(comp.tax_amount):
            frappe.throw('II self-determination must preserve the approved gross tax.')
        self.expected_amount=comp.net_cash_requirement
        if money(self.expected_amount)<=0:
            frappe.throw('Credit-only/nil II has no cash liquidation. Do not create a fictitious RUPE.')
        self.gross_tax_amount=comp.tax_amount
        self.credit_applied_amount=comp.credit_applied_amount

    def default_liquidated_amount(self):
        if self.fiscal_obligation:
            obligation=frappe.get_doc('Fiscal Obligation',self.fiscal_obligation)
            if is_ii(obligation):
                if any(money(r.amount) for r in self.settlement_components or [] if r.component!='Principal'):
                    frappe.throw('II penalties/authority changes require a separate controlled assessment; do not merge them with credit offsets.')
        return super().default_liquidated_amount()
