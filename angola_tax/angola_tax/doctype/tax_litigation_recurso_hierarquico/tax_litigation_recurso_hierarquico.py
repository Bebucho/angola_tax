from angola_tax.angola_tax.doctype.tax_litigation_phase import TaxLitigationPhase


class TaxLitigationRecursoHierarquico(TaxLitigationPhase):
    phase_label = 'Recurso Hierárquico'
    next_doctype = 'Tax Litigation Impugnacao Judicial'
