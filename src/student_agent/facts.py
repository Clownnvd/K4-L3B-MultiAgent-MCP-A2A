"""Source-local observations for model context, without deciding source precedence.

Different purchase episodes may share an order identifier. Dates are compared
only within a received record; no monetary totals or global case verdicts are
inferred here. Context conflicts are richer than official output conflicts and
must not be copied into the official output without schema/policy review.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

_ID_FIELDS = {
    "order_id": "order_ids", "order_item_id": "item_ids", "item_id": "item_ids",
    "seller_id": "seller_ids", "payment_reference": "payment_references",
    "payment_id": "payment_references", "shipment_id": "shipment_ids",
}
_FIELDS = {
    "order_status": "order_status",
    "order_purchase_timestamp": "purchase_timestamp", "purchase_timestamp": "purchase_timestamp",
    "order_approved_at": "approved_at", "approved_at": "approved_at",
    "order_delivered_carrier_date": "carrier_at", "delivered_carrier_at": "carrier_at",
    "order_delivered_customer_date": "delivered_at", "delivered_customer_at": "delivered_at",
    "order_estimated_delivery_date": "estimated_at", "estimated_delivery_at": "estimated_at",
    "shipping_limit_date": "shipping_limit_at", "shipping_limit_at": "shipping_limit_at",
}


def _pointer(parent: str, key: str | int) -> str:
    return parent + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _difference(left: Any, right: Any) -> float | None:
    first, second = _date(left), _date(right)
    if first is None or second is None:
        return None
    try:
        return (first - second).total_seconds()
    except TypeError:
        # Never assume a timezone for a naive timestamp to make a comparison work.
        return None


def _comparison(actual: dict | None, deadline: dict | None) -> dict:
    elapsed = _difference(actual["value"], deadline["value"]) if actual and deadline else None
    return {"verdict": "unknown" if elapsed is None else "late" if elapsed > 0 else "on_time",
            "difference_seconds": elapsed, "actual_source": actual, "deadline_source": deadline,
            "record_local_only": True}


def source_fact_state(case: dict, ledger: dict) -> dict:
    """Return witnessed IDs, dated observations, conflicts and optional interpretations.

    `candidate_interpretations` are explicitly suggestions: latest purchase before
    case opening is not an authoritative precedence rule. Future records remain
    in the observations and conflicts. Evidence references never become entity IDs.
    """
    sources: dict[str, dict[str, list]] = {
        name: {} for name in ("order_ids", "item_ids", "seller_ids",
                             "payment_references", "shipment_ids")}
    observations = []
    fields_by_order: dict[tuple[str, str], list[dict]] = defaultdict(list)

    def field_source(value: Any, ref: str, pointer: str) -> dict:
        return {"value": value, "evidence_ref": ref, "pointer": pointer}

    def walk(value: Any, ref: str, domain: str, pointer: str = "") -> None:
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, ref, domain, _pointer(pointer, index))
            return
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            category = _ID_FIELDS.get(key)
            if (category and isinstance(child, str) and child
                    and not child.startswith("ev_")):
                sources[category].setdefault(child, []).append({
                    "evidence_ref": ref, "pointer": _pointer(pointer, key)})
        order = value.get("order_id")
        if (isinstance(order, str) and order and not order.startswith("ev_")
                and (any(key in _FIELDS for key in value) or "shipping_limits" in value)):
            fields: dict[str, list[dict]] = defaultdict(list)
            for key, canonical in _FIELDS.items():
                if key in value:
                    source = field_source(value[key], ref, _pointer(pointer, key))
                    fields[canonical].append(source)
                    fields_by_order[(order, canonical)].append(source)

            def unambiguous(field: str) -> dict | None:
                known = [item for item in fields.get(field, []) if item["value"] is not None]
                if len({json.dumps(item["value"], sort_keys=True) for item in known}) == 1:
                    return known[0]
                return None

            purchase = unambiguous("purchase_timestamp")
            after_opened = (_difference(purchase["value"], case.get("opened_at"))
                            if purchase else None)
            carrier = unambiguous("carrier_at")
            handoffs = []
            limits = value.get("shipping_limits", [])
            for index, limit in enumerate(limits if isinstance(limits, list) else []):
                if not isinstance(limit, dict):
                    continue
                key = next((name for name in ("shipping_limit_at", "shipping_limit_date")
                            if name in limit), None)
                deadline = field_source(limit[key], ref, _pointer(
                    _pointer(_pointer(pointer, "shipping_limits"), index), key)) if key else None
                comparison = _comparison(carrier, deadline)
                comparison["seller_id"] = limit.get("seller_id")
                comparison["item_id"] = limit.get("order_item_id", limit.get("item_id"))
                handoffs.append(comparison)
            if not handoffs:
                handoffs.append(_comparison(carrier, unambiguous("shipping_limit_at")))
            observations.append({
                "order_id": order, "evidence_ref": ref, "pointer": pointer, "domain": domain,
                "fields": dict(fields),
                "purchase_after_case_opened": None if after_opened is None else after_opened > 0,
                "delivery_comparison": _comparison(unambiguous("delivered_at"),
                                                    unambiguous("estimated_at")),
                "handoff_comparisons": handoffs,
            })
        for key, child in value.items():
            walk(child, ref, domain, _pointer(pointer, key))

    for ref, envelope in ledger.items():
        walk(envelope["data"], ref, envelope["domain"])

    conflicts = []
    for (order, field), values in fields_by_order.items():
        known = [value for value in values if value["value"] is not None]
        if len({json.dumps(value["value"], sort_keys=True) for value in known}) > 1:
            conflicts.append({"order_id": order, "field": field,
                              "sources": list(dict.fromkeys(v["evidence_ref"] for v in known)),
                              "observations": known, "selected_source": None,
                              "resolution_code": "UNRESOLVED_SOURCE_VERSION_CONFLICT"})

    interpretations = []
    for order in sources["order_ids"]:
        eligible = []
        for row in observations:
            if row["order_id"] != order or row["purchase_after_case_opened"] is not False:
                continue
            purchases = row["fields"].get("purchase_timestamp", [])
            if len(purchases) == 1:
                elapsed = _difference(purchases[0]["value"], case.get("opened_at"))
                if elapsed is not None:
                    eligible.append((elapsed, row, purchases[0]))
        if not eligible:
            continue
        latest = max(item[0] for item in eligible)
        newest = [item for item in eligible if item[0] == latest]
        # Same timestamp with contradictory statuses/delivery fields is not a unique version.
        latest_fields: dict[str, set[str]] = defaultdict(set)
        for _, row, _ in newest:
            for field, values in row["fields"].items():
                for source in values:
                    if source["value"] is not None:
                        latest_fields[field].add(json.dumps(source["value"], sort_keys=True))
        if any(len(values) > 1 for values in latest_fields.values()):
            continue
        interpretations.append({
            "order_id": order, "purchase_timestamp": newest[0][2]["value"],
            "basis": "UNIQUE_LATEST_PURCHASE_NOT_AFTER_CASE_OPENED", "suggestion_only": True,
            "sources": [item[2] for item in newest],
            "does_not_establish_source_precedence": True,
        })
    return {"allowed_entity_ids": {name: sorted(values) for name, values in sources.items()},
            "entity_sources": sources, "order_observations": observations,
            "data_conflicts": conflicts, "candidate_interpretations": interpretations}
