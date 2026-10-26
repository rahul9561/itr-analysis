"""PDF text extraction with PyMuPDF.

Documents are opened from memory; nothing is written to disk. Pages that carry
images but (almost) no text are classified as image-only, and a document whose
pages are all image-only is reported as requiring OCR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from .exceptions import EncryptedDocumentError, InvalidDocumentError

# A page with fewer extractable characters than this, and at least one image,
# is treated as scanned / image-only.
MIN_TEXT_CHARS_PER_PAGE = 40


@dataclass
class PageText:
    number: int  # 1-based
    text: str
    char_count: int
    image_count: int
    method: str = "pdf_text"

    @property
    def image_only(self) -> bool:
        return self.char_count < MIN_TEXT_CHARS_PER_PAGE and self.image_count > 0


@dataclass
class ExtractedPDF:
    page_count: int
    pages: list[PageText] = field(default_factory=list)
    metadata_producer: str | None = None

    @property
    def image_only_pages(self) -> list[int]:
        return [p.number for p in self.pages if p.image_only]

    @property
    def is_scanned(self) -> bool:
        return self.page_count > 0 and len(self.image_only_pages) == self.page_count

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.pages)


def is_pdf_bytes(data: bytes) -> bool:
    return data[:1024].lstrip().startswith(b"%PDF-")


def open_pdf(data: bytes) -> pymupdf.Document:
    if not data or not is_pdf_bytes(data):
        raise InvalidDocumentError("File is not a PDF (missing %PDF header).")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several internal error types
        raise InvalidDocumentError("PDF could not be opened; it may be corrupt.") from exc
    if doc.needs_pass:
        doc.close()
        raise EncryptedDocumentError("PDF is password protected; supply an unlocked copy.")
    if doc.page_count == 0:
        doc.close()
        raise InvalidDocumentError("PDF has no pages.")
    return doc


def extract_pdf(data: bytes) -> ExtractedPDF:
    """Extract per-page text from PDF bytes."""
    doc = open_pdf(data)
    try:
        result = ExtractedPDF(page_count=doc.page_count, metadata_producer=(doc.metadata or {}).get("producer"))
        for index, page in enumerate(doc):
            try:
                text = page.get_text("text", sort=False) or ""
            except Exception as exc:
                raise InvalidDocumentError(f"Text extraction failed on page {index + 1}.") from exc
            result.pages.append(
                PageText(
                    number=index + 1,
                    text=text,
                    char_count=len(text.strip()),
                    image_count=len(page.get_images(full=False)),
                )
            )
        return result
    finally:
        doc.close()
