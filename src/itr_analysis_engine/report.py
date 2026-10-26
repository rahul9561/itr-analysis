"""Printable PDF report rendered from the analysis JSON with ReportLab.

The report is rendered only from the analysis dictionary, so a stored analysis
can be re-rendered later without retaining the source document. Absent values
are printed as "Not available" - never as Rs 0.
"""

from __future__ import annotations

import os
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

PACKAGE_ASSETS = Path(__file__).resolve().parent / "assets"

# Brand palette derived from the AV Management logo.
INK = colors.HexColor("#1E2230")
ACCENT = colors.HexColor("#F0A07C")
ACCENT_DARK = colors.HexColor("#C9714A")
RED = colors.HexColor("#ED3237")
MUTED = colors.HexColor("#6B7280")
RULE = colors.HexColor("#E5E7EB")
TINT = colors.HexColor("#FDF3EE")
OK = colors.HexColor("#1F7A4D")
WARN = colors.HexColor("#B45309")

STATUS_COLORS = {
    "pass": OK, "passed": OK, "success": OK,
    "fail": RED, "review_required": WARN, "warning": WARN,
    "insufficient_data": MUTED, "not_applicable": MUTED, "partial": WARN,
    "ocr_required": RED, "unsupported": RED, "failed": RED, "not_analyzable": RED,
}
SEVERITY_COLORS = {"high": RED, "medium": WARN, "low": ACCENT_DARK, "info": MUTED}

NA = "Not available"


def _assets_dir(explicit: str | Path | None) -> Path:
    for candidate in (explicit, os.environ.get("ITR_ENGINE_ASSETS_DIR"), PACKAGE_ASSETS):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return PACKAGE_ASSETS


def _register_fonts(assets: Path) -> dict[str, str]:
    fonts = assets / "fonts"
    wanted = {
        "body": ("ITR-Poppins", "Poppins-Regular.ttf"),
        "medium": ("ITR-Poppins-Medium", "Poppins-Medium.ttf"),
        "semibold": ("ITR-Poppins-SemiBold", "Poppins-SemiBold.ttf"),
        "heading": ("ITR-Montserrat-Bold", "Montserrat-Bold.ttf"),
    }
    fallback = {"body": "Helvetica", "medium": "Helvetica", "semibold": "Helvetica-Bold", "heading": "Helvetica-Bold"}
    out = {}
    for role, (name, filename) in wanted.items():
        path = fonts / filename
        if name in pdfmetrics.getRegisteredFontNames():
            out[role] = name
        elif path.is_file():
            pdfmetrics.registerFont(TTFont(name, str(path)))
            out[role] = name
        else:
            out[role] = fallback[role]
    out["rupee"] = "₹" if out["body"].startswith("ITR-") else "Rs "
    return out


def _inr(value: str | None, rupee: str, signed: bool = False) -> str:
    """Indian digit grouping: 1234567 -> 12,34,567."""
    if value is None:
        return NA
    amount = Decimal(value)
    negative = amount < 0
    amount = abs(amount)
    whole = int(amount)
    paise = amount - whole
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    text = f"{rupee}{digits}" + (f".{int(paise * 100):02d}" if paise else "")
    if negative:
        return "−" + text
    return ("+" + text) if signed and amount else text


_ACRONYMS = {"Ocr": "OCR", "Pdf": "PDF", "Json": "JSON", "Itr": "ITR"}


def _title(value: str | None) -> str:
    words = (value or "").replace("_", " ").capitalize().split(" ")
    return " ".join(_ACRONYMS.get(w, w) for w in words)


