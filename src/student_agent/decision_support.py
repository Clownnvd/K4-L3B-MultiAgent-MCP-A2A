"""Explain source-backed decision candidates without choosing the final issue."""

from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from itertools import combinations

from .arithmetic import numeric
from .episode_grounding import in_episode


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except ValueError:
        return None


def derive_decision_support(context: dict, ledger: dict) -> dict:
    result = {"signals": [], "candidate_only": True}
    orders = context["entity_resolution"]["resolved_order_ids"]
    if len(orders) != 1:
        return result
    interpretations = [
        row
        for row in context["source_facts"]["candidate_interpretations"]
        if row["order_id"] == orders[0]
    ]
    if len(interpretations) != 1:
        return result
    anchor = _date(interpretations[0]["purchase_timestamp"])
    if anchor is None:
        return result
    versions = [
        row
        for row in context["order_versions"]
        if any(
            _date(source["value"]) == anchor
            for source in row["fields"].get("purchase_timestamp", [])
        )
    ]
    result["matching_order_version_ids"] = [row["id"] for row in versions]
    if not versions:
        return result

    def current(row):
        return in_episode(row, versions[0], context) is True

    captures = [row for row in context["capture_choices"] if current(row)]
    result["capture_ids_in_selected_period"] = [row["id"] for row in captures]
    result["captured_sum_by_code"] = str(sum((numeric(r["value"]) for r in captures), Decimal(0)))
    refunds = [
        row
        for row in context["refund_events"]
        if current(row) and str(row.get("event_type", "")).startswith("refund")
    ]
    if refunds:
        last_time = max(_date(row["event_at"]) for row in refunds)
        last = [row for row in refunds if _date(row["event_at"]) == last_time]
        statuses = {row.get("status") for row in last}
        if len(statuses) == 1 and next(iter(statuses)) in {"pending", "failed"}:
            result["signals"].append(
                {
                    "issue": "refund_" + next(iter(statuses)),
                    "basis": "LATEST_SCOPED_REFUND_REQUEST_STATE",
                    "sources": [
                        {"evidence_ref": row["evidence_ref"], "pointer": row["event_pointer"]}
                        for row in last
                    ],
                }
            )
    for row in context["payment_events"]:
        if current(row) and "mismatch" in row.get("event_type", ""):
            result["signals"].append(
                {
                    "issue": "payment_mismatch",
                    "basis": "SCOPED_RECONCILIATION_EVENT",
                    "sources": [
                        {"evidence_ref": row["evidence_ref"], "pointer": row["event_pointer"]}
                    ],
                }
            )
    statuses = {
        source["value"] for row in versions for source in row["fields"].get("order_status", [])
    }
    if captures and statuses in ({"canceled"}, {"unavailable"}):
        result["signals"].append(
            {
                "issue": next(iter(statuses)) + "_order_paid",
                "basis": "DATED_ORDER_STATUS_AND_SETTLED_CAPTURE",
            }
        )

    # Sum each actual item once only when all received versions agree on its amount.
    items = defaultdict(set)
    for envelope in ledger.values():
        data = envelope.get("data")
        if envelope.get("domain") != "item" or not isinstance(data, list):
            continue
        for row in data:
            item = row.get("order_item_id", row.get("item_id"))
            if row.get("order_id") == orders[0] and item:
                try:
                    items[str(item)].add(numeric(row["price"]) + numeric(row["freight_value"]))
                except (KeyError, ValueError):
                    return result
    if not items or any(len(amounts) != 1 for amounts in items.values()):
        return result
    total = sum((next(iter(amounts)) for amounts in items.values()), Decimal(0))
    result["consistent_item_total_by_code"] = str(total)
    payment_rows = {}
    for row in context["payment_records"]:
        if row.get("order_id") != orders[0]:
            continue
        try:
            amount = numeric(row["payment_value"])
        except (KeyError, ValueError):
            continue
        key = (str(row.get("payment_sequential", "")), row.get("payment_type"), amount)
        payment_rows[key] = row
    if not 2 <= len(captures) <= 8:
        return result
    for count in range(2, len(captures) + 1):
        for selected in combinations(captures, count):
            amounts = Counter(numeric(row["value"]) for row in selected)
            if sum(amounts.elements(), Decimal(0)) != total:
                continue
            matches = [(key, row) for key, row in payment_rows.items() if key[2] in amounts]
            if (
                len(matches) != count
                or Counter(key[2] for key, _ in matches) != amounts
                or len({key[0] for key, _ in matches}) != count
                or any(not key[0] or not key[1] for key, _ in matches)
                or len({key[1] for key, _ in matches}) < 2
            ):
                continue
            result["signals"].append(
                {
                    "issue": "valid_split_payment",
                    "basis": "DISTINCT_METHODS_MATCH_ITEM_TOTAL",
                    "capture_ids": [row["id"] for row in selected],
                    "payment_methods": sorted({key[1] for key, _ in matches}),
                    "other_captures_still_require_review": len(captures) != count,
                }
            )
    return result
