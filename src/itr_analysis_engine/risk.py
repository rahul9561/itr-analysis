"""Explainable document-review indicators.

These indicators describe the *document*: completeness, internal consistency
and items that a reviewer should look at. They are deliberately separate from
any lender-specific underwriting policy, and they never produce a credit
score, a fraud label, or an approve / reject decision.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import CheckStatus, Severity
from .validation import ValidationCheck

RULES_VERSION = "2026.10.0"

DEFAULT_RULES: dict[str, dict[str, Any]] = {
    "OCR_REQUIRED": {"enabled": True, "severity": "high"},
    "UNSUPPORTED_DOCUMENT_FORMAT": {"enabled": True, "severity": "high"},
    "LOW_EXTRACTION_CONFIDENCE": {"enabled": True, "severity": "medium", "min_confidence": "0.70"},
    "SCHEMA_UNVERIFIED": {"enabled": True, "severity": "medium"},
    "MISSING_REQUIRED_FIELD": {"enabled": True, "severity": "medium"},
    "CONFLICTING_FIELD_VALUES": {"enabled": True, "severity": "high"},
    "INCOME_RECONCILIATION_MISMATCH": {"enabled": True, "severity": "high"},
    "TAX_COMPUTATION_MISMATCH": {"enabled": True, "severity": "medium"},
    "TAX_PAYMENT_RECONCILIATION_MISMATCH": {"enabled": True, "severity": "medium"},
    "BUSINESS_TURNOVER_REVIEW": {"enabled": True, "severity": "medium"},
    "EXEMPT_INCOME_DISCLOSED": {"enabled": True, "severity": "info", "min_amount": "1"},
    "YEAR_OVER_YEAR_INCOME_CHANGE": {"enabled": True, "severity": "medium", "threshold_pct": "50"},
    "NON_CONSECUTIVE_YEARS": {"enabled": True, "severity": "low"},
    "DUPLICATE_ASSESSMENT_YEAR": {"enabled": True, "severity": "medium"},
    "LATE_FILING": {"enabled": True, "severity": "low"},
}


@dataclass
class RiskIndicator:
    code: str
    severity: str
    title: str
    explanation: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    suggested_action: str = ""
    rule_version: str = RULES_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "explanation": self.explanation,
            "evidence": self.evidence,
            "suggested_action": self.suggested_action,
            "rule_version": self.rule_version,
        }


class RuleConfig:
    """Rule switches, severities and thresholds. Load overrides from a JSON file or dict."""

    def __init__(self, overrides: dict[str, dict[str, Any]] | None = None):
        self.rules = copy.deepcopy(DEFAULT_RULES)
        for code, values in (overrides or {}).items():
            if code not in self.rules:
                raise ValueError(f"Unknown rule code: {code}")
            if "severity" in values and values["severity"] not in {s.value for s in Severity}:
                raise ValueError(f"Invalid severity for {code}: {values['severity']}")
            self.rules[code].update(values)

    @classmethod
    def from_file(cls, path: str | Path) -> "RuleConfig":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def enabled(self, code: str) -> bool:
        return bool(self.rules[code]["enabled"])

    def severity(self, code: str) -> str:
        return self.rules[code]["severity"]

    def param(self, code: str, name: str) -> Decimal:
        return Decimal(str(self.rules[code][name]))


INCOME_CHECK_PREFIXES = ("income.",)
TAX_CHECK_PREFIXES = ("tax.",)
PAYMENT_CHECK_PREFIXES = ("payments.",)


def _check_evidence(check: ValidationCheck) -> dict[str, Any]:
    d = check.to_dict()
    keep = ("id", "status", "field_refs", "expected_calculated", "actual_reported", "difference")
    return {k: d[k] for k in keep if k in d}


class RiskEngine:
    def __init__(self, config: RuleConfig | None = None):
        self.config = config or RuleConfig()
        self.indicators: list[RiskIndicator] = []

    def emit(self, code: str, title: str, explanation: str, evidence: list[dict[str, Any]], action: str) -> None:
        if not self.config.enabled(code):
            return
        self.indicators.append(
            RiskIndicator(code, self.config.severity(code), title, explanation, evidence, action)
        )

    # Document-level ----------------------------------------------------------
    def document_status(self, status: str, message: str | None) -> None:
        if status == "ocr_required":
            self.emit(
                "OCR_REQUIRED", "Scanned document - OCR required",
                "The PDF contains page images without extractable text. No figures were read from it.",
                [{"extraction_status": status}],
                "Obtain a text-based PDF or the official ITR JSON, or enable a local OCR backend and re-run.",
            )
        elif status in ("unsupported", "failed"):
            self.emit(
                "UNSUPPORTED_DOCUMENT_FORMAT", "Document could not be analysed",
                message or "The document format, form or schema version is not supported.",
                [{"extraction_status": status}],
                "Review the document manually or provide a supported format (text-based computation PDF or ITR-1 JSON).",
            )

    def extraction_quality(self, confidence: Decimal | None, schema_verified: bool) -> None:
        if confidence is not None and confidence < self.config.param("LOW_EXTRACTION_CONFIDENCE", "min_confidence"):
            self.emit(
                "LOW_EXTRACTION_CONFIDENCE", "Low extraction confidence",
                f"Only {confidence * 100:.0f}% of the key fields expected for this document type were extracted.",
                [{"extraction_confidence": str(confidence)}],
                "Compare the extracted figures with the source document before relying on them.",
            )
        if not schema_verified:
            self.emit(
                "SCHEMA_UNVERIFIED", "JSON mapping not verified against official schema",
                "The JSON field mapping for this form has not been verified against the official CBDT schema.",
                [],
                "Spot-check key figures against the ITR acknowledgement or the e-filing portal.",
            )

    # Check-based -------------------------------------------------------------
    def from_checks(self, checks: list[ValidationCheck]) -> None:
        by_status = lambda prefixes, statuses: [  # noqa: E731
            c for c in checks if c.id.startswith(prefixes) and c.status in statuses
        ]
        missing = [c for c in checks if c.id.startswith("required.") and c.status == CheckStatus.FAIL]
        if missing:
            self.emit(
                "MISSING_REQUIRED_FIELD", "Required fields missing",
                f"{len(missing)} required field(s) could not be found. Missing values are reported as unavailable, "
                "not as zero, and are not evidence about the taxpayer.",
                [{"field": ref} for c in missing for ref in c.field_refs],
                "Obtain the missing details from the full return or the e-filing portal.",
            )
        conflicts = [c for c in checks if c.id == "integrity.conflicting_values" and c.status == CheckStatus.FAIL]
        if conflicts:
            self.emit(
                "CONFLICTING_FIELD_VALUES", "Contradictory values within the document",
                conflicts[0].explanation,
                [_check_evidence(c) for c in conflicts],
                "Check whether the document pages belong to the same return and taxpayer.",
            )
        income_fail = by_status(INCOME_CHECK_PREFIXES, (CheckStatus.FAIL,))
        if income_fail:
            self.emit(
                "INCOME_RECONCILIATION_MISMATCH", "Income figures do not reconcile",
                "One or more income totals differ from the sum of their reported components. This can result from "
                "an extraction gap, set-off of losses, or an error in the document; it is not by itself evidence of wrongdoing.",
                [_check_evidence(c) for c in income_fail],
                "Recompute the income heads from the source document and ask the taxpayer's preparer about the difference.",
            )
        tax_fail = by_status(TAX_CHECK_PREFIXES, (CheckStatus.FAIL,))
        if tax_fail:
            self.emit(
                "TAX_COMPUTATION_MISMATCH", "Tax computation lines do not reconcile",
                "The reported tax lines are not arithmetically consistent with one another.",
                [_check_evidence(c) for c in tax_fail],
                "Verify the tax computation against the filed return / intimation u/s 143(1).",
            )
        pay_fail = by_status(PAYMENT_CHECK_PREFIXES, (CheckStatus.FAIL,))
        if pay_fail:
            self.emit(
                "TAX_PAYMENT_RECONCILIATION_MISMATCH", "Tax payments / refund do not reconcile",
                "Tax payments, credits and the refund or payable figure do not agree with the reported liability.",
                [_check_evidence(c) for c in pay_fail],
                "Compare TDS / tax payments with Form 26AS or AIS and the refund status on the e-filing portal.",
            )
        biz = [c for c in checks if c.id.startswith("business.") and c.status in (CheckStatus.FAIL, CheckStatus.WARNING)]
        if biz:
            self.emit(
                "BUSINESS_TURNOVER_REVIEW", "Business turnover / presumptive income needs review",
                "Presumptive-taxation figures did not pass one or more checks: " + "; ".join(c.title for c in biz) + ".",
                [_check_evidence(c) for c in biz],
                "Review turnover evidence (bank statements, GST returns) and confirm the presumptive scheme applies.",
            )
        late = [c for c in checks if c.id == "integrity.filed_after_due_date"]
        if late:
            self.emit(
                "LATE_FILING", "Filed after the stated due date", late[0].explanation,
                [_check_evidence(late[0])],
                "Confirm the filing date and whether the return was belated.",
            )

    def exempt_income(self, agricultural: str | None, other_exempt: str | None) -> None:
        threshold = self.config.param("EXEMPT_INCOME_DISCLOSED", "min_amount")
        evidence = []
        for key, raw in (("income.agricultural_income", agricultural), ("income.exempt_income", other_exempt)):
            if raw is not None and Decimal(raw) >= threshold:
                evidence.append({"field": key, "value": raw})
        if evidence:
            self.emit(
                "EXEMPT_INCOME_DISCLOSED", "Exempt income disclosed",
                "The return discloses exempt income (for example agricultural income). Exempt income is not part of "
                "taxable income and is typically not corroborated by TDS records.",
                evidence,
                "If this income is relied upon, obtain supporting evidence (e.g. land records, sale receipts).",
            )

    def year_over_year(self, group: dict[str, Any]) -> list[RiskIndicator]:
        """Indicators for a taxpayer group; returned (not stored) so they can be attached per report."""
        out: list[RiskIndicator] = []
        threshold = self.config.param("YEAR_OVER_YEAR_INCOME_CHANGE", "threshold_pct")
        big = [
            c for c in group["comparisons"]
            if c["metric"] in ("income.gross_total_income", "income.taxable_income_reported")
            and c.get("change_pct") is not None and abs(Decimal(c["change_pct"])) >= threshold
        ]
        if big and self.config.enabled("YEAR_OVER_YEAR_INCOME_CHANGE"):
            out.append(
                RiskIndicator(
                    "YEAR_OVER_YEAR_INCOME_CHANGE", self.config.severity("YEAR_OVER_YEAR_INCOME_CHANGE"),
                    "Large year-over-year income change",
                    f"Income changed by {threshold}% or more between assessment years. Large changes are common and "
                    "often legitimate (new job, business growth, one-off income).",
                    [{k: c[k] for k in ("metric", "from_assessment_year", "to_assessment_year", "from_value", "to_value", "change_pct")} for c in big],
                    "Ask for an explanation of the change and supporting documents.",
                )
            )
        if any(not c["consecutive_years"] for c in group["comparisons"]) and self.config.enabled("NON_CONSECUTIVE_YEARS"):
            out.append(
                RiskIndicator(
                    "NON_CONSECUTIVE_YEARS", self.config.severity("NON_CONSECUTIVE_YEARS"),
                    "Assessment years are not consecutive",
                    "The supplied returns skip at least one assessment year.",
                    [{"assessment_years": group["assessment_years"]}],
                    "Request the missing year's return or confirm none was filed.",
                )
            )
        if group["duplicate_assessment_years"] and self.config.enabled("DUPLICATE_ASSESSMENT_YEAR"):
            out.append(
                RiskIndicator(
                    "DUPLICATE_ASSESSMENT_YEAR", self.config.severity("DUPLICATE_ASSESSMENT_YEAR"),
                    "More than one document for the same assessment year",
                    "Several documents were supplied for the same assessment year (for example original and revised "
                    "returns); they were excluded from the year-over-year comparison.",
                    [{"assessment_years": group["duplicate_assessment_years"]}],
                    "Identify which document is the latest valid return for each year.",
                )
            )
        return out
