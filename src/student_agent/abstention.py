"""Explicit abstention after an unsuccessful decision, without inventing evidence.

This is a deterministic safety response, not an LLM decision or a claim that the
case was solved. Callers must record why they abstained and still validate the
output with the official contracts and case evidence before packaging it.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator

from .specialists import values_for_key
from .verifier import verify_output


def _validated_resolution(case: dict, ledger: dict, resolution: Any) -> dict:
    unknown = {"status": "ambiguous", "resolved_order_ids": [],
               "rejected_candidates": [], "confidence": 0}
    if resolution is None:
        return unknown
    candidates = set(case.get("candidate_order_ids", []))
    claimed = case.get("customer_request", {}).get("claimed_order_id")
    if claimed:
        candidates.add(claimed)
    ids = {"type": "array", "maxItems": 20, "uniqueItems": True,
           "items": {"type": "string", "minLength": 1, "maxLength": 128}}
    schema = {"type": "object", "additionalProperties": False,
              "required": ["status", "resolved_order_ids", "rejected_candidates", "confidence"],
              "properties": {
                  "status": {"enum": ["resolved", "ambiguous", "not_found"]},
                  "resolved_order_ids": ids, "rejected_candidates": ids,
                  "confidence": {"type": "number", "minimum": 0, "maximum": 1},
              }}
    if not Draft202012Validator(schema).is_valid(resolution):
        return unknown
    selected = set(resolution["resolved_order_ids"])
    rejected = set(resolution["rejected_candidates"])
    witnessed = set().union(*(values_for_key(entry["data"], "order_id")
                              for entry in ledger.values() if entry["domain"] == "order"))
    if (selected & rejected or not (selected | rejected) <= (candidates & witnessed)
            or (resolution["status"] == "resolved") != bool(selected)):
        return unknown
    return deepcopy(resolution)


def build_abstention(case: dict, ledger: dict, resolution: dict | None = None) -> dict:
    """Build a schema-shaped, zero-confidence abstention from consumed evidence only.

    A validated prior entity result can be retained. Otherwise no candidate is
    selected or rejected. Financial totals remain unknown; zero is only the
    recommendation to issue no refund while investigation remains incomplete.
    """
    if not ledger:
        raise ValueError("Abstention requires consumed case evidence")
    if any(ref != entry.get("evidence_ref") for ref, entry in ledger.items()):
        raise ValueError("Abstention evidence ledger contains mismatched references")
    entity = _validated_resolution(case, ledger, resolution)
    retained_ids = set(entity["resolved_order_ids"] + entity["rejected_candidates"])
    # Keep evidence for retained entities before selecting other consumed refs.
    refs = sorted(ledger, key=lambda ref: not (
        ledger[ref]["domain"] == "order"
        and bool(values_for_key(ledger[ref]["data"], "order_id") & retained_ids)))[:20]
    claims = case.get("customer_request", {}).get("claims", [])
    if len(claims) > 5 or len({claim["claim_id"] for claim in claims}) != len(claims):
        raise ValueError("Case claims exceed the official abstention output contract")
    output = {
        "schema_version": "day09-l3b-output-v2", "case_id": case["case_id"],
        "assessment": {"primary_issue": "insufficient_evidence", "secondary_issues": [],
                       "case_status": "needs_investigation", "confidence": 0},
        "affected_entities": {"order_ids": list(entity["resolved_order_ids"]),
                              "item_ids": [], "seller_ids": [],
                              "payment_references": [], "shipment_ids": []},
        "entity_resolution": entity,
        "customer_context": {"customer_unique_id": None, "related_order_ids": []},
        "shipment_analysis": {"verdict": "insufficient_evidence", "late_seller_ids": [],
                              "timeline_complete": False},
        "payment_analysis": {"verdict": "insufficient_evidence", "captured_total_brl": None,
                             "refunded_total_brl": None, "refundable_total_brl": None},
        "root_cause_analysis": {"ranked_causes": [], "responsible_parties": []},
        "claim_assessments": [{"claim_id": claim["claim_id"], "verdict": "insufficient_evidence",
                               "confidence": 0, "evidence_refs": list(refs)} for claim in claims],
        "evidence_refs": refs, "data_conflicts": [],
        "financial_resolution": {"currency": "BRL", "recommended_refund_brl": 0,
                                 "refund_lines": []},
        "resolution_actions": [],
    }
    verify_output(case["case_id"], output, ledger)
    return output
