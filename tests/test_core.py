"""Money handling, normalisation and PII protection."""

import logging
from decimal import Decimal

import pytest

from itr_analysis_engine.calculations import pct_change, round_to_nearest_ten, sum_present, to_money
from itr_analysis_engine.exceptions import MalformedValueError
from itr_analysis_engine.normalizer import (
    clean_name,
    financial_year_for,
    normalize_assessment_year,
    parse_date,
)
from itr_analysis_engine.security import PIIRedactingFilter, mask_pan, redact_text, safe_filename


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,23,456", Decimal("123456")),
        ("(-) 11484", Decimal("-11484")),
        ("(500)", Decimal("-500")),
        ("-500.50", Decimal("-500.50")),
        ("₹ 7,440", Decimal("7440")),
        ("Rs. 10", Decimal("10")),
        (42, Decimal("42")),
        ("0.1", Decimal("0.1")),
    ],
)
def test_to_money_parses(raw, expected):
    assert to_money(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "12a", None, True, "1.2.3"])
def test_to_money_rejects(raw):
    with pytest.raises(MalformedValueError):
        to_money(raw)


def test_decimal_arithmetic_is_exact():
    assert to_money("0.1") + to_money("0.2") == Decimal("0.3")


@pytest.mark.parametrize(
    "amount,expected",
    [
        ("434518", "434520"),
        ("575989", "575990"),
        ("575984", "575980"),
        ("575985", "575990"),  # remainder of exactly 5 rounds up
        ("7438", "7440"),
        ("7434.99", "7430"),  # paise ignored before rounding
        ("0", "0"),
        ("-7438", "-7440"),
    ],
)
def test_round_288a_288b(amount, expected):
    assert round_to_nearest_ten(Decimal(amount)) == Decimal(expected)


def test_sum_present_does_not_treat_missing_as_zero():
    total, present, missing = sum_present({"a": Decimal(5), "b": None})
    assert total == Decimal(5) and present == ["a"] and missing == ["b"]
    assert sum_present({"a": None})[0] is None


def test_pct_change():
    assert pct_change(Decimal(100), Decimal(150)) == Decimal("50.00")
    assert pct_change(Decimal(0), Decimal(10)) is None


@pytest.mark.parametrize("raw,expected", [("2025-2026", "2025-26"), ("2025-26", "2025-26"), ("2025", "2025-26"), ("1999-2000", "1999-00")])
def test_assessment_year_normalisation(raw, expected):
    assert normalize_assessment_year(raw) == expected


def test_assessment_year_rejects_bad_span():
    with pytest.raises(MalformedValueError):
        normalize_assessment_year("2025-2027")


def test_financial_year_and_dates():
    assert financial_year_for("2025-26") == "2024-25"
    assert parse_date("06/09/2025") == "2025-09-06"
    with pytest.raises(MalformedValueError):
        parse_date("31/31/2025")


def test_clean_name_strips_honorific():
    assert clean_name("SMT.TEST PERSON") == "TEST PERSON"
    assert clean_name("SH. TEST  PERSON") == "TEST PERSON"


def test_mask_pan():
    assert mask_pan("AAAPZ1234Z") == "XXXXX1234Z"
    assert mask_pan("bad") == "XXX"
    assert mask_pan(None) is None


def test_redact_text_masks_pan_and_aadhaar():
    text = redact_text("PAN AAAPZ1234Z aadhaar 1234 5678 9012")
    assert "AAAPZ" not in text and "5678" not in text


def test_log_filter_redacts(caplog):
    logger = logging.getLogger("itr-test-redaction")
    logger.addFilter(PIIRedactingFilter())
    with caplog.at_level(logging.INFO, logger="itr-test-redaction"):
        logger.info("processing %s", "AAAPZ1234Z")
    assert "AAAPZ1234Z" not in caplog.text
    assert "XXXXX1234Z" in caplog.text


def test_safe_filename():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("C:\\x\\my file.pdf") == "my_file.pdf"
    assert safe_filename("") == "document"
