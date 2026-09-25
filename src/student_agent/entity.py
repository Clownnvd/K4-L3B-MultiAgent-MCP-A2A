"""Resolve a uniquely witnessed customer/order identity without resolving fact versions."""
from __future__ import annotations

from typing import Any


def unique_history_candidate(case: dict, evidence: dict) -> str | None:
    """Select one scoped identity to confirm, without disproving other candidates.

    History is received in this case before calling this helper. Duplicate rows for
    an order are fact versions, not additional identities. Missing/ambiguous history
    cannot narrow the investigation; ID spelling has no meaning here.
    """
    hint = case.get("customer_unique_id_hint")
    data = evidence.get("data")
    if (not isinstance(hint, str) or not hint or evidence.get("domain") != "customer"
            or not isinstance(data, dict) or data.get("customer_unique_id") != hint
            or data.get("status") == "tool_execution_failed"
            or not isinstance(data.get("orders"), list)):
        return None
    candidates = set(case.get("candidate_order_ids", []))
    claimed = case.get("customer_request", {}).get("claimed_order_id")
    if claimed:
        candidates.add(claimed)
    linked = {
        row["order_id"] for row in data["orders"]
        if isinstance(row, dict) and isinstance(row.get("order_id"), str)
        and row["order_id"] in candidates
    }
    return next(iter(linked)) if len(linked) == 1 else None


def resolve_from_sources(case: dict, orders: dict, ledger: dict) -> dict | None:
    """Return a conservative identity resolution, or None for the model fallback.

    Only received order objects and received customer-history membership can establish
    identity. Different dates/statuses of that same ID remain a downstream facts problem.
    Unreadable candidate placeholders are never labeled rejected.
    """
    hint = case.get("customer_unique_id_hint")
    if not isinstance(hint, str) or not hint:
        return None
    request = case.get("customer_request", {})
    claimed = request.get("claimed_order_id") if isinstance(request, dict) else None
    candidates = {value for value in case.get("candidate_order_ids", [])
                  if isinstance(value, str) and value}
    if isinstance(claimed, str) and claimed:
        candidates.add(claimed)

    memberships: dict[str, set[str]] = {}
    for ref, evidence in ledger.items():
        if (not isinstance(evidence, dict) or evidence.get("evidence_ref") != ref
                or evidence.get("domain") != "customer"):
            continue
        data = evidence.get("data")
        if not isinstance(data, dict) or data.get("status") == "tool_execution_failed":
            continue
        customer = data.get("customer_unique_id")
        history = data.get("orders")
        if not isinstance(customer, str) or not customer or not isinstance(history, list):
            continue
        for row in history:
            order_id = row.get("order_id") if isinstance(row, dict) else None
            if isinstance(order_id, str) and order_id in candidates:
                memberships.setdefault(order_id, set()).add(customer)

    selected = []
    for candidate in sorted(candidates):
        evidence: Any = orders.get(candidate)
        if not isinstance(evidence, dict) or evidence.get("domain") != "order":
            continue
        ref = evidence.get("evidence_ref")
        if not isinstance(ref, str) or ref not in ledger or ledger[ref] != evidence:
            continue
        data = evidence.get("data")
        if (not isinstance(data, dict) or data.get("order_id") != candidate
                or data.get("status") == "tool_execution_failed"):
            continue
        customers = memberships.get(candidate, set())
        if hint in customers and customers != {hint}:
            return None
        witnessed_customer = data.get("customer_unique_id")
        if hint in customers and witnessed_customer not in {None, hint}:
            return None
        if customers == {hint}:
            selected.append(candidate)
    if len(selected) != 1:
        return None
    return {"status": "resolved", "resolved_order_ids": selected,
            "rejected_candidates": [], "confidence": 0.95}
