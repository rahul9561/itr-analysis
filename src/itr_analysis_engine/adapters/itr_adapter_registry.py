"""Registry of PDF text-layout adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..pdf_extractor import ExtractedPDF
from .base import PDFTextAdapter
from .coi_text import ComputationOfIncomeAdapter

MIN_DETECTION_SCORE = 0.6

PDF_ADAPTERS: list[type[PDFTextAdapter]] = [ComputationOfIncomeAdapter]


@dataclass(frozen=True)
class KnownLayout:
    """A layout we can recognise but cannot yet parse."""

    kind: str
    pattern: re.Pattern[str]
    message: str


KNOWN_UNSUPPORTED_LAYOUTS = [
    KnownLayout(
        "itr_acknowledgement",
        re.compile(r"INCOME\s+TAX\s+RETURN\s+ACKNOWLEDGEMENT|\bITR-V\b", re.I),
        "ITR-V acknowledgement detected. No text-based acknowledgement sample was available to build and test "
        "a parser, so its figures are not extracted.",
    ),
]


@dataclass
class AdapterMatch:
    adapter: PDFTextAdapter | None
    score: float
    known_layout: KnownLayout | None = None


def select_pdf_adapter(pdf: ExtractedPDF) -> AdapterMatch:
    best: AdapterMatch = AdapterMatch(None, 0.0)
    for cls in PDF_ADAPTERS:
        adapter = cls()
        score = adapter.detect(pdf)
        if score > best.score:
            best = AdapterMatch(adapter, score)
    if best.score >= MIN_DETECTION_SCORE:
        return best
    for layout in KNOWN_UNSUPPORTED_LAYOUTS:
        if layout.pattern.search(pdf.text):
            return AdapterMatch(None, best.score, layout)
    return AdapterMatch(None, best.score)
