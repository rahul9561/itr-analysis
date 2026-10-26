"""Registry of official ITR JSON schemas the engine can map.

Each entry records the form, the assessment years for which a mapping exists,
and whether that mapping has been verified against the official CBDT schema.
Unknown forms or years are rejected with a clear message instead of being
parsed on a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters.base import JSONAdapter
from .adapters.itr1_json import ITR1JSONAdapter
from .exceptions import UnsupportedDocumentError
from .normalizer import normalize_assessment_year


@dataclass(frozen=True)
class SchemaEntry:
    form_key: str  # key under the "ITR" root, e.g. "ITR1"
    form_name: str
    assessment_years: tuple[str, ...]
    adapter: type[JSONAdapter]
    verified: bool
    note: str


SCHEMAS: list[SchemaEntry] = [
    SchemaEntry(
        form_key="ITR1",
        form_name="ITR-1",
        assessment_years=("2024-25", "2025-26"),
        adapter=ITR1JSONAdapter,
        verified=False,
        note="Mapped from the published ITR-1 JSON structure; official schema not available locally.",
    ),
]

KNOWN_UNSUPPORTED_FORMS = {"ITR2": "ITR-2", "ITR3": "ITR-3", "ITR4": "ITR-4", "ITR5": "ITR-5", "ITR6": "ITR-6", "ITR7": "ITR-7"}


@dataclass
class DetectedSchema:
    entry: SchemaEntry
    assessment_year: str
    schema_version: str | None


def detect_schema(data: Any, allow_unverified: bool = True) -> DetectedSchema:
    """Identify the form and AY of an ITR JSON document, or raise UnsupportedDocumentError."""
    if not isinstance(data, dict) or not isinstance(data.get("ITR"), dict):
        raise UnsupportedDocumentError("JSON is not an official ITR return: missing top-level 'ITR' object.")
    forms = [k for k in data["ITR"] if isinstance(data["ITR"][k], dict)]
    if len(forms) != 1:
        raise UnsupportedDocumentError(f"Expected exactly one form under 'ITR', found {len(forms)}.")
    form_key = forms[0]
    entry = next((s for s in SCHEMAS if s.form_key == form_key), None)
    if entry is None:
        name = KNOWN_UNSUPPORTED_FORMS.get(form_key, form_key)
        raise UnsupportedDocumentError(
            f"{name} JSON is not supported yet. Supply the official {name} schema and a redacted sample to add an adapter."
        )
    form_block = data["ITR"][form_key].get(f"Form_{form_key}", {})
    raw_ay = form_block.get("AssessmentYear") if isinstance(form_block, dict) else None
    try:
        ay = normalize_assessment_year(raw_ay)
    except Exception as exc:
        raise UnsupportedDocumentError(f"{entry.form_name} JSON has an unrecognised AssessmentYear.") from exc
    if ay is None:
        raise UnsupportedDocumentError(f"{entry.form_name} JSON has no Form_{form_key}.AssessmentYear.")
    if ay not in entry.assessment_years:
        raise UnsupportedDocumentError(
            f"{entry.form_name} JSON for AY {ay} is not supported (supported: {', '.join(entry.assessment_years)})."
        )
    if not entry.verified and not allow_unverified:
        raise UnsupportedDocumentError(
            f"The {entry.form_name} mapping is unverified and unverified schemas are disabled by configuration."
        )
    return DetectedSchema(entry=entry, assessment_year=ay, schema_version=form_block.get("SchemaVer"))


def describe_supported() -> list[dict[str, Any]]:
    return [
        {"form": s.form_name, "assessment_years": list(s.assessment_years), "verified": s.verified, "note": s.note}
        for s in SCHEMAS
    ]
