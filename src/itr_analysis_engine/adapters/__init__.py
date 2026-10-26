from .base import JSONAdapter, PDFTextAdapter
from .coi_text import ComputationOfIncomeAdapter
from .itr1_json import ITR1JSONAdapter
from .itr_adapter_registry import PDF_ADAPTERS, select_pdf_adapter

__all__ = [
    "JSONAdapter",
    "PDFTextAdapter",
    "ComputationOfIncomeAdapter",
    "ITR1JSONAdapter",
    "PDF_ADAPTERS",
    "select_pdf_adapter",
]
