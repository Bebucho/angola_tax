from angola_tax.angola_tax.doctype.tax_litigation_phase import TaxLitigationPhase


class TaxLitigationReclamacaoAdministrativa(TaxLitigationPhase):
    phase_label = 'Reclamação Administrativa'
    next_doctype = 'Tax Litigation Recurso Hierarquico'