class _Styles:
    def __init__(self, f: dict[str, str]):
        self.f = f
        base = dict(fontName=f["body"], fontSize=8.6, leading=12, textColor=INK)
        self.body = ParagraphStyle("body", **base)
        self.small = ParagraphStyle("small", **{**base, "fontSize": 7.4, "leading": 10, "textColor": MUTED})
        self.cell = ParagraphStyle("cell", **{**base, "fontSize": 8, "leading": 10.5})
        self.cell_r = ParagraphStyle("cell_r", parent=self.cell, alignment=TA_RIGHT)
        self.cell_head = ParagraphStyle("cell_head", **{**base, "fontName": f["semibold"], "fontSize": 7.6, "leading": 10, "textColor": colors.white})
        self.cell_head_r = ParagraphStyle("cell_head_r", parent=self.cell_head, alignment=TA_RIGHT)
        self.h1 = ParagraphStyle("h1", fontName=f["heading"], fontSize=17, leading=21, textColor=INK, spaceAfter=2)
        self.h2 = ParagraphStyle("h2", fontName=f["heading"], fontSize=11.5, leading=15, textColor=INK, spaceBefore=12, spaceAfter=6)
        self.label = ParagraphStyle("label", **{**base, "fontName": f["medium"], "fontSize": 7, "leading": 9, "textColor": MUTED})
        self.metric = ParagraphStyle("metric", fontName=f["semibold"], fontSize=12.5, leading=16, textColor=INK)
        self.badge = ParagraphStyle("badge", fontName=f["semibold"], fontSize=8, leading=10, textColor=colors.white, alignment=TA_LEFT)


