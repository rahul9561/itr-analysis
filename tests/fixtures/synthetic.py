"""Synthetic test documents.

All names, PANs, TANs, account numbers and amounts here are fictitious. The
text layout imitates the fixed-width "Computation of Income" format so the
parser can be tested without real taxpayer data.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pymupdf

PAN_A = "AAAPZ1234Z"
PAN_B = "BBBPY5678Y"
W = 70  # description column width


def row(desc: str, amount: Any = "", width: int = W) -> str:
    return f"{desc:<{width}}{amount!s:>10}"


def header(name: str, pan: str, ay: str = "2025-2026", year_end: str = "31/03/2025", page: str = "Page 1 of 2") -> list[str]:
    plain = name.split(".", 1)[-1]
    return [
        f" {page}  ({plain} - Asst. Yr.: {ay}) ",
        "SYNTHETIC TAX CONSULTANTS               Phone(s) : 0000000000",
        "_" * 85,
        f"Name              : {name}",
        "Father's Name     : SYNTHETIC PARENT",
        "Address           : 1 Test Street",
        "                  : TESTCITY - 000000",
        f"Status            : INDIVIDUAL                 Asstt. Year: {ay}",
        f"PAN/GIR           : {pan}                 Year Ending: {year_end}",
        "Range/Ward        : Ward-ITO                 Date of Birth: 01/01/1980",
        "Due Date of Filing: 15/09/2025          Residential Status: RESIDENT",
        "Date of Filing  01/09/2025 & Receipt No 900000000010925",
        "_" * 85,
        "",
        "Computation of Income (New Regime)",
        "",
    ]


def business_coi(name: str = "SMT.TEST PERSON ALPHA", pan: str = PAN_A, ay: str = "2025-2026",
                 year_end: str = "31/03/2025") -> list[list[str]]:
    """House property + 44AD business + other sources + agricultural income; refund outcome."""
    page1 = header(name, pan, ay, year_end) + [
        row("Income from House Property", 42000),
        "===========================",
        "Address:Shop at Test Market",
        "---------------------------------------",
        "Annual Rent Received From:-",
        row("Tenant A", 60000, 56),
        row("Less: Statutory Deduction of 30%", "(-)  18000", 56),
        " " * 53 + "--------",
        row("Income from House Property", 42000, 56),
        " " * 53 + "--------",
        "",
        row("Income from Business/Profession", 150000),
        "===============================",
        "TEST PERSON ALPHA",
        "Business Income u/s 44AD",
        "1. Gross Turnover       Actual Profit",
        "   A/C Payee Chq before specified Date    300000   =    90000",
        "   Receipt in Cash                        200000   =    60000",
        "   Any other mode                              0   =        0",
        "2. Adjusted Actual Profit                 150000",
        "3. Actual Profit %age is 30.00 %",
        "4. Deemed Profit %age is  6.00% on Rs.   300000    =    18000",
        "                          8.00% on Rs.   200000    =    16000",
        "5. Total Deemed Profit          on Rs.   500000    =    34000",
        "",
        row("Higher of 2 & 5 taken as assessable profit", 150000, 56),
        " " * 53 + "--------",
        row("Business Income", 150000, 56),
        " " * 53 + "--------",
    ]
    page2 = [
        f" Page 2 of 2  ({name.split('.', 1)[-1]} - Asst. Yr.: {ay}) ",
        "",
        row("Income from other Sources", 25003),
        "=========================",
        "Bank Interest",
        "-------------",
        row("Intt fr Test Bank SB A/c No. 11112222333", 5003, 58),
        " " * 51 + "----------",
        " " * 58 + "5003",
        "",
        "FDR Interest",
        "------------",
        row("Test Bank FDR A/c No. 44445555666 per AIS", 12000, 58),
        row("Test Bank FDR A/c No. 77778888999 per AIS", 8000, 58),
        " " * 51 + "----------",
        " " * 57 + "20000",
        "",
        row("Total Income from other sources", 25003, 58),
        " " * 51 + "==========",
        " " * 68 + "--------",
        row("Total Income", 217003),
        " " * 68 + "--------",
        "Net Assessable Income of the Assessee is thus Rs. 217000",
        row("Net Agricultural Income for rate purposes", 100000),
        "",
        "Computation of Tax",
        "",
        row("Tax on total income of Rs 317000 including agriculture income", 5850),
        row("Less: Tax Rebate on Agriculture Income", "(-)   2500"),
        " " * 68 + "--------",
        row("Net Tax On Total Income", 3350),
        row("Less: Tax relief u/s 87A", "(-)   3350"),
        " " * 68 + "--------",
        row("Balance Tax", 0),
        row("Less: TDS", "(-)   2004"),
        "TAN        Name                                 Income        TDS    Sec.   Head",
        "TEST12345T SYNTHETIC BANK LTD                    20000       2004    194A      OS",
        " " * 68 + "--------",
        row("Refund Due", "(-)   2000"),
        " " * 68 + "--------",
        f"I, {name.split('.', 1)[-1]}  son/daughter of  SYNTHETIC PARENT holding PAN  {pan}",
        "solemnly declare that the information given is correct.",
    ]
    return [page1, page2]


def salary_coi(name: str = "SH.TEST PERSON BETA", pan: str = PAN_B, ay: str = "2025-2026",
               year_end: str = "31/03/2025", salary_a: int = 300000, salary_b: int = 250000) -> list[list[str]]:
    """Salary from two employers + other sources; nil payable outcome."""
    salary = salary_a + salary_b
    other = 1545
    total = salary + other
    from decimal import Decimal

    from itr_analysis_engine.calculations import round_to_nearest_ten

    taxable = int(round_to_nearest_ten(Decimal(total)))
    page1 = header(name, pan, ay, year_end) + [
        row("Income from Salary", salary),
        "==================",
        "Gross Salary Including all allowances drawn from",
        row(" Test Employer One", salary_a, 56),
        " " * 53 + "--------",
        row("Taxable Salary", salary_a, 56),
        " " * 53 + "--------",
        "Gross Salary Including all allowances drawn from",
        row(" Test Employer Two", salary_b, 56),
        " " * 53 + "--------",
        row("Taxable Salary", salary_b, 56),
        " " * 53 + "--------",
        "",
        row("Income from other Sources", other),
        "=========================",
        "Bank Interest",
        "-------------",
        row("Test Bank SB A/c 12341234 as per AIS", 1200, 58),
        row("Post Office SB A/c 98769876 as per AIS", 345, 58),
        " " * 51 + "----------",
        " " * 58 + "1545",
        row("Total Income from other sources", other, 58),
        " " * 68 + "--------",
        row("Total Income", total),
        " " * 68 + "--------",
        f"Net Assessable Income of the Assessee is thus Rs. {taxable}",
    ]
    page2 = [
        f" Page 2 of 2  ({name.split('.', 1)[-1]} - Asst. Yr.: {ay}) ",
        "Computation of Tax",
        "",
        row(f"Tax on total income of Rs {taxable} at normal rates", 12578),
        row("Less: Tax relief u/s 87A", "(-)  12578"),
        " " * 68 + "--------",
        row("Balance Tax", 0),
        row("Less: TDS", 0),
        "TAN        Name                             Income      TDS",
        f"TEST54321T SYNTHETIC EMPLOYER ONE            {salary_a:>7}          0",
        " " * 68 + "--------",
        row("Tax Payable", 0),
        " " * 68 + "--------",
    ]
    return [page1, page2]


def text_pdf(pages: list[list[str]]) -> bytes:
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page(width=595, height=842)
        y = 30
        for line in lines:
            if line:
                page.insert_text((20, y), line, fontname="cour", fontsize=6.5)
            y += 9
    data = doc.tobytes()
    doc.close()
    return data


def scanned_pdf(pages: list[list[str]] | None = None) -> bytes:
    """Image-only PDF: each page is a raster of a synthetic text page."""
    source = pymupdf.open(stream=text_pdf(pages or [["SYNTHETIC SCANNED ACKNOWLEDGEMENT", "Total Income 100000"]]), filetype="pdf")
    out = pymupdf.open()
    for page in source:
        pix = page.get_pixmap(dpi=60)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=pix)
    data = out.tobytes()
    out.close()
    source.close()
    return data


def encrypted_pdf() -> bytes:
    doc = pymupdf.open(stream=text_pdf(business_coi()), filetype="pdf")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user-secret", owner_pw="owner-secret")
    doc.close()
    return data


def unrelated_pdf() -> bytes:
    return text_pdf([["Quarterly newsletter", "Nothing about income tax here.", "Lorem ipsum dolor sit amet " * 3]])


def itr_v_text_pdf() -> bytes:
    return text_pdf([["INDIAN INCOME TAX RETURN ACKNOWLEDGEMENT", "Assessment Year 2025-26", "Total Income 100000" + " " * 30]])


ITR1_BASE: dict[str, Any] = {
    "ITR": {
        "ITR1": {
            "CreationInfo": {"SWVersionNo": "R1", "SWCreatedBy": "SW00000000", "JSONCreatedBy": "SW00000000", "JSONCreationDate": "2025-07-15"},
            "Form_ITR1": {"FormName": "ITR-1", "Description": "Synthetic", "AssessmentYear": "2025", "SchemaVer": "Ver1.0", "FormVer": "Ver1.0"},
            "PersonalInfo": {
                "AssesseeName": {"FirstName": "TEST", "SurNameOrOrgName": "PERSON GAMMA"},
                "PAN": "CCCPX4321X",
                "Address": {"ResidenceNo": "1", "CityOrTownOrDistrict": "TESTCITY", "PinCode": 0},
                "DOB": "1985-01-01",
            },
            "FilingStatus": {"ReturnFileSec": 11, "OptOutNewTaxRegime": "N"},
            "ITR1_IncomeDeductions": {
                "GrossSalary": 575000,
                "IncomeFromSal": 500000,
                "TotalIncomeOfHP": 0,
                "IncomeOthSrc": 12003,
                "GrossTotIncome": 512003,
                "DeductUndChapVIA": {"Section80C": 0, "TotalChapVIADeductions": 0},
                "TotalIncome": 512000,
                "ExemptIncAgriOthUs10": {"ExemptIncAgriOthUs10Total": 0},
            },
            "ITR1_TaxComputation": {
                "TotalTaxPayable": 10600,
                "Rebate87A": 10600,
                "TaxPayableOnRebate": 0,
                "EducationCess": 0,
                "GrossTaxLiability": 0,
                "Section89": 0,
                "NetTaxLiability": 0,
                "TotalIntrstPay": 0,
                "TotTaxPlusIntrstPay": 0,
            },
            "TaxPaid": {
                "TaxesPaid": {"AdvanceTax": 0, "TDS": 5004, "TCS": 0, "SelfAssessmentTax": 0, "TotalTaxesPaid": 5004},
                "BalTaxPayable": 0,
            },
            "Refund": {"RefundDue": 5000},
        }
    }
}


def itr1_json(**overrides: Any) -> dict[str, Any]:
    """Synthetic ITR-1 JSON. ``overrides`` keys are dotted paths below ITR.ITR1."""
    data = copy.deepcopy(ITR1_BASE)
    for path, value in overrides.items():
        node = data["ITR"]["ITR1"]
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[part]
        if value is None:
            node.pop(parts[-1], None)
        else:
            node[parts[-1]] = value
    return data


def itr1_json_bytes(**overrides: Any) -> bytes:
    return json.dumps(itr1_json(**overrides)).encode("utf-8")
