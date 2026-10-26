"""Reconciliation checks, rule triggering and year-over-year analysis."""

from decimal import Decimal

import pytest

from itr_analysis_engine import AnalysisOptions, RuleConfig, analyze_batch, analyze_bytes
from tests.fixtures import synthetic as syn


def check(report, check_id):
    return next(c for c in report["validation"]["checks"] if c["id"] == check_id)


def codes(report):
    return [i["code"] for i in report["risk_indicators"]]


def edit(pages, old, new):
    return [[line.replace(old, new) for line in page] for page in pages]


@pytest.fixture
def business_report():
    return analyze_bytes(syn.text_pdf(syn.business_coi()), "biz.pdf").report


def test_consistent_document_passes(business_report):
    r = business_report
    assert r["validation"]["status"] == "passed"
    assert set(r["validation"]["summary"]) == {"pass"}
    assert codes(r) == ["EXEMPT_INCOME_DISCLOSED"]
    assert check(r, "income.taxable_income_rounding")["rule_reference"] == "Section 288A"
    assert check(r, "payments.net_position")["expected_calculated"] == "-2000.00"


def test_calculated_vs_reported_distinguished(business_report):
    r = business_report
    assert r["income"]["gross_total_income_reported"] is None
    assert r["income"]["gross_total_income_calculated"] == "217003.00"
    assert r["provenance"]["income.gross_total_income_calculated"]["status"] == "calculated"
    assert r["provenance"]["income.total_income_reported"]["status"] == "reported"
    calc = next(c for c in r["calculations"] if c["id"] == "calc.income.sum_of_heads")
    assert "income.salary" in calc["explanation"]  # missing head explicitly listed, not zeroed
    assert calc["inputs"] == {
        "income.house_property": "42000.00",
        "income.business_profession": "150000.00",
        "income.other_sources": "25003.00",
    }


def test_income_reconciliation_mismatch():
    pages = edit(syn.business_coi(), f"{'Total Income':<70}{217003:>10}", f"{'Total Income':<70}{227003:>10}")
    r = analyze_bytes(syn.text_pdf(pages), "x.pdf").report
    c = check(r, "income.gti_reconciliation")
    assert c["status"] == "fail" and c["difference"] == "10000.00"
    assert "INCOME_RECONCILIATION_MISMATCH" in codes(r)
    assert r["validation"]["status"] == "review_required"


def test_component_mismatch():
    pages = edit(syn.business_coi(), f"{'Tenant A':<56}{60000:>10}", f"{'Tenant A':<56}{61000:>10}")
    r = analyze_bytes(syn.text_pdf(pages), "x.pdf").report
    assert check(r, "income.components.house_property")["status"] == "fail"
    assert "INCOME_RECONCILIATION_MISMATCH" in codes(r)


def test_tax_payment_mismatch():
    pages = edit(syn.business_coi(), "(-)   2000", "(-)   5000")
    r = analyze_bytes(syn.text_pdf(pages), "x.pdf").report
    assert check(r, "payments.net_position")["status"] == "fail"
    ind = next(i for i in r["risk_indicators"] if i["code"] == "TAX_PAYMENT_RECONCILIATION_MISMATCH")
    assert ind["evidence"][0]["id"] == "payments.net_position"
    assert ind["suggested_action"] and ind["rule_version"]


def test_tds_detail_mismatch():
    pages = edit(syn.business_coi(), "20000       2004", "20000       1004")
    r = analyze_bytes(syn.text_pdf(pages), "x.pdf").report
    assert check(r, "payments.tds_details")["status"] == "fail"


def test_tax_chain_mismatch():
    pages = edit(syn.business_coi(), f"{'Net Tax On Total Income':<70}{3350:>10}", f"{'Net Tax On Total Income':<70}{3550:>10}")
    r = analyze_bytes(syn.text_pdf(pages), "x.pdf").report
    assert check(r, "tax.net_tax_reconciliation")["status"] == "fail"
    assert "TAX_COMPUTATION_MISMATCH" in codes(r)


