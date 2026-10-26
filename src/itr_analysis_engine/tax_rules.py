"""Assessment-year-versioned statutory parameters.

Only parameters that the validation layer actually needs are listed, and only
for assessment years that have been configured and reviewed. When an assessment
year is not configured, rule-dependent checks return ``not_applicable`` rather
than guessing.

The engine deliberately does NOT recompute tax from slab rates; it verifies
the arithmetic of the figures reported in the return.

Every value here should be re-verified against the Income-tax Act / Finance Act
before a new assessment year is added.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PresumptiveRules44AD:
    # Deemed profit rates (Section 44AD(1)).
    rate_digital_pct: Decimal
    rate_other_pct: Decimal
    # Turnover limits (Section 44AD and the proviso inserted by Finance Act 2023).
    turnover_limit: Decimal
    enhanced_turnover_limit: Decimal
    enhanced_limit_max_cash_pct: Decimal
    reference: str


@dataclass(frozen=True)
class TaxRuleSet:
    assessment_year: str
    version: str
    total_income_rounding: str
    tax_rounding: str
    presumptive_44ad: PresumptiveRules44AD | None


_44AD_FA2023 = PresumptiveRules44AD(
    rate_digital_pct=Decimal("6"),
    rate_other_pct=Decimal("8"),
    turnover_limit=Decimal("20000000"),          # Rs 2 crore
    enhanced_turnover_limit=Decimal("30000000"),  # Rs 3 crore
    enhanced_limit_max_cash_pct=Decimal("5"),
    reference="Section 44AD as amended by Finance Act 2023 (from AY 2024-25)",
)

RULESETS: dict[str, TaxRuleSet] = {
    "2024-25": TaxRuleSet("2024-25", "2024-25.1", "Section 288A", "Section 288B", _44AD_FA2023),
    "2025-26": TaxRuleSet("2025-26", "2025-26.1", "Section 288A", "Section 288B", _44AD_FA2023),
}


def get_rules(assessment_year: str | None) -> TaxRuleSet | None:
    if not assessment_year:
        return None
    return RULESETS.get(assessment_year)


def supported_assessment_years() -> list[str]:
    return sorted(RULESETS)
