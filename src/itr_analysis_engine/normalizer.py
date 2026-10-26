"""Normalisation of identifiers, years, dates and names into stable formats."""

from __future__ import annotations

import re
from datetime import date, datetime

from .exceptions import MalformedValueError

_HONORIFICS_RE = re.compile(r"^(?:SMT|SH|SHRI|SHREE|MR|MRS|MS|KUM|KUMARI|DR|M/S)\.?\s*", re.IGNORECASE)


def normalize_assessment_year(raw: str | int | None) -> str | None:
    """Return an assessment year as ``YYYY-YY``.

    Accepts ``2025-2026``, ``2025-26``, ``2025 - 26`` and the official JSON
    convention where ``"2025"`` denotes AY 2025-26. Raises on inconsistent spans.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    m = re.fullmatch(r"(\d{4})\s*[-/–]\s*(\d{2}|\d{4})", text)
    if m:
        start = int(m.group(1))
        end_raw = m.group(2)
        end = int(end_raw) if len(end_raw) == 4 else (start // 100) * 100 + int(end_raw)
        if len(end_raw) == 2 and end < start:
            end += 100
        if end != start + 1:
            raise MalformedValueError(f"Assessment year span is not one year: {raw!r}")
        return f"{start}-{str(end)[-2:]}"
    if re.fullmatch(r"\d{4}", text):
        start = int(text)
        return f"{start}-{str(start + 1)[-2:]}"
    raise MalformedValueError(f"Unrecognised assessment year: {raw!r}")


def financial_year_for(assessment_year: str | None) -> str | None:
    """The previous year (FY) for an AY: AY 2025-26 -> FY 2024-25."""
    if not assessment_year:
        return None
    start = int(assessment_year[:4]) - 1
    return f"{start}-{str(start + 1)[-2:]}"


def fy_end_date(assessment_year: str) -> date:
    return date(int(assessment_year[:4]), 3, 31)


def parse_date(raw: str | None) -> str | None:
    """Parse ``dd/mm/yyyy``, ``dd-mm-yyyy`` or ISO dates into ISO ``yyyy-mm-dd``."""
    if not raw:
        return None
    text = str(raw).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise MalformedValueError(f"Unrecognised date: {raw!r}")


def clean_name(raw: str | None) -> str | None:
    if not raw:
        return None
    name = re.sub(r"\s+", " ", raw).strip()
    name = _HONORIFICS_RE.sub("", name).strip()
    return name or None


def normalize_status(raw: str | None) -> str | None:
    if not raw:
        return None
    return re.sub(r"\s+", " ", raw).strip().upper()
