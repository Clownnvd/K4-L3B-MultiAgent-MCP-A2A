"""Evaluate source-bound numeric proposals; never execute model-generated code."""

from __future__ import annotations

import re
from copy import deepcopy
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


def _validate_calculation_shape(calculation: dict) -> None:
    if not isinstance(calculation, dict):
        raise ValueError("Unexpected arithmetic fields")
    fields = {"target", "operation", "operands"}
    is_net = calculation.get("operation") == "net"
    if is_net:
        fields.add("positive_count")
    if set(calculation) != fields:
        raise ValueError("Unexpected arithmetic fields")
    if not isinstance(calculation["operands"], list):
        raise ValueError("Operands must be a list")
    if is_net:
        count = calculation["positive_count"]
        if type(count) is not int or not 1 <= count <= len(calculation["operands"]):
            raise ValueError("net positive_count must be an integer within the operand count")


def verify_calculations(output: dict, calculations: list, ledger: dict) -> None:
    """Every positive submitted amount must equal a calculation over cited data."""
    allowed = set(monetary_paths(output))
    bound: set[str] = set()
    for calculation in calculations:
        _validate_calculation_shape(calculation)
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
            values.append(numeric(_strict_source_value(ledger[ref]["data"], pointer)))
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
        elif operation == "net":
            count = calculation["positive_count"]
            result = max(
                Decimal(0), sum(values[:count], Decimal(0)) - sum(values[count:], Decimal(0))
            )
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


def _strict_source_value(value: Any, pointer: str) -> Any:
    """Resolve an exact data-relative pointer, never repair a guessed source path."""
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError("Invalid source pointer")
    if pointer == "":
        return value
    for token in pointer[1:].split("/"):
        if re.search(r"~(?![01])", token):
            raise ValueError("Invalid JSON pointer escape")
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                raise ValueError("Array index must be canonical and nonnegative")
            index = int(token)
            if index >= len(value):
                raise ValueError("Numeric source pointer does not exist")
            value = value[index]
        elif isinstance(value, dict) and token in value:
            value = value[token]
        else:
            raise ValueError("Numeric source pointer does not exist")
    return value


def materialize_calculations(output: dict, calculations: list, ledger: dict) -> dict:
    """Copy output and compute only declared monetary leaves from cited evidence.

    This establishes arithmetic, not source relevance or policy eligibility. The
    caller must still validate the official schema, entity/claim scope, cross-field
    invariants and missing financial evidence. No input or business decision changes.
    """
    if not isinstance(calculations, list):
        raise ValueError("Calculations must be a list")
    result = deepcopy(output)
    allowed = set(monetary_paths(result))
    bound: set[str] = set()
    cited = set(result["evidence_refs"])
    for calculation in calculations:
        _validate_calculation_shape(calculation)
        target = calculation["target"]
        if not isinstance(target, str) or target not in allowed or target in bound:
            raise ValueError("Duplicate or forbidden arithmetic target")
        operands = calculation["operands"]
        if not isinstance(operands, list):
            raise ValueError("Operands must be a list")
        values = []
        seen: set[tuple[str, str]] = set()
        for operand in operands:
            if not isinstance(operand, dict) or set(operand) != {"evidence_ref", "pointer"}:
                raise ValueError("Only source-bound arithmetic operands are allowed")
            ref, pointer = operand["evidence_ref"], operand["pointer"]
            if not isinstance(ref, str) or ref not in ledger or ref not in cited:
                raise ValueError("Unknown or uncited arithmetic evidence")
            if not isinstance(pointer, str) or (ref, pointer) in seen:
                raise ValueError("Repeated or invalid source operand")
            seen.add((ref, pointer))
            values.append(numeric(_strict_source_value(ledger[ref]["data"], pointer)))
        operation = calculation["operation"]
        if operation == "zero" and not values:
            amount = Decimal(0)
        elif operation == "sum" and values:
            amount = sum(values, Decimal(0))
        elif operation == "subtract" and len(values) == 2:
            amount = values[0] - values[1]
        elif operation == "remaining" and values:
            amount = max(Decimal(0), values[0] - sum(values[1:], Decimal(0)))
        elif operation == "minimum" and values:
            amount = min(values)
        elif operation == "net":
            count = calculation["positive_count"]
            amount = max(
                Decimal(0), sum(values[:count], Decimal(0)) - sum(values[count:], Decimal(0))
            )
        else:
            raise ValueError("Unsupported arithmetic operation or arity")
        try:
            amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)
        except InvalidOperation as error:
            raise ValueError("Monetary result exceeds supported decimal precision") from error
        if amount < 0:
            raise ValueError("Negative monetary result")
        encoded = float(amount)
        if numeric(encoded) != amount:
            raise ValueError("JSON number loses source-bound precision")
        # Targets come from the fixed allowlist, not arbitrary model-written paths.
        tokens = target[1:].split("/")
        parent = result
        for token in tokens[:-1]:
            parent = parent[int(token)] if isinstance(parent, list) else parent[token]
        if tokens[-1] not in parent:
            raise ValueError("Missing output monetary field")
        parent[tokens[-1]] = encoded
        bound.add(target)
    # Keep the existing checker independent: all remaining nonzero money needs a binding.
    verify_calculations(result, calculations, ledger)
    return result
