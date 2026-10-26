"""Adapter for the official ITR-1 (Sahaj) JSON utility output.

IMPORTANT - verification status: no official CBDT schema (.json schema file)
or utility-generated ITR-1 JSON was available in this workspace when this
adapter was written. The field paths below follow the published ITR-1 JSON
structure (root ``ITR.ITR1`` with ``Form_ITR1``, ``PersonalInfo``,
``FilingStatus``, ``ITR1_IncomeDeductions``, ``ITR1_TaxComputation``,
``TaxPaid`` and ``Refund``) but have only been exercised against synthetic
fixtures. The schema registry therefore marks this adapter as *unverified*,
and every result produced by it carries a ``SCHEMA_UNVERIFIED`` review
indicator until it is validated against the official schema for each
supported assessment year.
"""

from __future__ import annotations

from typing import Any

from ..calculations import to_money
from ..exceptions import MalformedValueError
from ..models import ExtractedReturn
from ..normalizer import clean_name, normalize_assessment_year
from .base import JSONAdapter

ROOT = "ITR.ITR1"

# canonical key -> path below ITR.ITR1
MONEY_PATHS: dict[str, str] = {
    "income.salary": "ITR1_IncomeDeductions.IncomeFromSal",
    "income.house_property": "ITR1_IncomeDeductions.TotalIncomeOfHP",
    "income.other_sources": "ITR1_IncomeDeductions.IncomeOthSrc",
    "income.gross_total_income": "ITR1_IncomeDeductions.GrossTotIncome",
    "income.deductions": "ITR1_IncomeDeductions.DeductUndChapVIA.TotalChapVIADeductions",
    "income.taxable_income": "ITR1_IncomeDeductions.TotalIncome",
    "income.exempt_income": "ITR1_IncomeDeductions.ExemptIncAgriOthUs10.ExemptIncAgriOthUs10Total",
    "tax.tax_on_total_income": "ITR1_TaxComputation.TotalTaxPayable",
    "tax.rebate_87a": "ITR1_TaxComputation.Rebate87A",
    "tax.cess": "ITR1_TaxComputation.EducationCess",
    "tax.relief_89": "ITR1_TaxComputation.Section89",
    "tax.total_tax_liability": "ITR1_TaxComputation.NetTaxLiability",
    "tax.interest": "ITR1_TaxComputation.TotalIntrstPay",
    "tax.advance_tax": "TaxPaid.TaxesPaid.AdvanceTax",
    "tax.tds": "TaxPaid.TaxesPaid.TDS",
    "tax.tcs": "TaxPaid.TaxesPaid.TCS",
    "tax.self_assessment_tax": "TaxPaid.TaxesPaid.SelfAssessmentTax",
    "tax.total_taxes_paid": "TaxPaid.TaxesPaid.TotalTaxesPaid",
    "tax.demand": "TaxPaid.BalTaxPayable",
    "tax.refund": "Refund.RefundDue",
}

# ReturnFileSec codes. Only codes whose meaning is unambiguous are mapped;
# anything else is reported as "section code <n>".
RETURN_FILE_SEC = {
    11: "original (u/s 139(1))",
    12: "belated (u/s 139(4))",
    13: "revised (u/s 139(5))",
    21: "updated (u/s 139(8A))",
}


def get_path(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


class ITR1JSONAdapter(JSONAdapter):
    name = "itr1_json"
    version = "0.1"
    form = "ITR-1"
    schema_verified = False
    key_fields = (
        "return.assessment_year",
        "taxpayer.name",
        "taxpayer.pan",
        "income.gross_total_income",
        "income.taxable_income",
        "tax.total_tax_liability",
        "tax.total_taxes_paid",
    )

    def parse(self, data: dict[str, Any], source_document: str) -> ExtractedReturn:
        ret = ExtractedReturn(
            source_document=source_document,
            source_type="json",
            document_kind="itr_json",
            adapter=self.name,
            adapter_version=self.version,
            schema_verified=self.schema_verified,
        )
        root = get_path(data, ROOT) or {}

        def jp(path: str) -> str:
            return f"$.{ROOT}.{path}"

        def text(key: str, path: str, value: Any, label: str) -> None:
            if value not in (None, ""):
                ret.set(key, str(value), method="json_path", json_path=jp(path), label=label)
                ret.source_fields[jp(path)] = value

        form = get_path(root, "Form_ITR1.FormName")
        text("return.itr_form", "Form_ITR1.FormName", form, "FormName")
        ay_raw = get_path(root, "Form_ITR1.AssessmentYear")
        if ay_raw is not None:
            try:
                text("return.assessment_year", "Form_ITR1.AssessmentYear", normalize_assessment_year(ay_raw), "AssessmentYear")
                ret.source_fields[jp("Form_ITR1.AssessmentYear")] = ay_raw
            except MalformedValueError:
                ret.malformed.append({"field": "return.assessment_year", "json_path": jp("Form_ITR1.AssessmentYear")})
        schema_ver = get_path(root, "Form_ITR1.SchemaVer")
        text("return.schema_version", "Form_ITR1.SchemaVer", schema_ver, "SchemaVer")

        name_parts = [
            get_path(root, f"PersonalInfo.AssesseeName.{p}") for p in ("FirstName", "MiddleName", "SurNameOrOrgName")
        ]
        name = clean_name(" ".join(str(p) for p in name_parts if p))
        if name:
            ret.set("taxpayer.name", name, method="json_path", json_path=jp("PersonalInfo.AssesseeName"), label="AssesseeName")
        pan = get_path(root, "PersonalInfo.PAN")
        if pan:
            ret.set("taxpayer.pan", str(pan).upper(), method="json_path", json_path=jp("PersonalInfo.PAN"), label="PAN")

        sec = get_path(root, "FilingStatus.ReturnFileSec")
        if sec is not None:
            try:
                label = RETURN_FILE_SEC.get(int(sec), f"section code {sec}")
            except (TypeError, ValueError):
                label = f"section code {sec}"
            text("return.return_type", "FilingStatus.ReturnFileSec", label, "ReturnFileSec")
            ret.source_fields[jp("FilingStatus.ReturnFileSec")] = sec
        opt_out = get_path(root, "FilingStatus.OptOutNewTaxRegime")
        if opt_out in ("Y", "N"):
            text("return.tax_regime", "FilingStatus.OptOutNewTaxRegime", "old" if opt_out == "Y" else "new", "OptOutNewTaxRegime")
            ret.source_fields[jp("FilingStatus.OptOutNewTaxRegime")] = opt_out

        for key, path in MONEY_PATHS.items():
            raw = get_path(root, path)
            if raw is None:
                continue
            ret.source_fields[jp(path)] = raw
            try:
                value = to_money(raw)
            except MalformedValueError:
                ret.malformed.append({"field": key, "json_path": jp(path), "reason": "unparseable amount"})
                continue
            ret.set(key, value, method="json_path", json_path=jp(path), label=path.rsplit(".", 1)[-1])

        ret.limitations.append(
            "ITR-1 JSON field mapping has not been verified against the official CBDT schema in this deployment; "
            "figures are read from the documented field names and should be spot-checked."
        )
        ret.limitations.append(
            "ITR-1 reports TotalIncome already rounded u/s 288A; the unrounded total income is not available."
        )
        return ret
