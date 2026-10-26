"""Deterministic validation and reconciliation checks.

Every check compares a source-reported figure with a value independently
calculated from other reported figures. A failed check is a *review flag*:
it says the document is internally inconsistent or incomplete, not that
anything improper happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .calculations import ZERO, Calculation, round_to_nearest_ten, sum_present
from .models import FIELD_SPECS, INCOME_HEADS, CheckStatus, ExtractedReturn, format_money
from .normalizer import fy_end_date
from .security import is_valid_pan_format
from .tax_rules import TaxRuleSet

# Fields without which a document cannot be meaningfully reviewed.
REQUIRED_FIELDS = (
    "return.assessment_year",
    "taxpayer.name",
    "income.taxable_income",
    "tax.total_tax_liability",
)


@dataclass
class ValidationCheck:
    id: str
    category: str  # completeness | income | tax | payments | business | integrity
    status: CheckStatus
    title: str
    explanation: str
    field_refs: list[str] = field(default_factory=list)
    expected: Decimal | None = None
    actual: Decimal | None = None
    calculation: Calculation | None = None
    rule_reference: str | None = None

    @property
    def difference(self) -> Decimal | None:
        if self.expected is None or self.actual is None:
            return None
        return self.actual - self.expected

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "category": self.category,
            "status": self.status.value,
            "title": self.title,
            "explanation": self.explanation,
            "field_refs": self.field_refs,
        }
        if self.expected is not None or self.actual is not None:
            out["expected_calculated"] = format_money(self.expected)
            out["actual_reported"] = format_money(self.actual)
            out["difference"] = format_money(self.difference)
        if self.calculation:
            out["calculation"] = self.calculation.to_dict()
        if self.rule_reference:
            out["rule_reference"] = self.rule_reference
        return out


class Validator:
    def __init__(self, ret: ExtractedReturn, rules: TaxRuleSet | None, tolerance: Decimal = ZERO):
        self.ret = ret
        self.rules = rules
        self.tolerance = tolerance
        self.checks: list[ValidationCheck] = []
        self.calculated: dict[str, Calculation] = {}

    # ------------------------------------------------------------------
    def run(self) -> list[ValidationCheck]:
        self.required_fields()
        self.integrity()
        self.head_components()
        self.gross_total_income()
        self.total_income()
        self.taxable_income_rounding()
        self.rate_base()
        self.tax_chain()
        self.tds_details()
        self.taxes_paid()
        self.net_position()
        self.presumptive_business()
        return self.checks

    def m(self, key: str) -> Decimal | None:
        value = self.ret.get(key)
        return value if isinstance(value, Decimal) else None

    def add(self, check: ValidationCheck) -> None:
        self.checks.append(check)
        if check.calculation:
            self.calculated[check.calculation.id] = check.calculation

    def compare(
        self,
        check_id: str,
        category: str,
        title: str,
        calc: Calculation,
        reported_key: str | None,
        reported: Decimal | None,
        *,
        pass_text: str,
        fail_text: str,
        rule_reference: str | None = None,
        extra_refs: list[str] | None = None,
    ) -> None:
        refs = list(calc.inputs) + ([reported_key] if reported_key else []) + (extra_refs or [])
        if calc.result is None or reported is None:
            missing = calc.missing_inputs + ([reported_key] if reported is None and reported_key else [])
            self.add(
                ValidationCheck(
                    check_id, category, CheckStatus.INSUFFICIENT_DATA, title,
                    "Not enough reported figures to perform this check. Missing: " + ", ".join(missing or ["inputs"]),
                    refs, calc.result, reported, calc, rule_reference,
                )
            )
            return
        ok = abs(reported - calc.result) <= self.tolerance
        self.add(
            ValidationCheck(
                check_id, category, CheckStatus.PASS if ok else CheckStatus.FAIL, title,
                pass_text if ok else fail_text, refs, calc.result, reported, calc, rule_reference,
            )
        )

    # ------------------------------------------------------------------
    def required_fields(self) -> None:
        for key in REQUIRED_FIELDS:
            present = self.ret.has(key)
            label = FIELD_SPECS[key][1]
            self.add(
                ValidationCheck(
                    f"required.{key}", "completeness",
                    CheckStatus.PASS if present else CheckStatus.FAIL,
                    f"Required field present: {label}",
                    f"{label} was found in the document." if present
                    else f"{label} was not found. It is reported as unavailable, not as zero.",
                    [key],
                )
            )
        if not (self.ret.has("tax.refund") or self.ret.has("tax.demand")):
            self.add(
                ValidationCheck(
                    "required.tax_outcome", "completeness", CheckStatus.WARNING,
                    "Refund / tax payable outcome stated",
                    "Neither a refund nor a tax-payable figure was found in the document.",
                    ["tax.refund", "tax.demand"],
                )
            )

    def integrity(self) -> None:
        ret = self.ret
        status = CheckStatus.FAIL if ret.conflicts else CheckStatus.PASS
        self.add(
            ValidationCheck(
                "integrity.conflicting_values", "integrity", status, "No contradictory duplicate values",
                "Each field that appears more than once carries the same value."
                if not ret.conflicts
                else f"{len(ret.conflicts)} field(s) appear more than once with different values: "
                + ", ".join(sorted({c['field'] for c in ret.conflicts})),
                sorted({c["field"] for c in ret.conflicts}),
            )
        )
        if ret.malformed:
            self.add(
                ValidationCheck(
                    "integrity.malformed_values", "integrity", CheckStatus.FAIL, "Values could be parsed",
                    f"{len(ret.malformed)} value(s) could not be parsed: "
                    + ", ".join(sorted({m['field'] for m in ret.malformed})),
                    sorted({m["field"] for m in ret.malformed}),
                )
            )
        pan = ret.get("taxpayer.pan")
        if pan is not None:
            self.add(
                ValidationCheck(
                    "integrity.pan_format", "integrity",
                    CheckStatus.PASS if is_valid_pan_format(pan) else CheckStatus.FAIL,
                    "PAN format", "PAN has the standard 10-character format (value not shown).",
                    ["taxpayer.pan"],
                )
            )
        ay = ret.get("return.assessment_year")
        year_end = ret.get("return.year_ending")
        if ay and year_end:
            expected = fy_end_date(ay).isoformat()
            self.add(
                ValidationCheck(
                    "integrity.previous_year_end", "integrity",
                    CheckStatus.PASS if expected == year_end else CheckStatus.FAIL,
                    "Previous year matches assessment year",
                    f"AY {ay} relates to the previous year ending {expected}; document states {year_end}.",
                    ["return.assessment_year", "return.year_ending"],
                )
            )
        filing, due = ret.get("return.filing_date"), ret.get("return.due_date")
        if filing and due and filing > due:
            self.add(
                ValidationCheck(
                    "integrity.filed_after_due_date", "integrity", CheckStatus.WARNING,
                    "Return filed after stated due date",
                    f"Filing date {filing} is after the due date {due} stated in the document.",
                    ["return.filing_date", "return.due_date"],
                )
            )

    def head_components(self) -> None:
        """Each income head should equal the sum of its printed components / section totals."""
        for head in INCOME_HEADS:
            reported = self.m(head)
            label = FIELD_SPECS[head][1]
            items = self.ret.line_items.get(head)
            if items and reported is not None:
                calc = Calculation(
                    id=f"calc.{head}.components",
                    description=f"Sum of component lines under {label}",
                    formula=" + ".join(f"[{i.description}]" for i in items),
                    inputs={f"{head}.line_items[{n}]": i.amount for n, i in enumerate(items)},
                    result=sum((i.amount for i in items), ZERO),
                    explanation=f"Adds the {len(items)} amount line(s) printed inside the {label} section "
                    "(deductions printed with (-) are subtracted).",
                )
                self.compare(
                    f"income.components.{head.split('.')[1]}", "income", f"{label}: components add up",
                    calc, head, reported,
                    pass_text="Component lines add up to the reported head total.",
                    fail_text="Component lines do not add up to the reported head total.",
                )
            for n, section_total in enumerate(self.ret.head_totals.get(head, [])):
                if reported is None:
                    continue
                ok = section_total == reported
                self.add(
                    ValidationCheck(
                        f"income.section_total.{head.split('.')[1]}.{n}", "income",
                        CheckStatus.PASS if ok else CheckStatus.FAIL,
                        f"{label}: section total matches head",
                        "The total printed at the end of the section matches the head figure."
                        if ok else "The total printed at the end of the section differs from the head figure.",
                        [head], expected=reported, actual=section_total,
                    )
                )

    def heads_sum(self) -> Calculation:
        values = {k: self.m(k) for k in INCOME_HEADS}
        result, present, missing = sum_present(values)
        return Calculation(
            id="calc.income.sum_of_heads",
            description="Sum of reported income heads",
            formula=" + ".join(present) if present else "(no heads reported)",
            inputs={k: values[k] for k in present},
            result=result,
            explanation="Adds every income head that is reported. Heads not present in the document are "
            "listed as not reported and are not assumed to be zero"
            + (f" (not reported: {', '.join(missing)})." if missing else "."),
        )

    def gross_total_income(self) -> None:
        calc = self.heads_sum()
        self.calculated[calc.id] = calc
        gti = self.m("income.gross_total_income")
        if gti is not None:
            self.compare(
                "income.gti_reconciliation", "income", "Gross total income = sum of income heads",
                calc, "income.gross_total_income", gti,
                pass_text="Reported gross total income equals the sum of reported income heads.",
                fail_text="Reported gross total income differs from the sum of reported income heads. "
                "A head may be missing from extraction, or set-off of losses may apply.",
            )
            return
        total_income = self.m("income.total_income")
        if total_income is not None and not self.ret.has("income.deductions"):
            calc.explanation += (
                " The document has no separate gross-total-income or deductions line, so the sum of heads is "
                "compared directly with Total Income."
            )
            self.compare(
                "income.gti_reconciliation", "income", "Total income = sum of income heads (no deductions shown)",
                calc, "income.total_income", total_income,
                pass_text="Reported total income equals the sum of reported income heads.",
                fail_text="Reported total income differs from the sum of reported income heads.",
            )
        else:
            self.add(
                ValidationCheck(
                    "income.gti_reconciliation", "income", CheckStatus.INSUFFICIENT_DATA,
                    "Gross total income = sum of income heads",
                    "Gross total income is not reported, so the heads cannot be reconciled to it.",
                    ["income.gross_total_income"], calc.result, None, calc,
                )
            )

    def total_income(self) -> None:
        gti, ded = self.m("income.gross_total_income"), self.m("income.deductions")
        if gti is None or ded is None:
            return
        calc = Calculation(
            id="calc.income.gti_less_deductions",
            description="Gross total income less deductions",
            formula="income.gross_total_income - income.deductions",
            inputs={"income.gross_total_income": gti, "income.deductions": ded},
            result=gti - ded,
            explanation="Deductions are taken as reported; their eligibility is not assessed.",
        )
        total = self.m("income.total_income")
        if total is not None:
            self.compare(
                "income.total_income_reconciliation", "income", "Total income = GTI - deductions",
                calc, "income.total_income", total,
                pass_text="Reported total income equals gross total income less deductions.",
                fail_text="Reported total income differs from gross total income less deductions.",
            )
        else:
            self.calculated[calc.id] = calc

    def taxable_income_rounding(self) -> None:
        taxable = self.m("income.taxable_income")
        base_key = "income.total_income"
        base = self.m(base_key)
        if base is None and "calc.income.gti_less_deductions" in self.calculated:
            base_key, base = "calc.income.gti_less_deductions", self.calculated["calc.income.gti_less_deductions"].result
        if base is None or taxable is None:
            return
        ref = self.rules.total_income_rounding if self.rules else "Section 288A"
        calc = Calculation(
            id="calc.income.taxable_rounded",
            description="Total income rounded to the nearest multiple of ten rupees",
            formula=f"round_288A({base_key})",
            inputs={base_key: base},
            result=round_to_nearest_ten(base),
            explanation="Paise are ignored; a remainder of Rs 5 or more is rounded up to the next ten, otherwise dropped.",
            rule_reference=ref,
        )
        self.compare(
            "income.taxable_income_rounding", "income", "Taxable income correctly rounded",
            calc, "income.taxable_income", taxable,
            pass_text="Reported taxable income equals total income rounded under section 288A.",
            fail_text="Reported taxable income does not equal total income rounded under section 288A.",
            rule_reference=ref,
        )

    def rate_base(self) -> None:
        base, taxable, agri = self.m("tax.rate_base_income"), self.m("income.taxable_income"), self.m("income.agricultural_income")
        if base is None or taxable is None:
            return
        inputs = {"income.taxable_income": taxable}
        if agri is not None:
            inputs["income.agricultural_income"] = agri
        calc = Calculation(
            id="calc.tax.rate_base",
            description="Income on which the tax rate is applied",
            formula=" + ".join(inputs),
            inputs=inputs,
            result=sum(inputs.values(), ZERO),
            explanation="Net agricultural income, where shown, is added only for rate purposes (partial integration).",
        )
        self.compare(
            "tax.rate_base_reconciliation", "tax", "Tax computed on the correct income figure",
            calc, "tax.rate_base_income", base,
            pass_text="The income figure used for the tax computation matches taxable income (plus agricultural income for rate purposes).",
            fail_text="The income figure used for the tax computation does not match taxable income (plus agricultural income).",
        )

    def tax_chain(self) -> None:
        tax = self.m("tax.tax_on_total_income")
        liability = self.m("tax.total_tax_liability")
        net = self.m("tax.net_tax_on_total_income")
        rebate_agri = self.m("tax.rebate_agricultural")
        if tax is not None and net is not None and rebate_agri is not None:
            calc = Calculation(
                id="calc.tax.net_after_agri_rebate",
                description="Tax less rebate on agricultural income",
                formula="tax.tax_on_total_income - tax.rebate_agricultural",
                inputs={"tax.tax_on_total_income": tax, "tax.rebate_agricultural": rebate_agri},
                result=tax - rebate_agri,
            )
            self.compare(
                "tax.net_tax_reconciliation", "tax", "Net tax = tax - agricultural rebate", calc,
                "tax.net_tax_on_total_income", net,
                pass_text="Net tax on total income is arithmetically consistent.",
                fail_text="Net tax on total income does not equal tax less the agricultural-income rebate.",
            )
        if tax is None:
            return
        adds = {k: self.m(k) for k in ("tax.surcharge", "tax.cess")}
        subs = {k: self.m(k) for k in ("tax.rebate_agricultural", "tax.rebate_87a", "tax.relief_89")}
        inputs = {"tax.tax_on_total_income": tax}
        inputs.update({k: v for k, v in adds.items() if v is not None})
        inputs.update({k: v for k, v in subs.items() if v is not None})
        result = tax + sum((v for v in adds.values() if v is not None), ZERO) - sum(
            (v for v in subs.values() if v is not None), ZERO
        )
        formula = "tax.tax_on_total_income" + "".join(f" + {k}" for k, v in adds.items() if v is not None) + "".join(
            f" - {k}" for k, v in subs.items() if v is not None
        )
        calc = Calculation(
            id="calc.tax.liability",
            description="Tax liability from reported components",
            formula=formula,
            inputs=inputs,
            result=result,
            explanation="Verifies the arithmetic of the reported tax lines only. Tax is not recomputed from slab "
            "rates, and rebate / relief eligibility is not assessed. Components not shown in the document are omitted.",
        )
        self.compare(
            "tax.liability_reconciliation", "tax", "Tax liability = tax + surcharge + cess - rebates - relief",
            calc, "tax.total_tax_liability", liability,
            pass_text="Reported tax liability is arithmetically consistent with the reported components.",
            fail_text="Reported tax liability does not match the reported components.",
        )

    def tds_details(self) -> None:
        entries = self.ret.tds_entries
        reported = self.m("tax.tds")
        if not entries or reported is None:
            return
        amounts = [e.tds for e in entries]
        calc = Calculation(
            id="calc.tax.tds_details_total",
            description="Sum of TDS shown in deductor-wise details",
            formula="sum(tds_entries[].tds)",
            inputs={f"tds_entries[{n}].tds": a for n, a in enumerate(amounts)},
            result=sum((a for a in amounts if a is not None), ZERO) if all(a is not None for a in amounts) else None,
            missing_inputs=[f"tds_entries[{n}].tds" for n, a in enumerate(amounts) if a is None],
        )
        self.compare(
            "payments.tds_details", "payments", "TDS total matches deductor-wise details",
            calc, "tax.tds", reported,
            pass_text="TDS claimed equals the sum of the deductor-wise TDS entries.",
            fail_text="TDS claimed differs from the sum of the deductor-wise TDS entries.",
        )

    def payments_sum(self) -> Calculation:
        keys = ("tax.tds", "tax.tcs", "tax.advance_tax", "tax.self_assessment_tax")
        values = {k: self.m(k) for k in keys}
        result, present, missing = sum_present(values)
        return Calculation(
            id="calc.tax.total_paid",
            description="Total tax credits and payments",
            formula=" + ".join(present) if present else "(no payments reported)",
            inputs={k: values[k] for k in present},
            result=result,
            explanation="Adds the tax credits and payments shown in the document"
            + (f"; not shown: {', '.join(missing)}." if missing else "."),
        )

    def taxes_paid(self) -> None:
        calc = self.payments_sum()
        self.calculated[calc.id] = calc
        reported = self.m("tax.total_taxes_paid")
        if reported is None:
            return
        self.compare(
            "payments.total_paid_reconciliation", "payments", "Total taxes paid = TDS + TCS + advance + self-assessment",
            calc, "tax.total_taxes_paid", reported,
            pass_text="Reported total taxes paid equals the sum of its components.",
            fail_text="Reported total taxes paid differs from the sum of its components.",
        )

    def net_position(self) -> None:
        liability = self.m("tax.total_tax_liability")
        interest = self.m("tax.interest")
        paid_reported = self.m("tax.total_taxes_paid")
        paid = paid_reported if paid_reported is not None else self.calculated["calc.tax.total_paid"].result
        paid_key = "tax.total_taxes_paid" if paid_reported is not None else "calc.tax.total_paid"
        refund, demand = self.m("tax.refund"), self.m("tax.demand")
        title = "Refund / tax payable consistent with liability and payments"
        refs = ["tax.total_tax_liability", "tax.interest", paid_key, "tax.refund", "tax.demand"]
        if liability is None or paid is None or (refund is None and demand is None):
            self.add(
                ValidationCheck(
                    "payments.net_position", "payments", CheckStatus.INSUFFICIENT_DATA, title,
                    "Tax liability, tax payments and a refund or payable figure are all needed for this check.",
                    refs,
                )
            )
            return
        inputs = {"tax.total_tax_liability": liability, paid_key: paid}
        if interest is not None:
            inputs["tax.interest"] = interest
        net = liability + (interest or ZERO) - paid
        ref = self.rules.tax_rounding if self.rules else "Section 288B"
        calc = Calculation(
            id="calc.tax.net_position",
            description="Net tax position (positive = payable, negative = refundable)",
            formula=f"tax.total_tax_liability{' + tax.interest' if interest is not None else ''} - {paid_key}",
            inputs=inputs,
            result=net,
            explanation="Refund or payable is compared with the net position both unrounded and rounded to the "
            "nearest ten rupees under section 288B.",
            rule_reference=ref,
        )
        reported_net = (demand or ZERO) - (refund or ZERO)
        candidates = {net, round_to_nearest_ten(net)}
        ok = any(abs(reported_net - c) <= self.tolerance for c in candidates)
        self.add(
            ValidationCheck(
                "payments.net_position", "payments", CheckStatus.PASS if ok else CheckStatus.FAIL, title,
                "Reported refund / payable agrees with tax liability less payments."
                if ok else "Reported refund / payable does not agree with tax liability less payments.",
                refs, expected=round_to_nearest_ten(net), actual=reported_net, calculation=calc, rule_reference=ref,
            )
        )

    def presumptive_business(self) -> None:
        section = self.ret.get("business.presumptive_section")
        if section != "44AD":
            if section:
                self.add(
                    ValidationCheck(
                        "business.presumptive_rules", "business", CheckStatus.NOT_APPLICABLE,
                        f"Section {section} presumptive checks",
                        f"No rule set is configured for section {section}; only the figures are reported.",
                        ["business.presumptive_section"],
                    )
                )
            return
        rules = self.rules.presumptive_44ad if self.rules else None
        if rules is None:
            self.add(
                ValidationCheck(
                    "business.presumptive_rules", "business", CheckStatus.NOT_APPLICABLE, "Section 44AD checks",
                    "No 44AD rule set is configured for this assessment year; deemed-profit checks were skipped.",
                    ["return.assessment_year"],
                )
            )
            return
        digital, cash, other = (self.m(k) for k in ("business.turnover_digital", "business.turnover_cash", "business.turnover_other"))
        turnover = self.m("business.gross_turnover")
        if None not in (digital, cash, other):
            calc = Calculation(
                id="calc.business.turnover_total",
                description="Turnover by mode of receipt",
                formula="business.turnover_digital + business.turnover_cash + business.turnover_other",
                inputs={"business.turnover_digital": digital, "business.turnover_cash": cash, "business.turnover_other": other},
                result=digital + cash + other,
            )
            self.compare(
                "business.turnover_breakdown", "business", "Turnover breakdown adds up", calc,
                "business.gross_turnover", turnover,
                pass_text="Turnover by mode of receipt adds up to gross turnover.",
                fail_text="Turnover by mode of receipt does not add up to gross turnover.",
            )
            hundred = Decimal("100")
            deemed = Calculation(
                id="calc.business.deemed_profit_44ad",
                description="Minimum deemed profit u/s 44AD",
                formula=f"{rules.rate_digital_pct}% x business.turnover_digital + {rules.rate_other_pct}% x "
                "(business.turnover_cash + business.turnover_other)",
                inputs={"business.turnover_digital": digital, "business.turnover_cash": cash, "business.turnover_other": other},
                result=(digital * rules.rate_digital_pct / hundred + (cash + other) * rules.rate_other_pct / hundred),
                rule_reference=rules.reference,
            )
            self.compare(
                "business.deemed_profit", "business", "Deemed profit computed at the 44AD rates", deemed,
                "business.deemed_profit", self.m("business.deemed_profit"),
                pass_text="Deemed profit stated in the document matches the 44AD rates for this assessment year.",
                fail_text="Deemed profit stated in the document differs from the 44AD rates for this assessment year.",
                rule_reference=rules.reference,
            )
            declared = self.m("business.declared_profit") or self.m("income.business_profession")
            if declared is not None and deemed.result is not None:
                ok = declared >= deemed.result
                self.add(
                    ValidationCheck(
                        "business.declared_vs_deemed", "business", CheckStatus.PASS if ok else CheckStatus.WARNING,
                        "Profit offered is at least the 44AD deemed profit",
                        "Profit offered is not lower than the deemed profit."
                        if ok else "Profit offered is lower than the 44AD deemed profit; this has consequences under "
                        "section 44AD(5) / 44AB that need professional review.",
                        ["business.declared_profit", "calc.business.deemed_profit_44ad"],
                        expected=deemed.result, actual=declared, rule_reference=rules.reference,
                    )
                )
        if turnover is not None:
            cash_share = (cash / turnover * 100) if (cash is not None and turnover) else None
            if cash_share is None and rules.turnover_limit < turnover <= rules.enhanced_turnover_limit:
                self.add(
                    ValidationCheck(
                        "business.44ad_turnover_limit", "business", CheckStatus.INSUFFICIENT_DATA,
                        "Turnover within the 44AD limit",
                        "Turnover is above the basic limit; cash receipts are not shown, so eligibility for the "
                        "enhanced limit cannot be determined.",
                        ["business.gross_turnover", "business.turnover_cash"], rule_reference=rules.reference,
                    )
                )
                return
            enhanced = cash_share is not None and cash_share <= rules.enhanced_limit_max_cash_pct
            limit = rules.enhanced_turnover_limit if enhanced else rules.turnover_limit
            ok = turnover <= limit
            self.add(
                ValidationCheck(
                    "business.44ad_turnover_limit", "business", CheckStatus.PASS if ok else CheckStatus.FAIL,
                    "Turnover within the 44AD limit",
                    f"Gross turnover is {'within' if ok else 'above'} the 44AD limit of Rs {format_money(limit)} "
                    f"({'enhanced limit: cash receipts do not exceed ' + str(rules.enhanced_limit_max_cash_pct) + '%' if enhanced else 'basic limit'}).",
                    ["business.gross_turnover", "business.turnover_cash"],
                    expected=limit, actual=turnover, rule_reference=rules.reference,
                )
            )


def validate(ret: ExtractedReturn, rules: TaxRuleSet | None, tolerance: Decimal = ZERO) -> Validator:
    validator = Validator(ret, rules, tolerance)
    validator.run()
    return validator
