"""Optional, local-only OCR interface.

No OCR backend is required. When none is configured, image-only PDFs are
reported with status ``ocr_required`` instead of being silently skipped.

The bundled :class:`TesseractOCRBackend` uses PyMuPDF's built-in Tesseract
bridge and runs entirely on the local machine. It needs a local Tesseract
installation (``tesseract`` on PATH or ``TESSDATA_PREFIX`` set).
"""

from __future__ import annotations

import os
import shutil
from typing import Protocol

import pymupdf

from .exceptions import OCRUnavailableError


class OCRBackend(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def ocr_pages(self, pdf_bytes: bytes, page_numbers: list[int]) -> dict[int, str]:
        """Return ``{page_number: text}`` for the requested 1-based pages."""
        ...


class NullOCRBackend:
    name = "none"

    def is_available(self) -> bool:
        return False

    def ocr_pages(self, pdf_bytes: bytes, page_numbers: list[int]) -> dict[int, str]:
        raise OCRUnavailableError("No OCR backend configured.")


class TesseractOCRBackend:
    name = "tesseract"

    def __init__(self, language: str = "eng", dpi: int = 300):
        self.language = language
        self.dpi = dpi

    def is_available(self) -> bool:
        return bool(shutil.which("tesseract") or os.environ.get("TESSDATA_PREFIX"))

    def ocr_pages(self, pdf_bytes: bytes, page_numbers: list[int]) -> dict[int, str]:
        if not self.is_available():
            raise OCRUnavailableError("Tesseract is not installed locally.")
        out: dict[int, str] = {}
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            for number in page_numbers:
                page = doc[number - 1]
                textpage = page.get_textpage_ocr(language=self.language, dpi=self.dpi, full=True)
                out[number] = page.get_text("text", textpage=textpage)
        return out


def get_ocr_backend(name: str | None) -> OCRBackend:
    if not name or name == "none":
        return NullOCRBackend()
    if name == "tesseract":
        return TesseractOCRBackend()
    raise ValueError(f"Unknown OCR backend: {name}")
