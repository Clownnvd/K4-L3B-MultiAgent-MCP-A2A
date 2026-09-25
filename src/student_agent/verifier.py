"""Independent cross-field checks. JSON Schema is checked separately."""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def money(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("Money must be finite and non-negative")
    return result


def verify_output(
    case_id: str, output: dict[str, Any], ledger: dict[str, dict[str, Any]]
) -> None:
    if output["case_id"] != case_id:
        raise ValueError("case_id mismatch")
    refs = output["evidence_refs"]
    if not refs or any(ref not in ledger for ref in refs):
        raise ValueError("Output contains unknown or missing case evidence")
    if len(refs) != len(set(refs)):
        raise ValueError("Duplicate evidence reference")
    for claim in output.get("claim_assessments", []):
        if any(ref not in refs for ref in claim["evidence_refs"]):
            raise ValueError("Claim evidence must be a subset of output evidence")
        if claim["verdict"] != "insufficient_evidence" and not claim["evidence_refs"]:
            raise ValueError("Conclusive claim needs evidence")
    entities = output["entity_resolution"]
    resolved = set(entities["resolved_order_ids"])
    rejected = set(entities["rejected_candidates"])
    if resolved & rejected:
        raise ValueError("Resolved and rejected candidate sets overlap")
    if resolved != set(output["affected_entities"]["order_ids"]):
        raise ValueError("Affected order IDs do not match entity resolution")
    if entities["status"] == "resolved" and not resolved:
        raise ValueError("Resolved status needs an order")
    actions = output["resolution_actions"]
    if len(actions) != len(set(actions)):
        raise ValueError("Duplicate resolution action")
    finance = output["financial_resolution"]
    refund = money(finance["recommended_refund_brl"])
    lines = sum((money(line["amount_brl"]) for line in finance["refund_lines"]), Decimal(0))
    if abs(lines - refund) > Decimal("0.005"):
        raise ValueError("Refund lines do not sum to the total")
    payment = output["payment_analysis"]
    available = payment["refundable_total_brl"]
    if available is not None and refund > money(available) + Decimal("0.005"):
        raise ValueError("Refund exceeds refundable balance")
    captured, refunded = payment["captured_total_brl"], payment["refunded_total_brl"]
    if (
        captured is not None
        and refunded is not None
        and available is not None
        and money(available)
        > max(Decimal(0), money(captured) - money(refunded)) + Decimal("0.005")
    ):
        raise ValueError("Refundable balance exceeds captured less refunded")
    if output["assessment"]["case_status"] == "no_action" and refund:
        raise ValueError("No-action case cannot recommend a refund")
    for conflict in output.get("data_conflicts", []):
        selected = conflict["selected_source"]
        if selected is not None and selected not in conflict["sources"]:
            raise ValueError("Conflict selected_source is absent from candidate sources")