class _Doc(BaseDocTemplate):
    def __init__(self, target: Any, report: dict[str, Any], fonts: dict[str, str], logo: Path | None):
        super().__init__(
            target, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=30 * mm, bottomMargin=18 * mm,
            title=f"ITR Analysis Report {report.get('report_id', '')}", author="ITR Analysis Engine",
            subject="Automated ITR document analysis",
        )
        self.report, self.fonts, self.logo = report, fonts, logo
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="main")
        self.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=self._decorate)])

    def _decorate(self, canvas: Any, doc: Any) -> None:
        width, height = A4
        canvas.saveState()
        band = 21 * mm
        canvas.setFillColor(INK)
        canvas.rect(0, height - band, width, band, stroke=0, fill=1)
        canvas.setFillColor(ACCENT)
        canvas.rect(0, height - band - 1.2 * mm, width, 1.2 * mm, stroke=0, fill=1)
        if self.logo:
            logo_w = 52 * mm
            logo_h = logo_w * 573 / 2425
            canvas.drawImage(str(self.logo), 14 * mm, height - band + (band - logo_h) / 2, logo_w, logo_h, mask="auto")
        canvas.setFillColor(colors.white)
        canvas.setFont(self.fonts["semibold"], 10.5)
        canvas.drawRightString(width - 16 * mm, height - 10 * mm, "ITR Analysis Report")
        canvas.setFont(self.fonts["body"], 7)
        canvas.setFillColor(ACCENT)
        canvas.drawRightString(width - 16 * mm, height - 14.5 * mm, f"Report ID {self.report.get('report_id', '')}")
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.6)
        canvas.line(16 * mm, 12 * mm, width - 16 * mm, 12 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont(self.fonts["body"], 6.8)
        canvas.drawString(16 * mm, 8 * mm, "Automated analysis - not tax advice or a lending decision. Confidential.")
        canvas.drawRightString(width - 16 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()


def render_pdf(report: dict[str, Any], target: str | Path | BinaryIO, assets_dir: str | Path | None = None) -> None:
    assets = _assets_dir(assets_dir)
    fonts = _register_fonts(assets)
    logo = assets / "av-logo.png"
    target_arg = str(target) if isinstance(target, Path) else target
    doc = _Doc(target_arg, report, fonts, logo if logo.is_file() else None)
    doc.build(_Builder(report, _Styles(fonts), fonts["rupee"], doc.width).story())


def render_pdf_bytes(report: dict[str, Any], assets_dir: str | Path | None = None) -> bytes:
    buffer = BytesIO()
    render_pdf(report, buffer, assets_dir)
    return buffer.getvalue()


class _Builder:
    def __init__(self, report: dict[str, Any], s: _Styles, rupee: str, width: float):
        self.r, self.s, self.rupee, self.width = report, s, rupee, width
        self.out: list[Any] = []

    def p(self, text: str, style: ParagraphStyle | None = None) -> Paragraph:
        return Paragraph(text, style or self.s.body)

    def esc(self, value: Any) -> str:
        return escape(str(value)) if value not in (None, "") else f'<font color="#9CA3AF">{NA}</font>'

    def money(self, value: str | None, signed: bool = False) -> str:
        if value is None:
            return f'<font color="#9CA3AF">{NA}</font>'
        return escape(_inr(value, self.rupee, signed))

    def badge(self, status: str | None) -> Table:
        status = status or "unknown"
        t = Table([[self.p(escape(_title(status)), self.s.badge)]], hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), STATUS_COLORS.get(status, MUTED)),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    def table(self, header: list[str], rows: list[list[Any]], widths: list[float], right_cols: tuple[int, ...] = ()) -> Table:
        data = [[self.p(escape(h), self.s.cell_head_r if i in right_cols else self.s.cell_head) for i, h in enumerate(header)]]
        for row in rows:
            data.append([
                c if not isinstance(c, str) else self.p(c, self.s.cell_r if i in right_cols else self.s.cell)
                for i, c in enumerate(row)
            ])
        t = Table(data, colWidths=[w * self.width for w in widths], repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), INK),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]
        for i in range(2, len(data), 2):
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FAFAFA")))
        t.setStyle(TableStyle(style))
        return t

    def section(self, title: str) -> None:
        self.out.append(CondPageBreak(40 * mm))
        self.out.append(self.p(escape(title), self.s.h2))

    def source(self, path: str) -> str:
        prov = self.r.get("provenance", {}).get(path)
        if not prov:
            return ""
        if prov.get("status") == "calculated":
            return "Calculated"
        if prov.get("page"):
            return f"Page {prov['page']}"
        if prov.get("json_path"):
            return escape(prov["json_path"].replace("$.ITR.", ""))
        return escape(prov.get("method", ""))

    # ------------------------------------------------------------------
    def story(self) -> list[Any]:
        self.cover()
        self.summary()
        if self.r["document"]["extraction_status"] in ("success", "partial"):
            self.income()
            self.tax()
            self.business()
            self.tds()
            self.checks()
        self.yoy()
        self.indicators()
        self.limitations()
        return self.out

    def cover(self) -> None:
        r, s = self.r, self.s
        tp = r["taxpayer"]
        self.out.append(self.p(escape(tp.get("name") or "Taxpayer name not available"), s.h1))
        self.out.append(self.p(
            f"PAN {escape(tp.get('pan_masked') or NA)} &nbsp;·&nbsp; Assessment year {escape(r.get('assessment_year') or NA)}"
            f" &nbsp;·&nbsp; Financial year {escape(r.get('financial_year') or NA)}", s.small))
        self.out.append(Spacer(1, 8))
        d, rd = r["document"], r["return_details"]
        pairs = [
            ("ITR form", d.get("itr_form") or "Not stated in document"),
            ("Document", _title(d.get("document_kind")) or _title(d.get("source_type")) or NA),
            ("Tax regime", _title(rd.get("tax_regime")) or NA),
            ("Filing date", rd.get("filing_date") or NA),
            ("Acknowledgement no.", rd.get("acknowledgement_number") or NA),
            ("Return type", _title(rd.get("return_type")) or NA),
            ("Generated", r.get("generated_at", "").replace("T", " ").replace("+00:00", " UTC")),
            ("Extraction confidence", f"{Decimal(d['extraction_confidence']) * 100:.0f}%" if d.get("extraction_confidence") else NA),
        ]
        cells = [[self.p(escape(k), s.label), self.p(escape(str(v)), s.cell)] for k, v in pairs]
        rows = [cells[i] + cells[i + 1] for i in range(0, len(cells), 2)]
        t = Table(rows, colWidths=[w * self.width for w in (0.17, 0.33, 0.17, 0.33)])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), TINT),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ]))
        self.out.append(t)

    def summary(self) -> None:
        r, s = self.r, self.s
        self.section("Executive summary")
        status_row = Table(
            [[self.p("Extraction", s.label), self.badge(r["document"]["extraction_status"]),
              self.p("Validation", s.label), self.badge(r["validation"]["status"])]],
            colWidths=[22 * mm, 40 * mm, 22 * mm, 50 * mm], hAlign="LEFT",
        )
        status_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        self.out.append(status_row)
        self.out.append(Spacer(1, 8))
        inc, tax = r["income"], r["tax_computation"]
        gti = inc.get("gross_total_income_reported") or inc.get("gross_total_income_calculated")
        if tax.get("refund_reported") is not None:
            outcome_label, outcome = "Refund claimed", tax["refund_reported"]
        elif tax.get("demand_reported") is not None:
            outcome_label, outcome = "Tax payable", tax["demand_reported"]
        else:
            outcome_label, outcome = "Refund / payable", None
        metrics = [
            ("Gross total income" + (" (calc.)" if gti and not inc.get("gross_total_income_reported") else ""), gti),
            ("Taxable income", inc.get("taxable_income_reported")),
            ("Total tax liability", tax.get("total_tax_liability_reported")),
            (outcome_label, outcome),
        ]
        tiles = [[self.p(escape(label), s.label) for label, _ in metrics], [self.p(self.money(v), s.metric) for _, v in metrics]]
        t = Table(tiles, colWidths=[self.width / 4] * 4)
        t.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, RULE), ("INNERGRID", (0, 0), (-1, -1), 0.6, RULE),
            ("LINEABOVE", (0, 0), (-1, 0), 2, ACCENT),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        self.out.append(t)
        self.out.append(Spacer(1, 8))
        summary = r["validation"].get("summary") or {}
        indicators = r.get("risk_indicators", [])
        sev: dict[str, int] = {}
        for i in indicators:
            sev[i["severity"]] = sev.get(i["severity"], 0) + 1
        if r["document"]["extraction_status"] not in ("success", "partial"):
            reason = "; ".join(w for w in r["document"]["warnings"] if w).rstrip(".")
            text = f"The document could not be analysed: {escape(reason)}. No figures are shown."
        else:
            checks_text = ", ".join(f"{n} {_title(k).lower()}" for k, n in sorted(summary.items())) or "no checks"
            ind_text = ", ".join(f"{n} {k}" for k, n in sorted(sev.items())) or "none"
            text = (f"{sum(summary.values())} deterministic checks were run ({escape(checks_text)}). "
                    f"Document-review indicators: {escape(ind_text)}. Figures are taken from the document unless marked "
                    "as calculated; missing figures are shown as “Not available”, not as zero.")
        self.out.append(self.p(text))

    def income(self) -> None:
        inc = self.r["income"]
        self.section("Income breakdown")
        rows = []
        for key, label in [
            ("salary", "Salary / pension"), ("house_property", "House property"),
            ("business_profession", "Business / profession"), ("capital_gains", "Capital gains"),
            ("other_sources", "Other sources"),
        ]:
            rows.append([escape(label), self.money(inc.get(key)), self.source(f"income.{key}")])
        rows += [
            ["<b>Gross total income (reported)</b>", self.money(inc.get("gross_total_income_reported")), self.source("income.gross_total_income_reported")],
            ["Gross total income (sum of heads)", self.money(inc.get("gross_total_income_calculated")), "Calculated" if inc.get("gross_total_income_calculated") else ""],
            ["Less: deductions (Chapter VI-A)", self.money(inc.get("deductions_reported")), self.source("income.deductions_reported")],
            ["Total income (before rounding)", self.money(inc.get("total_income_reported")), self.source("income.total_income_reported")],
            ["<b>Taxable income (reported)</b>", self.money(inc.get("taxable_income_reported")), self.source("income.taxable_income_reported")],
            ["Taxable income (rounded u/s 288A)", self.money(inc.get("taxable_income_calculated")), "Calculated" if inc.get("taxable_income_calculated") else ""],
            ["Agricultural income (exempt, rate purposes)", self.money(inc.get("agricultural_income")), self.source("income.agricultural_income")],
            ["Other exempt income", self.money(inc.get("exempt_income")), self.source("income.exempt_income")],
        ]
        self.out.append(self.table(["Head", "Amount", "Source"], rows, [0.55, 0.25, 0.20], right_cols=(1,)))

        items = inc.get("line_items") or {}
        if items:
            detail = []
            for head, entries in items.items():
                for e in entries:
                    detail.append([escape(_title(head)), escape(e["description"]), self.money(e["amount"]), f"Page {e['page']}" if e.get("page") else ""])
            self.out.append(Spacer(1, 6))
            self.out.append(self.p("Component lines printed in the document", self.s.label))
            self.out.append(Spacer(1, 3))
            self.out.append(self.table(["Head", "Description", "Amount", "Source"], detail, [0.2, 0.47, 0.18, 0.15], right_cols=(2,)))

    def tax(self) -> None:
        t = self.r["tax_computation"]
        self.section("Tax computation and payment reconciliation")
        rows = [
            ["Income used for tax rate", self.money(t.get("rate_base_income")), self.source("tax_computation.rate_base_income")],
            ["Tax on total income", self.money(t.get("tax_on_total_income")), self.source("tax_computation.tax_on_total_income")],
            ["Less: rebate on agricultural income", self.money(t.get("rebate_agricultural")), self.source("tax_computation.rebate_agricultural")],
            ["Less: rebate u/s 87A", self.money(t.get("rebate_87a")), self.source("tax_computation.rebate_87a")],
            ["Less: relief u/s 89", self.money(t.get("relief_89")), self.source("tax_computation.relief_89")],
            ["Add: surcharge", self.money(t.get("surcharge")), self.source("tax_computation.surcharge")],
            ["Add: health & education cess", self.money(t.get("cess")), self.source("tax_computation.cess")],
            ["<b>Total tax liability (reported)</b>", self.money(t.get("total_tax_liability_reported")), self.source("tax_computation.total_tax_liability_reported")],
            ["Total tax liability (from components)", self.money(t.get("total_tax_liability_calculated")), "Calculated" if t.get("total_tax_liability_calculated") else ""],
            ["Interest u/s 234A/B/C", self.money(t.get("interest")), self.source("tax_computation.interest")],
            ["TDS", self.money(t.get("tds")), self.source("tax_computation.tds")],
            ["TCS", self.money(t.get("tcs")), self.source("tax_computation.tcs")],
            ["Advance tax", self.money(t.get("advance_tax")), self.source("tax_computation.advance_tax")],
            ["Self-assessment tax", self.money(t.get("self_assessment_tax")), self.source("tax_computation.self_assessment_tax")],
            ["<b>Total taxes paid (reported)</b>", self.money(t.get("total_taxes_paid_reported")), self.source("tax_computation.total_taxes_paid_reported")],
            ["Total taxes paid (sum of credits shown)", self.money(t.get("total_taxes_paid_calculated")), "Calculated" if t.get("total_taxes_paid_calculated") else ""],
            ["Net position (+ payable / − refundable)", self.money(t.get("net_position_calculated"), signed=True), "Calculated" if t.get("net_position_calculated") else ""],
            ["<b>Refund claimed</b>", self.money(t.get("refund_reported")), self.source("tax_computation.refund_reported")],
            ["<b>Tax payable</b>", self.money(t.get("demand_reported")), self.source("tax_computation.demand_reported")],
        ]
        self.out.append(self.table(["Item", "Amount", "Source"], rows, [0.55, 0.25, 0.20], right_cols=(1,)))

    def business(self) -> None:
        b = self.r["business"]
        if not any(v is not None for v in b.values()):
            return
        self.section("Business and presumptive income")
        rows = [
            ["Presumptive section", self.esc(b.get("presumptive_section")), self.source("business.presumptive_section")],
            ["Gross turnover / receipts", self.money(b.get("gross_turnover")), self.source("business.gross_turnover")],
            ["Received via banking / digital modes", self.money(b.get("turnover_digital")), self.source("business.turnover_digital")],
            ["Received in cash", self.money(b.get("turnover_cash")), self.source("business.turnover_cash")],
            ["Received via other modes", self.money(b.get("turnover_other")), self.source("business.turnover_other")],
            ["Actual profit", self.money(b.get("actual_profit")), self.source("business.actual_profit")],
            ["Deemed profit (as stated)", self.money(b.get("deemed_profit_stated")), self.source("business.deemed_profit_stated")],
            ["Deemed profit (engine, AY rates)", self.money(b.get("deemed_profit_calculated")), "Calculated" if b.get("deemed_profit_calculated") else ""],
            ["Profit offered for tax", self.money(b.get("declared_profit")), self.source("business.declared_profit")],
            ["Tax audit applicable", self.esc(b.get("tax_audit_applicable")), self.source("business.tax_audit_applicable")],
        ]
        self.out.append(self.table(["Item", "Value", "Source"], rows, [0.55, 0.25, 0.20], right_cols=(1,)))

    def tds(self) -> None:
        entries = self.r["tax_computation"].get("tds_details") or []
        if not entries:
            return
        self.section("TDS details")
        rows = [[escape(e.get("deductor_tan") or ""), escape(e.get("deductor_name") or ""), escape(e.get("section") or ""),
                 self.money(e.get("income")), self.money(e.get("tds"))] for e in entries]
        self.out.append(self.table(["TAN", "Deductor", "Section", "Income", "TDS"], rows, [0.17, 0.39, 0.12, 0.16, 0.16], right_cols=(3, 4)))

    def checks(self) -> None:
        checks = self.r["validation"]["checks"]
        if not checks:
            return
        self.section("Validation findings")
        order = {"fail": 0, "warning": 1, "insufficient_data": 2, "not_applicable": 3, "pass": 4}
        rows = []
        for c in sorted(checks, key=lambda c: order.get(c["status"], 9)):
            colour = STATUS_COLORS.get(c["status"], MUTED).hexval().replace("0x", "#")
            detail = escape(c["explanation"])
            if c.get("expected_calculated") is not None or c.get("actual_reported") is not None:
                detail += (f'<br/><font color="#6B7280">Calculated {escape(_inr(c.get("expected_calculated"), self.rupee))}'
                           f' · Reported {escape(_inr(c.get("actual_reported"), self.rupee))}</font>')
            rows.append([escape(c["title"]), f'<font color="{colour}"><b>{escape(_title(c["status"]))}</b></font>', detail])
        self.out.append(self.table(["Check", "Result", "Detail"], rows, [0.32, 0.14, 0.54]))

    def yoy(self) -> None:
        y = self.r.get("year_over_year") or {}
        comparisons = y.get("comparisons") or []
        if not comparisons:
            return
        self.section("Year-over-year comparison")
        if y.get("match_basis") == "name":
            self.out.append(self.p("Documents were matched by name only; confirm they belong to the same taxpayer.", self.s.small))
        rows = []
        for c in comparisons:
            pct = f"{c['change_pct']}%" if c.get("change_pct") is not None else "—"
            rows.append([escape(c["label"]), f"{escape(c['from_assessment_year'])} to {escape(c['to_assessment_year'])}",
                         self.money(c.get("from_value")), self.money(c.get("to_value")), escape(pct)])
        self.out.append(self.table(["Metric", "Years", "From", "To", "Change"], rows, [0.3, 0.18, 0.18, 0.18, 0.16], right_cols=(2, 3, 4)))

    def indicators(self) -> None:
        self.section("Document-review indicators")
        indicators = self.r.get("risk_indicators") or []
        if not indicators:
            self.out.append(self.p("No review indicators were raised for this document."))
            return
        for ind in indicators:
            colour = SEVERITY_COLORS.get(ind["severity"], MUTED)
            body = [
                self.p(f'<b>{escape(ind["title"])}</b> &nbsp;<font color="#6B7280" size="7">{escape(ind["code"])} · rule {escape(ind["rule_version"])}</font>'),
                self.p(escape(ind["explanation"])),
                self.p(f'<font color="#6B7280">Suggested action:</font> {escape(ind["suggested_action"])}'),
            ]
            t = Table([[self.p(escape(ind["severity"].upper()), self.s.badge), body]], colWidths=[18 * mm, self.width - 18 * mm])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), colour), ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#FAFAFA")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            self.out.append(KeepTogether([t, Spacer(1, 5)]))

    def limitations(self) -> None:
        self.section("Missing data and limitations")
        items = list(self.r.get("limitations") or []) + [w for w in self.r["document"].get("warnings") or [] if w]
        for item in dict.fromkeys(items):
            self.out.append(self.p(f"•&nbsp; {escape(item)}"))
        self.out.append(Spacer(1, 10))
        box = Table([[self.p(f"<b>Disclaimer.</b> {escape(self.r.get('disclaimer', ''))}", self.s.small)]], colWidths=[self.width])
        box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, ACCENT), ("BACKGROUND", (0, 0), (-1, -1), TINT),
                                 ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
        self.out.append(KeepTogether([box]))
