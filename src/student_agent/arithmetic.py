"""Evaluate source-bound numeric proposals; never execute model-generated code."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

CENT = Decimal("0.01")


def pointer_get(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("JSON pointer must begin with /")
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(token)] if isinstance(value, list) else value[token]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("Numeric source pointer does not exist") from exc
    return value


def numeric(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("Missing or boolean value cannot be used as money")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Source is not an unambiguous decimal number") from exc
    if not result.is_finite():
        raise ValueError("Non-finite numeric value")
    return result


def monetary_paths(output: dict) -> list[str]:
    paths = [
        "/financial_resolution/recommended_refund_brl",
        "/payment_analysis/captured_total_brl",
        "/payment_analysis/refunded_total_brl",
        "/payment_analysis/refundable_total_brl",
    ]
    paths.extend(
        f"/financial_resolution/refund_lines/{i}/amount_brl"
        for i in range(len(output["financial_resolution"]["refund_lines"]))
    )
    return paths


def verify_calculations(output: dict, calculations: list, ledger: dict) -> None:
    """Every positive submitted amount must equal a calculation over cited data."""
    allowed = set(monetary_paths(output))
    bound: set[str] = set()
    for calculation in calculations:
        if set(calculation) != {"target", "operation", "operands"}:
            raise ValueError("Unexpected arithmetic fields")
        target = calculation["target"]
        if target not in allowed or target in bound:
            raise ValueError("Duplicate or forbidden arithmetic target")
        values = []
        pointers = []
        for operand in calculation["operands"]:
            if set(operand) != {"evidence_ref", "pointer"}:
                raise ValueError("Only source-bound arithmetic operands are allowed")
            ref, pointer = operand["evidence_ref"], operand["pointer"]
            if ref not in ledger:
                raise ValueError("Unknown arithmetic evidence")
            values.append(numeric(pointer_get(ledger[ref]["data"], pointer)))
            pointers.append((ref, pointer))
        if len(pointers) != len(set(pointers)):
            raise ValueError("Repeated source operand would double-count evidence")
        operation = calculation["operation"]
        if operation == "zero" and not values:
            result = Decimal(0)
        elif operation == "sum" and values:
            result = sum(values, Decimal(0))
        elif operation == "subtract" and len(values) == 2:
            result = values[0] - values[1]
        elif operation == "remaining" and values:
            result = max(Decimal(0), values[0] - sum(values[1:], Decimal(0)))
        elif operation == "minimum" and values:
            result = min(values)
        else:
            raise ValueError("Unsupported arithmetic operation or arity")
        expected = result.quantize(CENT, rounding=ROUND_HALF_UP)
        actual = numeric(pointer_get(output, target))
        if actual != expected:
            raise ValueError(f"Source arithmetic mismatch at {target}")
        bound.add(target)
    for target in allowed:
        value = pointer_get(output, target)
        if value is not None and numeric(value) != 0 and target not in bound:
            raise ValueError(f"Unbound arithmetic amount at {target}")
