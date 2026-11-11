from angola_tax.angola_tax.doctype.tax_litigation_phase import TaxLitigationPhase


class TaxLitigationDAP(TaxLitigationPhase):
    phase_label = 'DAP'
    next_doctype = 'Tax Litigation Reclamacao Administrativa'
