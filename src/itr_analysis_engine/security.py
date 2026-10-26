"""PII handling: PAN masking, log redaction and safe file names."""

from __future__ import annotations

import logging
import re
import unicodedata

PAN_RE = re.compile(r"\b([A-Z]{5})(\d{4})([A-Z])\b")
PAN_FULL_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
# 12-digit Aadhaar numbers, optionally grouped 4-4-4.
AADHAAR_RE = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")


def is_valid_pan_format(pan: str | None) -> bool:
    return bool(pan) and bool(PAN_FULL_RE.match(pan))


def mask_pan(pan: str | None) -> str | None:
    """Return a masked PAN such as ``XXXXX1234X``; keeps only digits 6-9 and the check letter.

    Malformed values are fully masked rather than echoed back.
    """
    if not pan:
        return None
    pan = pan.strip().upper()
    if not is_valid_pan_format(pan):
        return "X" * len(pan)
    return "XXXXX" + pan[5:]


def redact_text(text: str) -> str:
    """Mask PANs and Aadhaar-like numbers inside free text."""
    text = PAN_RE.sub(lambda m: "XXXXX" + m.group(2) + m.group(3), text)
    return AADHAAR_RE.sub("XXXX-XXXX-XXXX", text)


class PIIRedactingFilter(logging.Filter):
    """Logging filter that masks PAN / Aadhaar patterns in every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed log call
            return True
        record.msg = redact_text(message)
        record.args = None
        return True


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(f, PIIRedactingFilter) for f in logger.filters):
        logger.addFilter(PIIRedactingFilter())
    return logger


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str, default: str = "document") -> str:
    """Strip directories and unsafe characters from a user-supplied file name."""
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _SAFE_NAME_RE.sub("_", name).strip("._")
    return name[:120] or default
