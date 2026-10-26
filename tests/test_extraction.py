"""PDF and JSON extraction, including failure modes."""

from decimal import Decimal

import pytest

from itr_analysis_engine import analyze_bytes
from itr_analysis_engine.adapters.coi_text import ComputationOfIncomeAdapter
from itr_analysis_engine.exceptions import EncryptedDocumentError, InvalidDocumentError, UnsupportedDocumentError
from itr_analysis_engine.json_parser import parse_itr_json
from itr_analysis_engine.pdf_extractor import extract_pdf
from itr_analysis_engine.schema_registry import detect_schema
from tests.fixtures import synthetic as syn


def parse_coi(pages):
    pdf = extract_pdf(syn.text_pdf(pages))
    return ComputationOfIncomeAdapter().parse(pdf, "synthetic.pdf")


def test_text_pdf_extraction():
    pdf = extract_pdf(syn.text_pdf(syn.business_coi()))
    assert pdf.page_count == 2
    assert not pdf.is_scanned
    assert "Computation of Income" in pdf.text


def test_invalid_pdf_raises():
    with pytest.raises(InvalidDocumentError):
        extract_pdf(b"%PDF-1.7\nthis is not really a pdf")
    with pytest.raises(InvalidDocumentError):
        extract_pdf(b"not a pdf at all")


def test_encrypted_pdf_raises():
    with pytest.raises(EncryptedDocumentError):
        extract_pdf(syn.encrypted_pdf())


def test_scanned_pdf_detected():
    pdf = extract_pdf(syn.scanned_pdf())
    assert pdf.is_scanned
    assert pdf.image_only_pages == [1]


def test_business_coi_fields():
    ret = parse_coi(syn.business_coi())
    g = ret.get
    assert g("return.assessment_year") == "2025-26"
    assert g("taxpayer.name") == "TEST PERSON ALPHA"
    assert g("taxpayer.pan") == syn.PAN_A
    assert g("return.filing_date") == "2025-09-01"
    assert g("return.acknowledgement_number") == "900000000010925"
    assert g("return.tax_regime") == "new"
    assert g("income.house_property") == Decimal("42000")
    assert g("income.business_profession") == Decimal("150000")
    assert g("income.other_sources") == Decimal("25003")
    assert g("income.total_income") == Decimal("217003")
    assert g("income.taxable_income") == Decimal("217000")
    assert g("income.agricultural_income") == Decimal("100000")
    assert g("tax.rate_base_income") == Decimal("317000")
    assert g("tax.rebate_87a") == Decimal("3350")
    assert g("tax.total_tax_liability") == Decimal("0")
    assert g("tax.tds") == Decimal("2004")
    assert g("tax.refund") == Decimal("2000")
    assert g("business.presumptive_section") == "44AD"
    assert g("business.gross_turnover") == Decimal("500000")
    assert g("business.turnover_digital") == Decimal("300000")
    assert g("business.turnover_cash") == Decimal("200000")
    assert g("business.deemed_profit") == Decimal("34000")
    assert [i.amount for i in ret.line_items["income.house_property"]] == [Decimal("60000"), Decimal("-18000")]
    assert len(ret.tds_entries) == 1 and ret.tds_entries[0].section == "194A"
    # Missing heads are absent, not zero.
    assert not ret.has("income.salary")
    assert not ret.has("income.capital_gains")
    assert ret.fields["income.house_property"].provenance.page == 1


def test_account_numbers_masked_in_line_items():
    ret = parse_coi(syn.business_coi())
    descriptions = " ".join(i.description for i in ret.line_items["income.other_sources"])
    assert "11112222333" not in descriptions
    assert "2333" in descriptions


def test_salary_coi_fields():
    ret = parse_coi(syn.salary_coi())
    assert ret.get("income.salary") == Decimal("550000")
    assert [i.amount for i in ret.line_items["income.salary"]] == [Decimal("300000"), Decimal("250000")]
    assert ret.get("income.taxable_income") == Decimal("551550")
    assert ret.get("tax.demand") == Decimal("0")
    assert not ret.has("tax.refund")


def test_conflicting_pan_recorded_and_masked():
    pages = syn.business_coi()
    pages[1] = [line.replace(f"holding PAN  {syn.PAN_A}", "holding PAN  ZZZPZ9999Z") for line in pages[1]]
    ret = parse_coi(pages)
    assert ret.get("taxpayer.pan") == syn.PAN_A
    conflict = next(c for c in ret.conflicts if c["field"] == "taxpayer.pan")
    assert conflict["kept"] == "XXXXX1234Z" and conflict["other"] == "XXXXX9999Z"


