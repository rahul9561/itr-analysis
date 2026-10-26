"""Exact-money helpers and transparent calculation records.

All arithmetic uses :class:`decimal.Decimal`. Rounding is applied only where a
statutory rule says so (sections 288A / 288B), and every calculation records
its formula and inputs so it can be shown in the JSON output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, Iterable

from .exceptions import MalformedValueError
from .models import format_money

_AMOUNT_CLEAN_RE = re.compile(r"[,\s₹]|Rs\.?|INR", re.IGNORECASE)


def to_money(raw: Any) -> Decimal:
    """Parse an amount such as ``"1,23,456"``, ``"(-) 11484"``, ``"-500.50"`` or an int.

    Raises :class:`MalformedValueError` for anything that is not a plain number.
    Floats are rejected to avoid binary rounding artefacts.
    """
    if isinstance(raw, bool) or raw is None:
        raise MalformedValueError(f"Not an amount: {raw!r}")
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        # JSON numbers with fractions: go via repr to keep the written digits.
        return Decimal(repr(raw))
    text = str(raw).strip()
    negative = False
    if text.startswith("(-)"):
        negative, text = True, text[3:]
    elif text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    text = _AMOUNT_CLEAN_RE.sub("", text)
    if text.startswith("-"):
        negative, text = (not negative), text[1:]
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        raise MalformedValueError(f"Not an amount: {raw!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - guarded by regex
        raise MalformedValueError(f"Not an amount: {raw!r}") from exc
    return -value if negative else value


def round_to_nearest_ten(amount: Decimal) -> Decimal:
    """Round as prescribed by sections 288A (total income) and 288B (tax / refund).

    Paise are ignored first; then a remainder of five rupees or more is rounded
    up to the next multiple of ten, otherwise it is dropped.
    """
    sign = -1 if amount < 0 else 1
    rupees = abs(amount).quantize(Decimal("1"), rounding=ROUND_DOWN)
    remainder = rupees % 10
    base = rupees - remainder
    if remainder >= 5:
        base += 10
    return base * sign


ZERO = Decimal("0")


@dataclass
class Calculation:
    """An engine-calculated value with its explanation."""

    id: str
    description: str
    formula: str
    inputs: dict[str, Decimal | None]
    result: Decimal | None
    explanation: str = ""
    rule_reference: str | None = None
    missing_inputs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "id": self.id,
            "description": self.description,
            "formula": self.formula,
            "inputs": {k: format_money(v) for k, v in self.inputs.items()},
            "result": format_money(self.result),
            "status": "calculated",
            "explanation": self.explanation,
        }
        if self.rule_reference:
            out["rule_reference"] = self.rule_reference
        if self.missing_inputs:
            out["missing_inputs"] = self.missing_inputs
        return out


def sum_present(values: dict[str, Decimal | None]) -> tuple[Decimal | None, list[str], list[str]]:
    """Sum the values that are present.

    Returns ``(total, present_keys, missing_keys)``; ``total`` is ``None`` when
    nothing was present. Missing values are *not* treated as zero by callers
    unless they explicitly document that assumption.
    """
    present = [k for k, v in values.items() if v is not None]
    missing = [k for k, v in values.items() if v is None]
    if not present:
        return None, present, missing
    return sum((values[k] for k in present), ZERO), present, missing


def pct_change(old: Decimal, new: Decimal) -> Decimal | None:
    """Percentage change rounded to two decimals; ``None`` when the base is zero."""
    if old == 0:
        return None
    return ((new - old) / abs(old) * 100).quantize(Decimal("0.01"))


def total(values: Iterable[Decimal]) -> Decimal:
    return sum(values, ZERO)
