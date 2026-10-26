"""Orchestration: document bytes in, versioned analysis JSON out."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .adapters.itr_adapter_registry import select_pdf_adapter
from .calculations import ZERO
from .exceptions import (
    DocumentTooLargeError,
    EncryptedDocumentError,
    InvalidDocumentError,
    OCRUnavailableError,
    UnsupportedDocumentError,
)
from .json_parser import load_json_bytes, looks_like_json, parse_itr_json
from .models import (
    FIELD_SPECS,
    SCHEMA_VERSION,
    CheckStatus,
    ExtractedReturn,
    ExtractionStatus,
    ValueStatus,
    format_money,
)
from .normalizer import financial_year_for, normalize_assessment_year
from .ocr import NullOCRBackend, OCRBackend
from .pdf_extractor import extract_pdf, is_pdf_bytes
from .risk import RiskEngine, RuleConfig
from .security import get_logger, mask_pan
from .tax_rules import get_rules
from .validation import ValidationCheck, validate
from .year_over_year import compare_years

log = get_logger(__name__)

DEFAULT_MAX_BYTES = 15 * 1024 * 1024
MIN_SUCCESS_CONFIDENCE = Decimal("0.70")

DISCLAIMER = (
    "This report is an automated, rule-based analysis of the supplied document. It is not tax advice, not an "
    "audit, and not a lending or credit decision. Validation findings indicate internal inconsistencies or missing "
    "information for manual review; they are not evidence of fraud or of creditworthiness."
)

# canonical key -> (output section, output key)
OUTPUT_MAP: dict[str, tuple[str, str]] = {
    "return.filing_date": ("return_details", "filing_date"),
    "return.due_date": ("return_details", "due_date"),
    "return.return_type": ("return_details", "return_type"),
    "return.acknowledgement_number": ("return_details", "acknowledgement_number"),
    "return.tax_regime": ("return_details", "tax_regime"),
    "return.year_ending": ("return_details", "previous_year_ending"),
    "return.schema_version": ("return_details", "source_schema_version"),
    "taxpayer.name": ("taxpayer", "name"),
    "taxpayer.status": ("taxpayer", "status"),
    "taxpayer.residential_status": ("taxpayer", "residential_status"),
    "income.salary": ("income", "salary"),
    "income.house_property": ("income", "house_property"),
    "income.business_profession": ("income", "business_profession"),
    "income.capital_gains": ("income", "capital_gains"),
    "income.other_sources": ("income", "other_sources"),
    "income.agricultural_income": ("income", "agricultural_income"),
    "income.exempt_income": ("income", "exempt_income"),
    "income.gross_total_income": ("income", "gross_total_income_reported"),
    "income.deductions": ("income", "deductions_reported"),
    "income.total_income": ("income", "total_income_reported"),
    "income.taxable_income": ("income", "taxable_income_reported"),
    "tax.rate_base_income": ("tax_computation", "rate_base_income"),
    "tax.tax_on_total_income": ("tax_computation", "tax_on_total_income"),
    "tax.rebate_agricultural": ("tax_computation", "rebate_agricultural"),
    "tax.net_tax_on_total_income": ("tax_computation", "net_tax_on_total_income"),
    "tax.rebate_87a": ("tax_computation", "rebate_87a"),
    "tax.relief_89": ("tax_computation", "relief_89"),
    "tax.surcharge": ("tax_computation", "surcharge"),
    "tax.cess": ("tax_computation", "cess"),
    "tax.total_tax_liability": ("tax_computation", "total_tax_liability_reported"),
    "tax.interest": ("tax_computation", "interest"),
    "tax.tds": ("tax_computation", "tds"),
    "tax.tcs": ("tax_computation", "tcs"),
    "tax.advance_tax": ("tax_computation", "advance_tax"),
    "tax.self_assessment_tax": ("tax_computation", "self_assessment_tax"),
    "tax.total_taxes_paid": ("tax_computation", "total_taxes_paid_reported"),
    "tax.refund": ("tax_computation", "refund_reported"),
    "tax.demand": ("tax_computation", "demand_reported"),
    "business.presumptive_section": ("business", "presumptive_section"),
    "business.gross_turnover": ("business", "gross_turnover"),
    "business.turnover_digital": ("business", "turnover_digital"),
    "business.turnover_cash": ("business", "turnover_cash"),
    "business.turnover_other": ("business", "turnover_other"),
    "business.actual_profit": ("business", "actual_profit"),
    "business.deemed_profit": ("business", "deemed_profit_stated"),
    "business.declared_profit": ("business", "declared_profit"),
    "business.tax_audit_applicable": ("business", "tax_audit_applicable"),
}

CALCULATED_OUTPUTS = {
    "calc.income.sum_of_heads": ("income", "gross_total_income_calculated"),
    "calc.income.taxable_rounded": ("income", "taxable_income_calculated"),
    "calc.tax.liability": ("tax_computation", "total_tax_liability_calculated"),
    "calc.tax.total_paid": ("tax_computation", "total_taxes_paid_calculated"),
    "calc.tax.net_position": ("tax_computation", "net_position_calculated"),
    "calc.business.deemed_profit_44ad": ("business", "deemed_profit_calculated"),
}


@dataclass
class AnalysisOptions:
    assessment_year: str | None = None
    ocr_backend: OCRBackend = field(default_factory=NullOCRBackend)
    rules: RuleConfig = field(default_factory=RuleConfig)
    max_bytes: int = DEFAULT_MAX_BYTES
    allow_unverified_schemas: bool = True
    tolerance: Decimal = ZERO


@dataclass
class AnalysisResult:
    report: dict[str, Any]
    _pan: str | None = field(default=None, repr=False)
    _name: str | None = field(default=None, repr=False)

    @property
    def report_id(self) -> str:
        return self.report["report_id"]

    @property
    def analyzable(self) -> bool:
        return self.report["document"]["extraction_status"] in ("success", "partial")

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.report, indent=indent, ensure_ascii=False)

    def write_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    def write_pdf(self, path: str | Path, **kwargs: Any) -> Path:
        from .report import render_pdf

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        render_pdf(self.report, path, **kwargs)
        return path


@dataclass
class BatchResult:
    results: list[AnalysisResult]
    year_over_year: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "document_count": len(self.results),
            "reports": [
                {
                    "report_id": r.report_id,
                    "source_name": r.report["document"]["source_name"],
                    "assessment_year": r.report["assessment_year"],
                    "extraction_status": r.report["document"]["extraction_status"],
                    "validation_status": r.report["validation"]["status"],
                    "indicator_codes": [i["code"] for i in r.report["risk_indicators"]],
                }
                for r in self.results
            ],
            "year_over_year": self.year_over_year,
            "disclaimer": DISCLAIMER,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
def analyze_bytes(data: bytes, source_name: str, options: AnalysisOptions | None = None) -> AnalysisResult:
    """Analyse one document held in memory. Never raises for document problems."""
    options = options or AnalysisOptions()
    report_id = str(uuid.uuid4())
    sha = hashlib.sha256(data).hexdigest()
    doc_info: dict[str, Any] = {
        "source_type": None,
        "document_kind": None,
        "source_name": source_name,
        "sha256": sha,
        "size_bytes": len(data),
        "page_count": None,
        "itr_form": None,
        "adapter": None,
        "adapter_version": None,
        "extraction_status": ExtractionStatus.FAILED.value,
        "extraction_confidence": None,
        "schema_verified": None,
        "warnings": [],
    }
    risk = RiskEngine(options.rules)
    ret: ExtractedReturn | None = None
    status_message: str | None = None

    try:
        if len(data) > options.max_bytes:
            raise DocumentTooLargeError(f"Document exceeds the {options.max_bytes} byte limit.")
        if is_pdf_bytes(data):
            doc_info["source_type"] = "pdf"
            ret, status, status_message = _analyze_pdf(data, source_name, options, doc_info)
        elif looks_like_json(data):
            doc_info["source_type"] = "json"
            parsed = load_json_bytes(data)
            ret = parse_itr_json(parsed, source_name, allow_unverified=options.allow_unverified_schemas)
            status = None
        else:
            raise UnsupportedDocumentError("Only PDF and official ITR JSON documents are supported.")
    except EncryptedDocumentError as exc:
        status, status_message = ExtractionStatus.FAILED, str(exc)
    except (InvalidDocumentError, DocumentTooLargeError) as exc:
        status, status_message = ExtractionStatus.FAILED, str(exc)
    except UnsupportedDocumentError as exc:
        status, status_message = ExtractionStatus.UNSUPPORTED, str(exc)

    if ret is None:
        doc_info["extraction_status"] = status.value
        doc_info["warnings"].append(status_message)
        risk.document_status(status.value, status_message)
        report = _empty_report(report_id, doc_info, risk, status_message)
        log.info("analysis %s sha=%s status=%s", report_id, sha[:12], status.value)
        return AnalysisResult(report)

    # Assessment year handling ------------------------------------------------
    extra_checks: list[ValidationCheck] = []
    if options.assessment_year:
        requested = normalize_assessment_year(options.assessment_year)
        found = ret.get("return.assessment_year")
        if found is None:
            ret.set("return.assessment_year", requested, method="user_supplied", status=ValueStatus.USER_SUPPLIED.value)
            ret.warnings.append("Assessment year not found in the document; using the caller-supplied value.")
        elif found != requested:
            extra_checks.append(
                ValidationCheck(
                    "integrity.assessment_year_matches_request", "integrity", CheckStatus.FAIL,
                    "Assessment year matches the requested year",
                    f"The document is for AY {found} but AY {requested} was requested.",
                    ["return.assessment_year"],
                )
            )
    ay = ret.get("return.assessment_year")
    if ay:
        ret.set("return.financial_year", financial_year_for(ay), method="calculation", status=ValueStatus.CALCULATED.value)

    rules = get_rules(ay)
    if ay and rules is None:
        ret.limitations.append(
            f"No statutory rule set is configured for AY {ay}; rule-dependent checks (e.g. 44AD) were skipped."
        )

    validator = validate(ret, rules, options.tolerance)
    checks = extra_checks + validator.checks

    adapter_key_fields = _key_fields_for(ret)
    confidence = (
        (Decimal(sum(1 for k in adapter_key_fields if ret.has(k))) / Decimal(len(adapter_key_fields))).quantize(Decimal("0.01"))
        if adapter_key_fields else None
    )
    if status is None:
        status = ExtractionStatus.SUCCESS if confidence is None or confidence >= MIN_SUCCESS_CONFIDENCE else ExtractionStatus.PARTIAL

    doc_info.update(
        {
            "document_kind": ret.document_kind,
            "itr_form": ret.get("return.itr_form"),
            "adapter": ret.adapter,
            "adapter_version": ret.adapter_version,
            "extraction_status": status.value,
            "extraction_confidence": str(confidence) if confidence is not None else None,
            "schema_verified": ret.schema_verified,
        }
    )
    doc_info["warnings"].extend(ret.warnings)

    risk.extraction_quality(confidence, ret.schema_verified)
    risk.from_checks(checks)
    risk.exempt_income(
        format_money(ret.get("income.agricultural_income")), format_money(ret.get("income.exempt_income"))
    )

    report = _build_report(report_id, doc_info, ret, checks, validator.calculated, risk, rules)
    log.info("analysis %s sha=%s status=%s validation=%s", report_id, sha[:12], status.value, report["validation"]["status"])
    return AnalysisResult(report, _pan=ret.get("taxpayer.pan"), _name=ret.get("taxpayer.name"))


def _key_fields_for(ret: ExtractedReturn) -> tuple[str, ...]:
    from .adapters.coi_text import ComputationOfIncomeAdapter
    from .adapters.itr1_json import ITR1JSONAdapter

    for cls in (ComputationOfIncomeAdapter, ITR1JSONAdapter):
        if cls.name == ret.adapter:
            return cls.key_fields
    return ()


def _analyze_pdf(
    data: bytes, source_name: str, options: AnalysisOptions, doc_info: dict[str, Any]
) -> tuple[ExtractedReturn | None, ExtractionStatus | None, str | None]:
    pdf = extract_pdf(data)
    doc_info["page_count"] = pdf.page_count
    ocr_used = False
    if pdf.is_scanned or pdf.image_only_pages:
        pages = pdf.image_only_pages
        if options.ocr_backend.is_available():
            try:
                texts = options.ocr_backend.ocr_pages(data, pages)
            except OCRUnavailableError:
                texts = {}
            for page in pdf.pages:
                if page.number in texts:
                    page.text, page.method = texts[page.number], "ocr_text"
                    page.char_count = len(page.text.strip())
            ocr_used = bool(texts)
            doc_info["warnings"].append(
                f"Text on page(s) {', '.join(map(str, pages))} was read with local OCR ({options.ocr_backend.name}); verify figures."
            )
        elif pdf.is_scanned:
            return None, ExtractionStatus.OCR_REQUIRED, (
                "The PDF is image-only (scanned). No text could be extracted and no local OCR backend is "
                "configured, so no figures were read."
            )
        else:
            doc_info["warnings"].append(
                f"Page(s) {', '.join(map(str, pages))} are image-only and were not read (no OCR backend configured)."
            )

    match = select_pdf_adapter(pdf)
    if match.adapter is None:
        if match.known_layout:
            doc_info["document_kind"] = match.known_layout.kind
            return None, ExtractionStatus.UNSUPPORTED, match.known_layout.message
        return None, ExtractionStatus.UNSUPPORTED, (
            "The PDF layout was not recognised as a supported ITR document (supported: text-based computation of income)."
        )
    ret = match.adapter.parse(pdf, source_name)
    if ocr_used:
        ret.limitations.append("Some figures were obtained by OCR and may contain recognition errors.")
    return ret, None, None


# ---------------------------------------------------------------------------
def _base_report(report_id: str, doc_info: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": __version__,
        "report_id": report_id,
        "generated_at": _now(),
        "assessment_year": None,
        "financial_year": None,
        "document": doc_info,
        "return_details": {k: None for s, k in OUTPUT_MAP.values() if s == "return_details"},
        "taxpayer": {"name": None, "pan_masked": None, "status": None, "residential_status": None},
        "income": {k: None for s, k in OUTPUT_MAP.values() if s == "income"}
        | {"gross_total_income_calculated": None, "taxable_income_calculated": None, "line_items": {}},
        "tax_computation": {k: None for s, k in OUTPUT_MAP.values() if s == "tax_computation"}
        | {
            "total_tax_liability_calculated": None,
            "total_taxes_paid_calculated": None,
            "net_position_calculated": None,
            "tds_details": [],
        },
        "business": {k: None for s, k in OUTPUT_MAP.values() if s == "business"} | {"deemed_profit_calculated": None},
        "validation": {"status": "not_analyzable", "summary": {}, "checks": []},
        "calculations": [],
        "year_over_year": {},
        "risk_indicators": [],
        "provenance": {},
        "audit": {"source_fields": {}, "conflicts": [], "malformed_values": [], "rule_set": None},
        "limitations": [],
        "disclaimer": DISCLAIMER,
    }


def _empty_report(report_id: str, doc_info: dict[str, Any], risk: RiskEngine, message: str | None) -> dict[str, Any]:
    report = _base_report(report_id, doc_info)
    report["risk_indicators"] = [i.to_dict() for i in risk.indicators]
    report["limitations"].append(message or "The document could not be analysed.")
    return report


def _build_report(
    report_id: str,
    doc_info: dict[str, Any],
    ret: ExtractedReturn,
    checks: list[ValidationCheck],
    calculated: dict[str, Any],
    risk: RiskEngine,
    rules: Any,
) -> dict[str, Any]:
    report = _base_report(report_id, doc_info)
    report["assessment_year"] = ret.get("return.assessment_year")
    report["financial_year"] = ret.get("return.financial_year")

    for key, fv in ret.fields.items():
        if key == "taxpayer.pan":
            report["taxpayer"]["pan_masked"] = mask_pan(fv.value)
            report["provenance"]["taxpayer.pan_masked"] = fv.provenance.to_dict()
            continue
        if key not in OUTPUT_MAP:
            continue
        section, out_key = OUTPUT_MAP[key]
        kind = FIELD_SPECS[key][0]
        report[section][out_key] = format_money(fv.value) if kind == "money" else fv.value
        report["provenance"][f"{section}.{out_key}"] = fv.provenance.to_dict()

    report["tax_computation"]["tax_regime"] = ret.get("return.tax_regime")

    for calc_id, (section, out_key) in CALCULATED_OUTPUTS.items():
        calc = calculated.get(calc_id)
        if calc is not None and calc.result is not None:
            report[section][out_key] = format_money(calc.result)
            report["provenance"][f"{section}.{out_key}"] = {
                "method": "calculation",
                "status": ValueStatus.CALCULATED.value,
                "calculation_id": calc_id,
            }
    report["calculations"] = [c.to_dict() for c in calculated.values()]

    report["income"]["line_items"] = {
        head.split(".", 1)[1]: [
            {"description": i.description, "amount": format_money(i.amount), "page": i.page} for i in items
        ]
        for head, items in ret.line_items.items()
        if head.startswith("income.")
    }
    report["tax_computation"]["tds_details"] = [
        {
            "deductor_tan": e.deductor_tan,
            "deductor_name": e.deductor_name,
            "income": format_money(e.income),
            "tds": format_money(e.tds),
            "section": e.section,
            "head": e.head,
            "page": e.page,
        }
        for e in ret.tds_entries
    ]

    counts: dict[str, int] = {}
    for c in checks:
        counts[c.status.value] = counts.get(c.status.value, 0) + 1
    needs_review = any(
        c.status in (CheckStatus.FAIL, CheckStatus.WARNING, CheckStatus.INSUFFICIENT_DATA) for c in checks
    ) or any(i.severity in ("medium", "high") for i in risk.indicators)
    report["validation"] = {
        "status": "review_required" if needs_review else "passed",
        "summary": counts,
        "checks": [c.to_dict() for c in checks],
    }
    report["risk_indicators"] = [i.to_dict() for i in risk.indicators]
    report["audit"] = {
        "source_fields": ret.source_fields,
        "conflicts": ret.conflicts,
        "malformed_values": ret.malformed,
        "rule_set": rules.version if rules else None,
    }
    missing = [
        FIELD_SPECS[k][1] for k in ("income.gross_total_income", "income.deductions", "return.itr_form", "return.filing_date")
        if not ret.has(k)
    ]
    report["limitations"] = list(ret.limitations)
    if missing:
        report["limitations"].append("Not available in this document: " + ", ".join(missing) + ".")
    report["limitations"].append(
        "Tax is not recomputed from slab rates and the eligibility of deductions, rebates and reliefs is not assessed."
    )
    return report


# ---------------------------------------------------------------------------
def analyze_file(path: str | Path, options: AnalysisOptions | None = None) -> AnalysisResult:
    path = Path(path)
    options = options or AnalysisOptions()
    size = path.stat().st_size
    if size > options.max_bytes:
        return _too_large(path.name, size, options)
    return analyze_bytes(path.read_bytes(), path.name, options)


def _too_large(name: str, size: int, options: AnalysisOptions) -> AnalysisResult:
    doc_info = {
        "source_type": None, "document_kind": None, "source_name": name, "sha256": None, "size_bytes": size,
        "page_count": None, "itr_form": None, "adapter": None, "adapter_version": None,
        "extraction_status": ExtractionStatus.FAILED.value, "extraction_confidence": None,
        "schema_verified": None, "warnings": [f"Document exceeds the {options.max_bytes} byte limit."],
    }
    risk = RiskEngine(options.rules)
    risk.document_status("failed", doc_info["warnings"][0])
    return AnalysisResult(_empty_report(str(uuid.uuid4()), doc_info, risk, doc_info["warnings"][0]))


def analyze_batch(
    documents: Iterable[tuple[bytes, str]], options: AnalysisOptions | None = None
) -> BatchResult:
    """Analyse several documents and compare assessment years per taxpayer."""
    options = options or AnalysisOptions()
    return _finalize_batch([analyze_bytes(data, name, options) for data, name in documents], options)


def analyze_paths(paths: Iterable[str | Path], options: AnalysisOptions | None = None) -> BatchResult:
    options = options or AnalysisOptions()
    return _finalize_batch([analyze_file(p, options) for p in paths], options)


def _finalize_batch(results: list[AnalysisResult], options: AnalysisOptions) -> BatchResult:
    groups = compare_years([(r._pan, r._name, r.report) for r in results])
    engine = RiskEngine(options.rules)
    by_id = {r.report_id: r for r in results}
    for group in groups:
        indicators = [i.to_dict() for i in engine.year_over_year(group)]
        for rid in group["report_ids"]:
            report = by_id[rid].report
            report["risk_indicators"].extend(indicators)
            if any(i["severity"] in ("medium", "high") for i in indicators) and report["validation"]["status"] == "passed":
                report["validation"]["status"] = "review_required"
    return BatchResult(results, groups)