def test_itr1_json_parsing():
    ret = parse_itr_json(syn.itr1_json(), "return.json")
    assert ret.get("return.itr_form") == "ITR-1"
    assert ret.get("return.assessment_year") == "2025-26"
    assert ret.get("return.return_type") == "original (u/s 139(1))"
    assert ret.get("income.salary") == Decimal("500000")
    assert ret.get("income.gross_total_income") == Decimal("512003")
    assert ret.get("tax.refund") == Decimal("5000")
    assert ret.fields["income.salary"].provenance.json_path == "$.ITR.ITR1.ITR1_IncomeDeductions.IncomeFromSal"
    assert ret.schema_verified is False
    assert "$.ITR.ITR1.TaxPaid.TaxesPaid.TDS" in ret.source_fields
    assert not any("PAN" in k for k in ret.source_fields)


def test_itr1_json_missing_fields_are_absent():
    ret = parse_itr_json(syn.itr1_json(**{"ITR1_IncomeDeductions.IncomeOthSrc": None}), "r.json")
    assert not ret.has("income.other_sources")


def test_itr1_json_malformed_amount():
    ret = parse_itr_json(syn.itr1_json(**{"ITR1_IncomeDeductions.IncomeFromSal": "12x"}), "r.json")
    assert not ret.has("income.salary")
    assert ret.malformed and ret.malformed[0]["field"] == "income.salary"


@pytest.mark.parametrize(
    "data,message",
    [
        ({"foo": 1}, "missing top-level 'ITR'"),
        ({"ITR": {"ITR3": {"Form_ITR3": {"AssessmentYear": "2025"}}}}, "ITR-3 JSON is not supported"),
        (syn.itr1_json(**{"Form_ITR1.AssessmentYear": "2019"}), "AY 2019-20 is not supported"),
        (syn.itr1_json(**{"Form_ITR1.AssessmentYear": None}), "no Form_ITR1.AssessmentYear"),
    ],
)
def test_unsupported_json_schemas(data, message):
    with pytest.raises(UnsupportedDocumentError, match=message):
        detect_schema(data)


def test_strict_mode_rejects_unverified_schema():
    with pytest.raises(UnsupportedDocumentError, match="unverified"):
        detect_schema(syn.itr1_json(), allow_unverified=False)


@pytest.mark.parametrize(
    "data,status",
    [
        (lambda: syn.scanned_pdf(), "ocr_required"),
        (lambda: syn.encrypted_pdf(), "failed"),
        (lambda: b"%PDF-1.4 broken", "failed"),
        (lambda: syn.unrelated_pdf(), "unsupported"),
        (lambda: syn.itr_v_text_pdf(), "unsupported"),
        (lambda: b"PK\x03\x04zipfile", "unsupported"),
        (lambda: b"{not json", "failed"),
    ],
)
def test_document_failures_reported_not_raised(data, status):
    report = analyze_bytes(data(), "doc").report
    assert report["document"]["extraction_status"] == status
    assert report["validation"]["status"] == "not_analyzable"
    assert report["income"]["salary"] is None
    assert report["risk_indicators"], "a document-level indicator must be raised"


def test_itr_v_layout_message():
    report = analyze_bytes(syn.itr_v_text_pdf(), "ack.pdf").report
    assert report["document"]["document_kind"] == "itr_acknowledgement"
    assert "acknowledgement" in report["limitations"][0].lower()


def test_ocr_required_indicator_and_no_figures():
    report = analyze_bytes(syn.scanned_pdf(syn.business_coi()), "scan.pdf").report
    assert [i["code"] for i in report["risk_indicators"]] == ["OCR_REQUIRED"]
    assert report["assessment_year"] is None
    assert report["taxpayer"]["name"] is None


class FakeOCR:
    """Stands in for a local OCR engine: returns the synthetic text for each page."""

    name = "fake"

    def __init__(self, pages):
        self.pages = pages

    def is_available(self):
        return True

    def ocr_pages(self, pdf_bytes, page_numbers):
        return {n: "\n".join(self.pages[n - 1]) for n in page_numbers}


def test_ocr_backend_used_when_available():
    from itr_analysis_engine import AnalysisOptions

    pages = syn.business_coi()
    report = analyze_bytes(syn.scanned_pdf(pages), "scan.pdf", AnalysisOptions(ocr_backend=FakeOCR(pages))).report
    assert report["document"]["extraction_status"] == "success"
    assert report["income"]["taxable_income_reported"] == "217000.00"
    assert report["provenance"]["income.taxable_income_reported"]["method"] == "ocr_text"
    assert any("OCR" in w for w in report["document"]["warnings"])


def test_size_limit():
    from itr_analysis_engine import AnalysisOptions

    report = analyze_bytes(syn.text_pdf(syn.business_coi()), "big.pdf", AnalysisOptions(max_bytes=100)).report
    assert report["document"]["extraction_status"] == "failed"
    assert "limit" in report["document"]["warnings"][0]
