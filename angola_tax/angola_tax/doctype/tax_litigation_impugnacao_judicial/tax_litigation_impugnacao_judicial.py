from angola_tax.angola_tax.doctype.tax_litigation_phase import TaxLitigationPhase


class TaxLitigationImpugnacaoJudicial(TaxLitigationPhase):
    phase_label = 'Impugnação Judicial'
    next_doctype = None

    # tax_control_judicial_submission_status
    def on_submit(self):
        super().on_submit()
        self.phase_status = "Awaiting Court Decision"
        self.db_set("phase_status", self.phase_status, update_modified=False)

