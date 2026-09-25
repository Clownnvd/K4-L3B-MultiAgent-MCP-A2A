"""Bind causal facts to one dated purchase episode, not to case opening time.

Opening is used to choose candidate purchases, not an accounting cutoff. A later
purchase with the same ID bounds the previous episode. Unknown or contradictory
dates do not establish responsibility. No claims or case IDs are answer rules.
"""

from collections import defaultdict
from datetime import datetime


def date(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def field(version, key):
    values = {r["value"] for r in version.get("fields", {}).get(key, []) if r["value"] is not None}
    return next(iter(values)) if len(values) == 1 else None


def bounds(version, context):
    start = date(field(version, "purchase_timestamp"))
    later = [
        date(field(v, "purchase_timestamp"))
        for v in [*context["order_versions"], *context["excluded_future"]["order_versions"]]
        if v["order_id"] == version["order_id"]
    ]
    later = [v for v in later if start is not None and v is not None and v > start]
    return start, min(later) if later else None


def in_episode(row, version, context):
    """Tri-state association; False excludes another order or known other episode."""
    if row.get("order_id") != version["order_id"]:
        return False
    start, end = bounds(version, context)
    at = date(row.get("event_at"))
    if start is None or at is None:
        return None
    return at >= start and (end is None or at < end)


def shipment_support(version, context, ledger):
    start, end = bounds(version, context)
    carrier = date(field(version, "carrier_at"))
    groups = defaultdict(list)
    sellers = set()
    for ref, envelope in ledger.items():
        rows = envelope["data"]
        if envelope["domain"] != "item" or not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or row.get("order_id") != version["order_id"]:
                continue
            key = row.get("order_item_id", row.get("item_id"))
            seller = row.get("seller_id")
            if isinstance(seller, str) and seller:
                sellers.add(seller)
            at = date(row.get("shipping_limit_date", row.get("shipping_limit_at")))
            if not key or not seller or at is None or start is None:
                continue
            if at < start or (end is not None and at >= end):
                continue
            groups[str(key)].append((seller, at, ref, index))
    late, ontime, uncertain, proofs = set(), set(), set(), []
    for item, rows in groups.items():
        choices = {(seller, at) for seller, at, _, _ in rows}
        sellers.update(seller for seller, _, _, _ in rows)
        if len(choices) != 1 or carrier is None or (end is not None and carrier >= end):
            uncertain.update(seller for seller, _, _, _ in rows)
            continue
        seller, deadline = next(iter(choices))
        (late if carrier > deadline else ontime).add(seller)
        proofs.append(
            {
                "item_id": item,
                "seller_id": seller,
                "verdict": "late" if carrier > deadline else "on_time",
                "carrier_sources": version["fields"].get("carrier_at", []),
                "deadline_sources": [
                    {"evidence_ref": ref, "pointer": f"/{i}"} for _, _, ref, i in rows
                ],
            }
        )
    verdict = (
        "seller_delay"
        if late and not uncertain
        else ("on_time" if ontime and not late and not uncertain else "unknown")
    )
    return {
        "late_seller_ids": sorted(late - uncertain),
        "seller_ids": sorted(sellers),
        "handoff_verdict": verdict,
        "handoff_proofs": proofs,
        "ambiguous_seller_ids": sorted(uncertain),
    }


def bind_parties(parties, issue, support):
    """Policy defines roles. Case evidence must independently bind concrete IDs."""
    bound = []
    for party in parties:
        if not isinstance(party, dict):
            continue
        role = party.get("party_type")
        if role == "seller":
            eligible = support.get("late_seller_ids", []) if issue == "late_delivery_seller" else []
            if issue == "unavailable_order_paid" and len(support.get("seller_ids", [])) == 1:
                # Proven unavailable order plus policy seller responsibility, uniquely bound.
                eligible = support["seller_ids"]
            bound.extend({"party_type": role, "party_id": seller} for seller in eligible)
            if not eligible:
                bound.append({"party_type": role, "party_id": None})
        else:
            # Generic roles remain useful; policy literals are not case identity evidence.
            bound.append({"party_type": role, "party_id": None})
    unique = {(p["party_type"], p["party_id"]): p for p in bound}
    return list(unique.values())[:5]