def test_44ad_deemed_profit_and_declared_profit():
    pages = edit(syn.business_coi(), "5. Total Deemed Profit          on Rs.   500000    =    34000",
                 "5. Total Deemed Profit          on Rs.   500000    =    30000")
    r = analyze_bytes(syn.text_pdf(pages), "x.pdf").report
    assert check(r, "business.deemed_profit")["status"] == "fail"
    assert r["business"]["deemed_profit_calculated"] == "34000.00"
    assert "BUSINESS_TURNOVER_REVIEW" in codes(r)


def test_44ad_turnover_limit():
    pages = edit(syn.business_coi(), "Receipt in Cash                        200000", "Receipt in Cash                      30000000")
    pages = edit(pages, "on Rs.   500000    =    34000", "on Rs. 30300000    =    34000")
    r = analyze_bytes(syn.text_pdf(pages), "x.pdf").report
    c = check(r, "business.44ad_turnover_limit")
    assert c["status"] == "fail" and c["expected_calculated"] == "20000000.00"


def test_no_44ad_rules_for_unconfigured_year():
    r = analyze_bytes(syn.text_pdf(syn.business_coi(ay="2021-2022", year_end="31/03/2021")), "x.pdf").report
    assert check(r, "business.presumptive_rules")["status"] == "not_applicable"
    assert any("No statutory rule set" in item for item in r["limitations"])


def test_previous_year_mismatch():
    r = analyze_bytes(syn.text_pdf(syn.business_coi(year_end="31/03/2024")), "x.pdf").report
    assert check(r, "integrity.previous_year_end")["status"] == "fail"


def test_missing_required_field_not_zero():
    pages = [[l for l in p if "Net Assessable Income" not in l] for p in syn.business_coi()]
    r = analyze_bytes(syn.text_pdf(pages), "x.pdf").report
    assert r["income"]["taxable_income_reported"] is None
    assert check(r, "required.income.taxable_income")["status"] == "fail"
    ind = next(i for i in r["risk_indicators"] if i["code"] == "MISSING_REQUIRED_FIELD")
    assert {"field": "income.taxable_income"} in ind["evidence"]
    assert "not as zero" in ind["explanation"]


def test_low_extraction_confidence():
    pages = [[l for l in p if not any(k in l for k in ("Net Assessable", "Total Income  ", "Name  ", "PAN", "Balance Tax", "Tax on total"))]
             for p in syn.business_coi()]
    r = analyze_bytes(syn.text_pdf(pages), "x.pdf").report
    assert r["document"]["extraction_status"] == "partial"
    assert "LOW_EXTRACTION_CONFIDENCE" in codes(r)


def test_assessment_year_override_mismatch():
    r = analyze_bytes(syn.text_pdf(syn.business_coi()), "x.pdf", AnalysisOptions(assessment_year="2024-25")).report
    assert check(r, "integrity.assessment_year_matches_request")["status"] == "fail"


def test_json_schema_unverified_indicator():
    r = analyze_bytes(syn.itr1_json_bytes(), "r.json").report
    assert "SCHEMA_UNVERIFIED" in codes(r)
    assert r["validation"]["status"] == "review_required"
    assert check(r, "payments.net_position")["status"] == "pass"
    assert check(r, "payments.total_paid_reconciliation")["status"] == "pass"


def test_json_mismatch():
    r = analyze_bytes(syn.itr1_json_bytes(**{"ITR1_IncomeDeductions.GrossTotIncome": 600000}), "r.json").report
    assert check(r, "income.gti_reconciliation")["status"] == "fail"


def test_rule_config_overrides():
    cfg = RuleConfig({"EXEMPT_INCOME_DISCLOSED": {"enabled": False}, "SCHEMA_UNVERIFIED": {"severity": "low"}})
    r = analyze_bytes(syn.text_pdf(syn.business_coi()), "x.pdf", AnalysisOptions(rules=cfg)).report
    assert codes(r) == []
    r = analyze_bytes(syn.itr1_json_bytes(), "r.json", AnalysisOptions(rules=cfg)).report
    assert r["risk_indicators"][0]["severity"] == "low"
    assert r["validation"]["status"] == "passed"
    with pytest.raises(ValueError):
        RuleConfig({"NOT_A_RULE": {}})
    with pytest.raises(ValueError):
        RuleConfig({"OCR_REQUIRED": {"severity": "catastrophic"}})


