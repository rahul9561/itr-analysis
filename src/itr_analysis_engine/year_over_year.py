"""Year-over-year comparison across several analysed returns of one taxpayer.

Returns are grouped by PAN when available (compared in memory only; PANs are
never written to the output), otherwise by normalised name, which is weaker
and labelled as such. Where one taxpayer has more than one document for the
same assessment year, that year is reported as a duplicate and excluded.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from .calculations import pct_change
from .models import format_money

METRICS = [
    ("income.gross_total_income", "Gross total income"),
    ("income.taxable_income_reported", "Taxable income"),
    ("income.salary", "Salary"),
    ("income.house_property", "House property"),
    ("income.business_profession", "Business / profession"),
    ("income.capital_gains", "Capital gains"),
    ("income.other_sources", "Other sources"),
    ("tax_computation.total_tax_liability_reported", "Total tax liability"),
    ("business.gross_turnover", "Business turnover"),
]


def _metric(report: dict[str, Any], path: str) -> tuple[Decimal | None, str | None]:
    section, key = path.split(".", 1)
    block = report.get(section) or {}
    if path == "income.gross_total_income":
        if block.get("gross_total_income_reported") is not None:
            return Decimal(block["gross_total_income_reported"]), "reported"
        if block.get("gross_total_income_calculated") is not None:
            return Decimal(block["gross_total_income_calculated"]), "calculated"
        return None, None
    value = block.get(key)
    return (Decimal(value), "reported") if value is not None else (None, None)


def compare_years(entries: list[tuple[str | None, str | None, dict[str, Any]]]) -> list[dict[str, Any]]:
    """``entries`` are ``(pan_or_none, name_or_none, report_dict)``.

    Returns one group per taxpayer with two or more analysed assessment years,
    and attaches a ``year_over_year`` block to each participating report.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for pan, name, report in entries:
        if report.get("document", {}).get("extraction_status") not in ("success", "partial"):
            continue
        if not report.get("assessment_year"):
            continue
        if pan:
            groups[("pan", pan)].append(report)
        elif name:
            groups[("name", name.upper())].append(report)

    results = []
    for index, ((basis, _), reports) in enumerate(sorted(groups.items(), key=lambda kv: kv[0][1])):
        by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in reports:
            by_year[r["assessment_year"]].append(r)
        duplicates = sorted(ay for ay, rs in by_year.items() if len(rs) > 1)
        years = sorted(ay for ay, rs in by_year.items() if len(rs) == 1)
        if len(years) < 2 and not duplicates:
            continue
        group_id = f"taxpayer-group-{index + 1}"
        comparisons = []
        for prev_ay, next_ay in zip(years, years[1:]):
            prev, nxt = by_year[prev_ay][0], by_year[next_ay][0]
            consecutive = int(next_ay[:4]) - int(prev_ay[:4]) == 1
            for path, label in METRICS:
                old, old_basis = _metric(prev, path)
                new, new_basis = _metric(nxt, path)
                if old is None and new is None:
                    continue
                entry: dict[str, Any] = {
                    "metric": path,
                    "label": label,
                    "from_assessment_year": prev_ay,
                    "to_assessment_year": next_ay,
                    "consecutive_years": consecutive,
                    "from_value": format_money(old),
                    "to_value": format_money(new),
                    "value_basis": sorted({b for b in (old_basis, new_basis) if b}),
                    "change": None,
                    "change_pct": None,
                    "status": "calculated",
                }
                if old is None or new is None:
                    entry["status"] = "not_comparable"
                    entry["note"] = "Value unavailable in one of the years; not treated as zero."
                else:
                    entry["change"] = format_money(new - old)
                    pct = pct_change(old, new)
                    entry["change_pct"] = str(pct) if pct is not None else None
                    if pct is None:
                        entry["note"] = "Percentage change undefined because the earlier value is zero."
                comparisons.append(entry)
        group = {
            "taxpayer_group": group_id,
            "match_basis": basis,
            "assessment_years": years,
            "duplicate_assessment_years": duplicates,
            "report_ids": [r["report_id"] for r in reports],
            "comparisons": comparisons,
        }
        if basis == "name":
            group["note"] = "Grouped by name only (no PAN available); confirm the documents belong to one taxpayer."
        results.append(group)
        for r in reports:
            r["year_over_year"] = {
                "taxpayer_group": group_id,
                "match_basis": basis,
                "assessment_years": years,
                "duplicate_assessment_years": duplicates,
                "comparisons": comparisons,
            }
    return results
