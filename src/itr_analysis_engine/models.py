"""Internal data model.

Adapters populate an :class:`ExtractedReturn`: a flat map of canonical field
keys (see :data:`FIELD_SPECS`) to :class:`FieldValue` objects, each carrying
provenance. Missing values are simply absent - never zero.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

SCHEMA_VERSION = "1.0"


class ValueStatus(str, Enum):
    REPORTED = "reported"        # read from the source document
    CALCULATED = "calculated"    # computed by this engine
    USER_SUPPLIED = "user_supplied"


class ExtractionStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    OCR_REQUIRED = "ocr_required"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_APPLICABLE = "not_applicable"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Canonical fields: key -> (kind, human label). kind is "money", "text" or "date".
FIELD_SPECS: dict[str, tuple[str, str]] = {
    # Return identification
    "return.assessment_year": ("text", "Assessment year"),
    "return.financial_year": ("text", "Financial year"),
    "return.year_ending": ("date", "Previous year ending"),
    "return.itr_form": ("text", "ITR form"),
    "return.filing_date": ("date", "Return filing date"),
    "return.due_date": ("date", "Due date of filing"),
    "return.return_type": ("text", "Return type (original / revised / updated)"),
    "return.acknowledgement_number": ("text", "Acknowledgement / receipt number"),
    "return.tax_regime": ("text", "Tax regime"),
    "return.schema_version": ("text", "Source schema version"),
    # Taxpayer (PAN is kept internally only and never serialised unmasked)
    "taxpayer.name": ("text", "Taxpayer name"),
    "taxpayer.pan": ("text", "PAN"),
    "taxpayer.status": ("text", "Taxpayer status"),
    "taxpayer.residential_status": ("text", "Residential status"),
    # Income
    "income.salary": ("money", "Income from salary"),
    "income.house_property": ("money", "Income from house property"),
    "income.business_profession": ("money", "Income from business / profession"),
    "income.capital_gains": ("money", "Capital gains"),
    "income.other_sources": ("money", "Income from other sources"),
    "income.agricultural_income": ("money", "Net agricultural income (exempt, for rate purposes)"),
    "income.exempt_income": ("money", "Other exempt income"),
    "income.gross_total_income": ("money", "Gross total income"),
    "income.deductions": ("money", "Deductions (Chapter VI-A)"),
    "income.total_income": ("money", "Total income (before rounding)"),
    "income.taxable_income": ("money", "Taxable / net assessable income"),
    # Tax computation
    "tax.rate_base_income": ("money", "Income on which tax rate is computed"),
    "tax.tax_on_total_income": ("money", "Tax on total income"),
    "tax.rebate_agricultural": ("money", "Rebate on agricultural income"),
    "tax.net_tax_on_total_income": ("money", "Net tax on total income"),
    "tax.rebate_87a": ("money", "Rebate u/s 87A"),
    "tax.relief_89": ("money", "Relief u/s 89"),
    "tax.surcharge": ("money", "Surcharge"),
    "tax.cess": ("money", "Health & education cess"),
    "tax.total_tax_liability": ("money", "Total tax liability"),
    "tax.interest": ("money", "Interest u/s 234A/B/C"),
    "tax.tds": ("money", "TDS"),
    "tax.tcs": ("money", "TCS"),
    "tax.advance_tax": ("money", "Advance tax"),
    "tax.self_assessment_tax": ("money", "Self-assessment tax"),
    "tax.total_taxes_paid": ("money", "Total taxes paid / credits"),
    "tax.refund": ("money", "Refund claimed"),
    "tax.demand": ("money", "Tax payable / demand"),
    # Business
    "business.presumptive_section": ("text", "Presumptive taxation section"),
    "business.gross_turnover": ("money", "Gross turnover / receipts"),
    "business.turnover_digital": ("money", "Turnover via banking / digital modes"),
    "business.turnover_cash": ("money", "Turnover received in cash"),
    "business.turnover_other": ("money", "Turnover via any other mode"),
    "business.actual_profit": ("money", "Actual profit"),
    "business.deemed_profit": ("money", "Deemed profit (as stated)"),
    "business.declared_profit": ("money", "Profit offered for tax"),
    "business.tax_audit_applicable": ("text", "Tax audit applicable"),
}

INCOME_HEADS = (
    "income.salary",
    "income.house_property",
    "income.business_profession",
    "income.capital_gains",
    "income.other_sources",
)


@dataclass
class Provenance:
    source_document: str
    method: str  # pdf_text, ocr_text, json_path, calculation, user_supplied
    status: str = ValueStatus.REPORTED.value
    page: int | None = None
    json_path: str | None = None
    source_label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class FieldValue:
    value: Any
    provenance: Provenance


@dataclass
class LineItem:
    """A component line inside an income head (e.g. one interest entry)."""

    description: str
    amount: Decimal
    page: int | None = None


@dataclass
class TDSEntry:
    deductor_tan: str | None
    deductor_name: str | None
    income: Decimal | None
    tds: Decimal | None
    section: str | None = None
    head: str | None = None
    page: int | None = None


@dataclass
class ExtractedReturn:
    """Everything an adapter could read from one document."""

    source_document: str
    source_type: str  # pdf | json
    document_kind: str | None = None  # computation_of_income | itr_json | itr_acknowledgement
    adapter: str | None = None
    adapter_version: str | None = None
    fields: dict[str, FieldValue] = field(default_factory=dict)
    head_totals: dict[str, list[FieldValue]] = field(default_factory=dict)
    line_items: dict[str, list[LineItem]] = field(default_factory=dict)
    tds_entries: list[TDSEntry] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    malformed: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    source_fields: dict[str, Any] = field(default_factory=dict)  # json_path -> raw value (non-PII)
    schema_verified: bool = True

    def get(self, key: str) -> Any:
        fv = self.fields.get(key)
        return fv.value if fv else None

    def has(self, key: str) -> bool:
        return key in self.fields and self.fields[key].value is not None

    def set(
        self,
        key: str,
        value: Any,
        *,
        method: str,
        page: int | None = None,
        json_path: str | None = None,
        label: str | None = None,
        status: str = ValueStatus.REPORTED.value,
    ) -> None:
        """Record a value. A second, different value for the same key is a conflict;
        the first value is kept and the conflict is reported for manual review."""
        if key not in FIELD_SPECS:
            raise KeyError(f"Unknown canonical field: {key}")
        if value is None:
            return
        prov = Provenance(
            source_document=self.source_document,
            method=method,
            status=status,
            page=page,
            json_path=json_path,
            source_label=label,
        )
        existing = self.fields.get(key)
        if existing is not None:
            if existing.value != value:
                shown = _jsonable
                if key == "taxpayer.pan":
                    from .security import mask_pan

                    shown = mask_pan
                self.conflicts.append(
                    {
                        "field": key,
                        "kept": shown(existing.value),
                        "kept_provenance": existing.provenance.to_dict(),
                        "other": shown(value),
                        "other_provenance": prov.to_dict(),
                    }
                )
            return
        self.fields[key] = FieldValue(value, prov)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format_money(value)
    return value


def format_money(value: Decimal | None) -> str | None:
    """Serialise money as a fixed two-decimal string (exact, no float)."""
    if value is None:
        return None
    return f"{value.quantize(Decimal('0.01')):f}"
