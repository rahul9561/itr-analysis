"""Output JSON contract, PDF report and CLI."""

import json
import re

import pymupdf
import pytest

from itr_analysis_engine import SCHEMA_VERSION, analyze_bytes
from itr_analysis_engine.cli import main
from itr_analysis_engine.report import _inr, render_pdf_bytes
from tests.fixtures import synthetic as syn

TOP_LEVEL = {
    "schema_version", "engine_version", "report_id", "generated_at", "assessment_year", "financial_year", "document",
    "return_details", "taxpayer", "income", "tax_computation", "business", "validation", "calculations",
    "year_over_year", "risk_indicators", "provenance", "audit", "limitations", "disclaimer",
}
INCOME_KEYS = {
    "salary", "house_property", "business_profession", "capital_gains", "other_sources",
    "gross_total_income_reported", "deductions_reported", "taxable_income_reported",
}
MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")


@pytest.fixture(scope="module")
def reports():
    return {
        "pdf": analyze_bytes(syn.text_pdf(syn.business_coi()), "biz.pdf").report,
        "json": analyze_bytes(syn.itr1_json_bytes(), "r.json").report,
        "scan": analyze_bytes(syn.scanned_pdf(), "scan.pdf").report,
    }


@pytest.mark.parametrize("kind", ["pdf", "json", "scan"])
def test_contract_shape(reports, kind):
    r = reports[kind]
    assert TOP_LEVEL <= set(r)
    assert r["schema_version"] == SCHEMA_VERSION
    assert INCOME_KEYS <= set(r["income"])
    assert {"source_type", "itr_form", "extraction_status", "warnings"} <= set(r["document"])
    assert set(r["taxpayer"]) >= {"name", "pan_masked"}
    assert r["validation"]["status"] in {"passed", "review_required", "not_analyzable"}
    for ind in r["risk_indicators"]:
        assert {"code", "severity", "title", "explanation", "evidence", "suggested_action", "rule_version"} <= set(ind)
    json.dumps(r)  # serialisable


@pytest.mark.parametrize("kind", ["pdf", "json"])
def test_money_serialised_as_exact_strings(reports, kind):
    r = reports[kind]
    for section in ("income", "tax_computation"):
        for key, value in r[section].items():
            if isinstance(value, str) and key not in ("tax_regime",):
                assert MONEY_RE.match(value), (section, key, value)


def test_provenance_present(reports):
    prov = reports["pdf"]["provenance"]["income.house_property"]
    assert prov == {"source_document": "biz.pdf", "method": "pdf_text", "status": "reported", "page": 1,
                    "source_label": "Income from House Property"}
    assert reports["json"]["provenance"]["income.salary"]["json_path"].startswith("$.ITR.ITR1.")


@pytest.mark.parametrize("kind", ["pdf", "json"])
def test_no_unmasked_pan_in_output(reports, kind):
    text = json.dumps(reports[kind])
    for pan in (syn.PAN_A, "CCCPX4321X"):
        assert pan not in text
    assert reports[kind]["taxpayer"]["pan_masked"].startswith("XXXXX")


def test_inr_formatting():
    assert _inr("434520.00", "₹") == "₹4,34,520"
    assert _inr("12345678.50", "₹") == "₹1,23,45,678.50"
    assert _inr("-7438.00", "₹") == "−₹7,438"
    assert _inr(None, "₹") == "Not available"


@pytest.mark.parametrize("kind", ["pdf", "json", "scan"])
def test_pdf_report(reports, kind):
    data = render_pdf_bytes(reports[kind])
    assert data.startswith(b"%PDF")
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        text = "\n".join(p.get_text() for p in doc)
    assert reports[kind]["report_id"] in text
    assert "not tax advice" in text
    assert syn.PAN_A not in text and "CCCPX4321X" not in text
    if kind == "pdf":
        assert "₹2,17,000" in text  # taxable income
        assert "Not available" in text  # salary absent -> not shown as zero
        assert "Validation findings" in text
    if kind == "scan":
        assert "OCR" in text and "₹" not in text


# CLI ---------------------------------------------------------------------------
def test_cli_analyze_pdf(tmp_path, capsys):
    src = tmp_path / "in.pdf"
    src.write_bytes(syn.text_pdf(syn.business_coi()))
    code = main(["analyze", str(src), "--json", str(tmp_path / "a.json"), "--pdf", str(tmp_path / "a.pdf")])
    assert code == 0
    report = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))
    assert report["assessment_year"] == "2025-26"
    assert (tmp_path / "a.pdf").read_bytes().startswith(b"%PDF")
    err = capsys.readouterr().err
    assert syn.PAN_A not in err and "TEST PERSON ALPHA" not in err


def test_cli_analyze_json_stdout(tmp_path, capsys):
    src = tmp_path / "r.json"
    src.write_bytes(syn.itr1_json_bytes())
    assert main(["analyze", str(src), "--ay", "2025-26"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["document"]["itr_form"] == "ITR-1"
    assert "CCCPX4321X" not in out


def test_cli_fail_on_review(tmp_path):
    src = tmp_path / "r.json"
    src.write_bytes(syn.itr1_json_bytes())
    assert main(["analyze", str(src), "--fail-on-review", "--json", str(tmp_path / "o.json")]) == 1


def test_cli_exit_codes(tmp_path):
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(syn.scanned_pdf())
    assert main(["analyze", str(scan), "--json", str(tmp_path / "s.json")]) == 3
    assert main(["analyze", str(tmp_path / "missing.pdf")]) == 2
    assert main(["analyze", str(scan), "--ay", "nonsense"]) == 2
    rules = tmp_path / "rules.json"
    rules.write_text('{"NOPE": {}}', encoding="utf-8")
    assert main(["analyze", str(scan), "--rules", str(rules)]) == 2


def test_cli_batch(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(syn.text_pdf(syn.salary_coi(ay="2024-2025", year_end="31/03/2024")))
    (folder / "b.pdf").write_bytes(syn.text_pdf(syn.salary_coi()))
    (folder / "notes.txt").write_text("ignored", encoding="utf-8")
    out = tmp_path / "out"
    assert main(["batch", str(folder), "--out-dir", str(out), "--pdf"]) == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["document_count"] == 2
    assert summary["year_over_year"][0]["assessment_years"] == ["2024-25", "2025-26"]
    assert len(list(out.glob("*.pdf"))) == 2
    assert main(["batch", str(tmp_path / "nope"), "--out-dir", str(out)]) == 2
