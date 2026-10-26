"""Adapter interface.

An adapter recognises one document layout / schema version and maps it into
an :class:`~itr_analysis_engine.models.ExtractedReturn`. Adapters only read
what is printed in the source - they never compute or infer missing figures.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import ExtractedReturn
from ..pdf_extractor import ExtractedPDF


class PDFTextAdapter(ABC):
    name: str = "base"
    version: str = "0"
    document_kind: str = "unknown"
    # Whether the layout has been tested against real (redacted) samples.
    tested: bool = False

    @abstractmethod
    def detect(self, pdf: ExtractedPDF) -> float:
        """Return a confidence in [0, 1] that this adapter understands the layout."""

    @abstractmethod
    def parse(self, pdf: ExtractedPDF, source_document: str) -> ExtractedReturn:
        """Extract fields; must never raise for missing fields."""

    # Key fields used for the extraction-confidence score.
    key_fields: tuple[str, ...] = ()


class JSONAdapter(ABC):
    name: str = "base"
    version: str = "0"
    form: str = ""
    schema_verified: bool = False

    @abstractmethod
    def parse(self, data: dict[str, Any], source_document: str) -> ExtractedReturn:
        """Map an official ITR JSON document into an ExtractedReturn."""

    key_fields: tuple[str, ...] = ()
