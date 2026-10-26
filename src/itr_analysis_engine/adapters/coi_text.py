"""Adapter for text-based "Computation of Income" PDFs.

Layout reference: the fixed-width computation printed by common Indian CA
tax-filing software (header block with ``Asstt. Year`` / ``PAN/GIR``, sections
``Income from <head>`` underlined with ``=``, then ``Computation of Tax``).

Tested against redacted AY 2025-26 samples (new regime; salary/pension,
house property, 44AD presumptive business, other sources, agricultural income,
87A relief, TDS, refund and nil-payable outcomes). Labels that were not present
in any tested sample (capital gains, Chapter VI-A deductions, surcharge, cess,
interest, advance / self-assessment tax) are recognised on a best-effort basis
and flagged as such in the output limitations.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..calculations import to_money
from ..exceptions import MalformedValueError
from ..models import ExtractedReturn, LineItem, TDSEntry
from ..normalizer import clean_name, normalize_assessment_year, normalize_status, parse_date
from ..pdf_extractor import ExtractedPDF
from .base import PDFTextAdapter

AMT = r"-?[\d,]+(?:\.\d+)?"
NEG_AMT = rf"(?:\(-\)\s*)?({AMT})"

HEAD_KEYS = {
    "salary": "income.salary",
    "house property": "income.house_property",
    "business/profession": "income.business_profession",
    "business or profession": "income.business_profession",
    "business": "income.business_profession",
    "capital gains": "income.capital_gains",
    "other sources": "income.other_sources",
}

HEAD_RE = re.compile(
    rf"^\s*Income from (Salary|House Property|Business/Profession|Business or Profession|Business|Capital Gains|other Sources)\s+({AMT})\s*$",
    re.IGNORECASE,
)
SEPARATOR_RE = re.compile(r"^\s*[-=_]{3,}\s*$")
AMOUNT_ONLY_RE = re.compile(rf"^\s*({AMT})\s*$")
DETAIL_RE = re.compile(rf"^(?P<desc>.*?\S)\s{{2,}}(?P<neg>\(-\)\s*)?(?P<amt>{AMT})\s*$")

# Lines that close an income head section, by head.
SECTION_TOTAL_RES = {
    "income.house_property": re.compile(rf"^\s*Income from House Property\s+({AMT})\s*$", re.I),
    "income.business_profession": re.compile(rf"^\s*(?:Business Income|Income from Profession|Professional Income)\s+({AMT})\s*$", re.I),
    "income.other_sources": re.compile(rf"^\s*Total Income from other sources\s+({AMT})\s*$", re.I),
    "income.capital_gains": re.compile(rf"^\s*Total Capital Gains?\s+({AMT})\s*$", re.I),
}
SALARY_ITEM_RE = re.compile(rf"^\s*(Taxable Salary|Net Salary|Income chargeable under the head Salaries)\s+({AMT})\s*$", re.I)

TDS_ROW_RE = re.compile(
    rf"^\s*([A-Z]{{4}}\d{{5}}[A-Z])\s+(.+?)\s{{2,}}({AMT})\s+({AMT})(?:\s+(\S+))?(?:\s+(\S+))?\s*$"
)

# (canonical key, regex with one money group, label)
TAX_LINE_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("tax.rebate_agricultural", re.compile(rf"^\s*Less:?\s*Tax Rebate on Agricultur\w* Income\s+{NEG_AMT}\s*$", re.I), "Tax rebate on agriculture income"),
    ("tax.net_tax_on_total_income", re.compile(rf"^\s*Net Tax On Total Income\s+({AMT})\s*$", re.I), "Net Tax On Total Income"),
    ("tax.rebate_87a", re.compile(rf"^\s*Less:?\s*(?:Tax relief|Rebate)\s+u/s\s*87A\s+{NEG_AMT}\s*$", re.I), "Tax relief u/s 87A"),
    ("tax.relief_89", re.compile(rf"^\s*Less:?\s*Relief\s+u/s\s*89\S*\s+{NEG_AMT}\s*$", re.I), "Relief u/s 89"),
    ("tax.surcharge", re.compile(rf"^\s*Add:?\s*Surcharge.*?\s{{2,}}({AMT})\s*$", re.I), "Surcharge"),
    ("tax.cess", re.compile(rf"^\s*Add:?\s*(?:Health\s*(?:&|and)\s*)?Education Cess.*?\s{{2,}}({AMT})\s*$", re.I), "Education cess"),
    ("tax.tds", re.compile(rf"^\s*Less:?\s*TDS\s+{NEG_AMT}\s*$", re.I), "TDS"),
    ("tax.tcs", re.compile(rf"^\s*Less:?\s*TCS\s+{NEG_AMT}\s*$", re.I), "TCS"),
    ("tax.advance_tax", re.compile(rf"^\s*Less:?\s*Advance Tax\s+{NEG_AMT}\s*$", re.I), "Advance tax"),
    ("tax.self_assessment_tax", re.compile(rf"^\s*Less:?\s*Self[- ]Assessment Tax\s+{NEG_AMT}\s*$", re.I), "Self-assessment tax"),
    ("tax.refund", re.compile(rf"^\s*Refund Due\s+{NEG_AMT}\s*$", re.I), "Refund Due"),
    ("tax.demand", re.compile(rf"^\s*(?:Balance\s+)?Tax Payable\s+({AMT})\s*$", re.I), "Tax Payable"),
]
LIABILITY_RES = [
    (re.compile(rf"^\s*Total Tax Liability\s+({AMT})\s*$", re.I), "Total Tax Liability"),
    (re.compile(rf"^\s*Balance Tax\s+({AMT})\s*$", re.I), "Balance Tax"),
]
INTEREST_RE = re.compile(rf"^\s*Add:?\s*Interest\s+u/s\s*(234[ABC])\s+({AMT})\s*$", re.I)
TAX_ON_TOTAL_RE = re.compile(rf"^\s*Tax on total income of Rs\.?\s*({AMT})\b.*?\s{{2,}}({AMT})\s*$", re.I)

UNTESTED_KEYS = {
    "income.capital_gains", "income.deductions", "income.gross_total_income", "tax.surcharge", "tax.cess",
    "tax.relief_89", "tax.interest", "tax.tcs", "tax.advance_tax", "tax.self_assessment_tax",
}


class ComputationOfIncomeAdapter(PDFTextAdapter):
    name = "computation_of_income_text"
    version = "1.0"
    document_kind = "computation_of_income"
    tested = True
    key_fields = (
        "return.assessment_year",
        "taxpayer.name",
        "taxpayer.pan",
        "income.total_income",
        "income.taxable_income",
        "tax.tax_on_total_income",
        "tax.total_tax_liability",
    )

    def detect(self, pdf: ExtractedPDF) -> float:
        text = pdf.text
        score = 0.0
        if re.search(r"Computation of Income", text, re.I):
            score += 0.5
        if re.search(r"Computation of Tax", text, re.I):
            score += 0.2
        if re.search(r"Asstt?\.\s*(?:Year|Yr\.)", text, re.I):
            score += 0.2
        if re.search(r"Net Assessable Income", text, re.I):
            score += 0.1
        return min(score, 1.0)

    def parse(self, pdf: ExtractedPDF, source_document: str) -> ExtractedReturn:
        ret = ExtractedReturn(
            source_document=source_document,
            source_type="pdf",
            document_kind=self.document_kind,
            adapter=self.name,
            adapter_version=self.version,
        )
        lines: list[tuple[int, str, str]] = []  # (page, method, line)
        for page in pdf.pages:
            for line in page.text.splitlines():
                lines.append((page.number, page.method, line.rstrip()))

        _Parser(ret, lines).run()

        found_untested = sorted(k for k in UNTESTED_KEYS if k in ret.fields)
        if found_untested:
            ret.limitations.append(
                "These fields were read using labels not yet verified against a real sample of this layout; "
                "verify manually: " + ", ".join(found_untested)
            )
        if not ret.has("return.itr_form"):
            ret.limitations.append(
                "A computation of income does not state the ITR form; the form type is reported as unavailable."
            )
        return ret


class _Parser:
    def __init__(self, ret: ExtractedReturn, lines: list[tuple[int, str, str]]):
        self.ret = ret
        self.lines = lines
        self.current_head: str | None = None
        self.in_tax_section = False

    # -- helpers -----------------------------------------------------------
    def money(self, raw: str, key: str, page: int, negative: bool = False) -> Decimal | None:
        try:
            value = to_money(raw)
        except MalformedValueError:
            self.ret.malformed.append({"field": key, "page": page, "reason": "unparseable amount"})
            return None
        return -value if negative else value

    def set_money(self, key: str, raw: str, page: int, method: str, label: str) -> None:
        value = self.money(raw, key, page)
        if value is not None:
            self.ret.set(key, value, method=method, page=page, label=label)

    def set_text(self, key: str, value: str | None, page: int, method: str, label: str) -> None:
        if value:
            self.ret.set(key, value, method=method, page=page, label=label)

    def next_nonblank(self, index: int) -> str:
        for _, _, line in self.lines[index + 1 : index + 4]:
            if line.strip():
                return line
        return ""

    # -- main loop ---------------------------------------------------------
    def run(self) -> None:
        for index, (page, method, line) in enumerate(self.lines):
            if not line.strip():
                continue
            self.header_fields(line, page, method)
            if re.search(r"^\s*Computation of Tax\s*$", line, re.I):
                self.in_tax_section = True
                self.current_head = None
                continue

            head_match = HEAD_RE.match(line)
            if head_match and self.next_nonblank(index).strip().startswith("="):
                key = HEAD_KEYS[head_match.group(1).lower()]
                self.set_money(key, head_match.group(2), page, method, f"Income from {head_match.group(1)}")
                self.current_head = key
                continue

            if self.totals_and_tax(line, page, method):
                continue
            if self.current_head:
                self.section_line(line, page, method)

    def header_fields(self, line: str, page: int, method: str) -> None:
        ret = self.ret
        if m := re.match(r"^\s*Name\s*:\s*(.+?)\s*$", line):
            if not ret.has("taxpayer.name"):
                self.set_text("taxpayer.name", clean_name(m.group(1)), page, method, "Name")
        if m := re.search(r"Asstt?\.\s*(?:Year|Yr\.)\s*:\s*(\d{4}\s*-\s*\d{2,4})", line, re.I):
            try:
                ay = normalize_assessment_year(m.group(1))
                self.set_text("return.assessment_year", ay, page, method, "Asstt. Year")
            except MalformedValueError:
                ret.malformed.append({"field": "return.assessment_year", "page": page, "reason": "unrecognised year"})
        if m := re.match(r"^\s*Status\s*:\s*(.+?)(?:\s{2,}|$)", line):
            self.set_text("taxpayer.status", normalize_status(m.group(1)), page, method, "Status")
        if m := re.search(r"Residential Status\s*:\s*([A-Za-z ]+?)\s*$", line):
            self.set_text("taxpayer.residential_status", normalize_status(m.group(1)), page, method, "Residential Status")
        if m := re.match(r"^\s*PAN(?:/GIR)?\s*:\s*(\S+)", line):
            pan = m.group(1).upper()
            if re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", pan):
                self.set_text("taxpayer.pan", pan, page, method, "PAN/GIR")
            else:
                ret.malformed.append({"field": "taxpayer.pan", "page": page, "reason": "PAN not in valid format"})
        if m := re.search(r"holding PAN\s+([A-Z]{5}\d{4}[A-Z])", line):
            self.set_text("taxpayer.pan", m.group(1), page, method, "Declaration PAN")
        if m := re.search(r"Year Ending\s*:\s*(\S+)", line):
            self.date("return.year_ending", m.group(1), page, method, "Year Ending")
        if m := re.search(r"Due Date of Filing\s*:\s*(\S+)", line):
            self.date("return.due_date", m.group(1), page, method, "Due Date of Filing")
        if m := re.search(r"Date of Filing\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})\s*&\s*Receipt No\.?\s*:?\s*(\d+)", line, re.I):
            self.date("return.filing_date", m.group(1), page, method, "Date of Filing")
            self.set_text("return.acknowledgement_number", m.group(2), page, method, "Receipt No")
        if m := re.search(r"Computation of Income\s*\((New|Old) Regime\)", line, re.I):
            self.set_text("return.tax_regime", m.group(1).lower(), page, method, "Computation of Income (… Regime)")
        if m := re.search(r"\b(Revised|Belated|Updated)\s+Return\b", line, re.I):
            self.set_text("return.return_type", m.group(1).lower(), page, method, m.group(0))

    def date(self, key: str, raw: str, page: int, method: str, label: str) -> None:
        try:
            self.set_text(key, parse_date(raw), page, method, label)
        except MalformedValueError:
            self.ret.malformed.append({"field": key, "page": page, "reason": "unrecognised date"})

    def totals_and_tax(self, line: str, page: int, method: str) -> bool:
        """Handle lines that end any income-head section. Returns True if consumed."""
        ret = self.ret
        if m := re.match(rf"^\s*Gross Total Income\s+({AMT})\s*$", line, re.I):
            self.current_head = None
            self.set_money("income.gross_total_income", m.group(1), page, method, "Gross Total Income")
            return True
        if m := re.match(rf"^\s*Less:?\s*Deductions?\b.*?\s{{2,}}{NEG_AMT}\s*$", line, re.I):
            self.current_head = None
            self.set_money("income.deductions", m.group(1), page, method, "Less: Deductions")
            return True
        if m := re.match(rf"^\s*Total Income\s+({AMT})\s*$", line, re.I):
            self.current_head = None
            self.set_money("income.total_income", m.group(1), page, method, "Total Income")
            return True
        if m := re.search(rf"Net Assessable Income of the Assessee is thus Rs\.?\s*({AMT})", line, re.I):
            self.current_head = None
            self.set_money("income.taxable_income", m.group(1), page, method, "Net Assessable Income")
            return True
        if m := re.match(rf"^\s*Net Agricultural Income for rate purposes\s+({AMT})\s*$", line, re.I):
            self.set_money("income.agricultural_income", m.group(1), page, method, "Net Agricultural Income for rate purposes")
            return True
        if not self.in_tax_section:
            return False

        if m := TAX_ON_TOTAL_RE.match(line):
            self.set_money("tax.rate_base_income", m.group(1), page, method, "Tax on total income of Rs …")
            self.set_money("tax.tax_on_total_income", m.group(2), page, method, "Tax on total income")
            return True
        for regex, label in LIABILITY_RES:
            if m := regex.match(line):
                self.set_money("tax.total_tax_liability", m.group(1), page, method, label)
                return True
        if m := INTEREST_RE.match(line):
            value = self.money(m.group(2), "tax.interest", page)
            if value is not None:
                ret.line_items.setdefault("tax.interest", []).append(LineItem(f"Interest u/s {m.group(1)}", value, page))
                current = ret.get("tax.interest")
                ret.fields.pop("tax.interest", None)
                ret.set("tax.interest", (current or Decimal(0)) + value, method=method, page=page, label="Add: Interest u/s 234A/B/C")
            return True
        for key, regex, label in TAX_LINE_PATTERNS:
            if m := regex.match(line):
                self.set_money(key, m.group(1), page, method, label)
                return True
        if m := TDS_ROW_RE.match(line):
            ret.tds_entries.append(
                TDSEntry(
                    deductor_tan=m.group(1),
                    deductor_name=m.group(2).strip(),
                    income=self.money(m.group(3), "tds_entries.income", page),
                    tds=self.money(m.group(4), "tds_entries.tds", page),
                    section=m.group(5),
                    head=m.group(6),
                    page=page,
                )
            )
            return True
        return False

    def section_line(self, line: str, page: int, method: str) -> None:
        ret = self.ret
        head = self.current_head
        assert head is not None
        if SEPARATOR_RE.match(line) or AMOUNT_ONLY_RE.match(line):
            return

        total_re = SECTION_TOTAL_RES.get(head)
        if total_re and (m := total_re.match(line)):
            value = self.money(m.group(1), head, page)
            if value is not None:
                ret.head_totals.setdefault(head, []).append(value)
            return

        if head == "income.salary":
            if m := SALARY_ITEM_RE.match(line):
                value = self.money(m.group(2), head, page)
                if value is not None:
                    ret.line_items.setdefault(head, []).append(LineItem(m.group(1), value, page))
            return

        if head == "income.business_profession":
            self.business_line(line, page, method)
            return

        if m := DETAIL_RE.match(line):
            value = self.money(m.group("amt"), head, page, negative=bool(m.group("neg")))
            if value is not None:
                ret.line_items.setdefault(head, []).append(LineItem(_describe(m.group("desc")), value, page))

    def business_line(self, line: str, page: int, method: str) -> None:
        if m := re.search(r"Business Income u/s\s*(44ADA|44AD|44AE)", line, re.I):
            self.set_text("business.presumptive_section", m.group(1).upper(), page, method, "Business Income u/s")
            return
        for key, pattern, label in (
            ("business.turnover_digital", r"A/C Payee Chq", "A/C Payee Chq before specified Date"),
            ("business.turnover_cash", r"Receipt in Cash", "Receipt in Cash"),
            ("business.turnover_other", r"Any other mode", "Any other mode"),
        ):
            if re.search(pattern, line, re.I):
                if m := re.search(rf"\s({AMT})\s+=\s+({AMT})\s*$", line):
                    self.set_money(key, m.group(1), page, method, label)
                return
        if m := re.search(rf"Adjusted Actual Profit\s+({AMT})\s*$", line, re.I):
            self.set_money("business.actual_profit", m.group(1), page, method, "Adjusted Actual Profit")
        elif m := re.search(rf"Total Deemed Profit\s+on Rs\.?\s*({AMT})\s*=\s*({AMT})\s*$", line, re.I):
            self.set_money("business.gross_turnover", m.group(1), page, method, "Total Deemed Profit on Rs.")
            self.set_money("business.deemed_profit", m.group(2), page, method, "Total Deemed Profit")
        elif m := re.search(rf"taken as assessable profit\s+({AMT})\s*$", line, re.I):
            self.set_money("business.declared_profit", m.group(1), page, method, "Higher of … taken as assessable profit")
        elif m := re.search(r"Tax Audit\s*(?:u/s\s*44AB)?\s*:?\s*(Yes|No|Applicable|Not Applicable)", line, re.I):
            self.set_text("business.tax_audit_applicable", m.group(1).lower(), page, method, "Tax Audit")


def _describe(desc: str) -> str:
    """Item descriptions are kept short; long digit runs (account numbers) are masked."""
    desc = re.sub(r"\s+", " ", desc).strip()
    desc = re.sub(r"\d{5,}", lambda m: "x" * (len(m.group(0)) - 4) + m.group(0)[-4:], desc)
    return desc[:80]
