"""Narrow mechanical grounding; never infer a new business decision or refund amount."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import datetime
from typing import Any


class GroundingError(ValueError):
    """Safe, fixed error codes suitable for model feedback and operational traces."""


ENTITY_KEYS = {
    "order_ids": {"order_id"},
    "item_ids": {"order_item_id", "item_id", "item_ids"},
    "seller_ids": {"seller_id", "seller_ids"},
    "payment_references": {"payment_reference", "payment_ref", "payment_id", "payment_references"},
    "shipment_ids": {"shipment_id", "shipment_ids"},
}
MONEY_ACTIONS = {"issue_refund", "refund_freight", "refund_duplicate_charge", "retry_refund"}
LATE_EVENTS = {"delivered_late", "seller_delay", "logistics_delay", "late_delivery_seller",
               "late_delivery_logistics", "carrier_delay", "shipping_delay"}


def _records(value: Any, order_id: str | None = None) -> Iterator[tuple[dict, str | None]]:
    if isinstance(value, dict):
        scope = value.get("order_id", order_id)
        yield value, scope
        for child in value.values():
            yield from _records(child, scope)
    elif isinstance(value, list):
        for child in value:
            yield from _records(child, order_id)


def _values(value: Any) -> set[str]:
    if isinstance(value, str) and value:
        return {value}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str) and item}
    return set()


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except ValueError:
        return None


def _first(record: dict, *keys: str) -> Any:
    return next((record[key] for key in keys if key in record), None)


def _shipment_check(output: dict, scoped: list[tuple[str, dict]]) -> None:
    verdict = output.get("shipment_analysis", {}).get("verdict")
    if verdict not in {"logistics_delay", "seller_delay"}:
        return
    if any(record.get("event_type") in LATE_EVENTS for _, record in scoped):
        return
    comparisons = []
    incomplete = False
    for domain, record in scoped:
        if domain not in {"order", "customer", "shipment", "item"}:
            continue
        if verdict == "logistics_delay":
            relevant = {"delivered_customer_at", "order_delivered_customer_date",
                        "estimated_delivery_at", "order_estimated_delivery_date"}
            if not relevant.intersection(record):
                continue
            pairs = [(_first(record, "delivered_customer_at", "order_delivered_customer_date"),
                      _first(record, "estimated_delivery_at", "order_estimated_delivery_date"))]
        else:
            limits = record.get("shipping_limits")
            if isinstance(limits, list) and limits:
                dates = [_first(item, "shipping_limit_at", "shipping_limit_date")
                         if isinstance(item, dict) else None for item in limits]
            elif "shipping_limit_at" in record or "shipping_limit_date" in record:
                dates = [_first(record, "shipping_limit_at", "shipping_limit_date")]
            else:
                if any(key in record for key in (
                    "delivered_carrier_at", "order_delivered_carrier_date"
                )):
                    incomplete = True
                continue
            carrier = _first(record, "delivered_carrier_at", "order_delivered_carrier_date")
            # An item-only deadline is not an independently complete shipment version.
            if carrier is None and not any(k in record for k in (
                "delivered_carrier_at", "order_delivered_carrier_date", "shipping_limits"
            )):
                continue
            pairs = [(carrier, date) for date in dates]
        for actual, promised in pairs:
            first, second = _timestamp(actual), _timestamp(promised)
            if first is None or second is None:
                incomplete = True
            else:
                comparisons.append(first <= second)
    if comparisons and all(comparisons) and not incomplete:
        raise GroundingError("SHIPMENT_DELAY_CONTRADICTED")


def _refund_check(output: dict, scoped: list[tuple[str, dict]]) -> None:
    if output.get("payment_analysis", {}).get("verdict") != "refund_failed":
        return
    witnessed = any(
        domain == "refund" and (
            record.get("event_type") == "refund_failed"
            or (str(record.get("event_type", "")).startswith("refund")
                and record.get("status") == "failed")
        ) for domain, record in scoped
    )
    if not witnessed:
        raise GroundingError("REFUND_FAILURE_UNWITNESSED")


def _policy_actions(output: dict, case: dict, ledger: dict) -> None:
    issue = output.get("assessment", {}).get("primary_issue")
    choices = []
    for ref, envelope in sorted(ledger.items()):
        data = envelope.get("data")
        if envelope.get("domain") != "policy" or not isinstance(data, dict):
            continue
        if case.get("policy_version") and data.get("policy_version") != case["policy_version"]:
            continue
        rule = data.get("rules", {}).get(issue)
        action = rule.get("recommended_action") if isinstance(rule, dict) else None
        if isinstance(action, str) and action:
            choices.append((ref, action))
    if not choices:
        return
    if len({action for _, action in choices}) != 1:
        raise GroundingError("POLICY_ACTION_CONFLICT")
    ref, action = choices[0]
    finance = output.get("financial_resolution", {})
    compatible = (
        isinstance(finance.get("recommended_refund_brl"), (int, float))
        and not isinstance(finance.get("recommended_refund_brl"), bool)
        and finance["recommended_refund_brl"] > 0
        and output.get("assessment", {}).get("case_status") == "action_required"
        and output.get("entity_resolution", {}).get("status") == "resolved"
    )
    output["resolution_actions"] = [] if action in MONEY_ACTIONS and not compatible else [action]
    refs = output.setdefault("evidence_refs", [])
    if ref not in refs:
        refs.append(ref)


def build_grounded_output(output: dict, case: dict, ledger: dict) -> dict:
    """Return an independent copy with source-key IDs and compatible exact policy actions.

    Records need an explicit or inherited resolved order ID to witness entity fields.
    No chronology version is selected, no claim verdict is rewritten, and no money is changed.
    The two contradiction gates fail closed with fixed codes, leaving repair/abstention to callers.
    """
    result = deepcopy(output)
    if result.get("case_id") != case.get("case_id"):
        raise GroundingError("CASE_SCOPE_MISMATCH")
    resolved = set(result.get("entity_resolution", {}).get("resolved_order_ids", []))
    scoped = [(envelope.get("domain", ""), record)
              for envelope in ledger.values() if envelope.get("domain") != "policy"
              for record, order_id in _records(envelope.get("data")) if order_id in resolved]
    entities = result.setdefault("affected_entities", {})
    for field, keys in ENTITY_KEYS.items():
        witnessed = set().union(*(_values(record.get(key))
                                  for _, record in scoped for key in keys))
        if field == "order_ids":
            witnessed &= resolved
        entities[field] = list(dict.fromkeys(
            item for item in entities.get(field, []) if item in witnessed
        ))
    _shipment_check(result, scoped)
    _refund_check(result, scoped)
    _policy_actions(result, case, ledger)
    return result