def test_no_automated_decision_fields(business_report):
    text = str(business_report).lower()
    for forbidden in ("credit_score", "approve", "reject", "fraud\"", "creditworthy\""):
        assert forbidden not in text


# Year over year -----------------------------------------------------------------
def multi_year():
    docs = [
        (syn.text_pdf(syn.salary_coi(ay="2024-2025", year_end="31/03/2024", salary_a=200000, salary_b=100000)), "ay24.pdf"),
        (syn.text_pdf(syn.salary_coi(ay="2025-2026", year_end="31/03/2025")), "ay25.pdf"),
        (syn.text_pdf(syn.business_coi()), "other-taxpayer.pdf"),
    ]
    return analyze_batch(docs)


def test_year_over_year_comparison():
    batch = multi_year()
    assert len(batch.year_over_year) == 1
    group = batch.year_over_year[0]
    assert group["match_basis"] == "pan"
    assert group["assessment_years"] == ["2024-25", "2025-26"]
    salary = next(c for c in group["comparisons"] if c["metric"] == "income.salary")
    assert salary["from_value"] == "300000.00" and salary["to_value"] == "550000.00"
    assert salary["change"] == "250000.00" and salary["change_pct"] == "83.33"
    business = next(c for c in group["comparisons"] if c["metric"] == "income.business_profession") if any(
        c["metric"] == "income.business_profession" for c in group["comparisons"]) else None
    assert business is None  # absent in both years -> not compared
    r24, r25, other = (r.report for r in batch.results)
    assert "YEAR_OVER_YEAR_INCOME_CHANGE" in codes(r25)
    assert r25["validation"]["status"] == "review_required"
    assert other["year_over_year"] == {}
    assert "YEAR_OVER_YEAR_INCOME_CHANGE" not in codes(other)


def test_yoy_threshold_configurable():
    cfg = RuleConfig({"YEAR_OVER_YEAR_INCOME_CHANGE": {"threshold_pct": "90"}})
    docs = [
        (syn.text_pdf(syn.salary_coi(ay="2024-2025", year_end="31/03/2024", salary_a=200000, salary_b=100000)), "a.pdf"),
        (syn.text_pdf(syn.salary_coi()), "b.pdf"),
    ]
    batch = analyze_batch(docs, AnalysisOptions(rules=cfg))
    assert all("YEAR_OVER_YEAR_INCOME_CHANGE" not in codes(r.report) for r in batch.results)


def test_yoy_duplicates_and_gaps():
    docs = [
        (syn.text_pdf(syn.salary_coi(ay="2023-2024", year_end="31/03/2023")), "a.pdf"),
        (syn.text_pdf(syn.salary_coi(ay="2025-2026")), "b.pdf"),
        (syn.text_pdf(syn.salary_coi(ay="2025-2026")), "c.pdf"),
    ]
    batch = analyze_batch(docs)
    group = batch.year_over_year[0]
    assert group["duplicate_assessment_years"] == ["2025-26"]
    assert group["assessment_years"] == ["2023-24"]
    assert "DUPLICATE_ASSESSMENT_YEAR" in codes(batch.results[0].report)


def test_yoy_missing_value_not_comparable():
    from itr_analysis_engine.year_over_year import compare_years

    def rep(rid, ay, salary):
        return {"report_id": rid, "assessment_year": ay, "document": {"extraction_status": "success"},
                "income": {"salary": salary}, "tax_computation": {}, "business": {}}

    groups = compare_years([("P", None, rep("a", "2024-25", None)), ("P", None, rep("b", "2025-26", "100.00"))])
    salary = next(c for c in groups[0]["comparisons"] if c["metric"] == "income.salary")
    assert salary["status"] == "not_comparable" and salary["change"] is None


def test_pan_not_in_yoy_output():
    batch = multi_year()
    assert syn.PAN_B not in str(batch.summary())
