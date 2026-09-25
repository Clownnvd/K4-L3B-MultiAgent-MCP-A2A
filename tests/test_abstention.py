import asyncio
from pathlib import Path

import pytest

from student_agent.abstention import build_abstention
from student_agent.arithmetic import verify_calculations
from student_agent.contracts import Contracts
from student_agent.demo import DemoGateway, demo_cases
from student_agent.verifier import verify_output

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    case = demo_cases(1)[0]
    gateway = DemoGateway([case])
    responses = [asyncio.run(gateway.call("get_order", case_id=case["case_id"], order_id=order))
                 for order in case["candidate_order_ids"]]
    return case, {response["evidence_ref"]: response for response in responses}


def test_abstention_is_valid_unknown_not_a_financial_decision():
    case, ledger = fixture()
    output = build_abstention(case, ledger)
    Contracts(ROOT / "contracts/schemas").validate_output(output, case["case_id"])
    verify_output(case["case_id"], output, ledger)
    verify_calculations(output, [], ledger)
    assert output["assessment"] == {"primary_issue": "insufficient_evidence",
        "secondary_issues": [], "case_status": "needs_investigation", "confidence": 0}
    assert output["entity_resolution"]["status"] == "ambiguous"
    assert output["affected_entities"]["order_ids"] == []
    assert all(output["payment_analysis"][field] is None for field in (
        "captured_total_brl", "refunded_total_brl", "refundable_total_brl"))
    assert output["financial_resolution"]["recommended_refund_brl"] == 0
    assert output["financial_resolution"]["refund_lines"] == []
    claims = output["claim_assessments"]
    assert [claim["claim_id"] for claim in claims] == [
        claim["claim_id"] for claim in case["customer_request"]["claims"]]
    assert all(claim["confidence"] == 0 and claim["verdict"] == "insufficient_evidence"
               and set(claim["evidence_refs"]) <= set(ledger) for claim in claims)


def test_abstention_preserves_only_a_valid_received_entity_resolution():
    case, ledger = fixture()
    resolution = {"status": "resolved", "resolved_order_ids": ["demo-order-0001"],
                  "rejected_candidates": ["wrong-order-0001"], "confidence": 1}
    output = build_abstention(case, ledger, resolution)
    assert output["entity_resolution"] == resolution
    assert output["affected_entities"]["order_ids"] == ["demo-order-0001"]
    output["entity_resolution"]["resolved_order_ids"].append("mutation")
    assert resolution["resolved_order_ids"] == ["demo-order-0001"]
    resolution["resolved_order_ids"] = ["invented-order"]
    output = build_abstention(case, ledger, resolution)
    assert output["entity_resolution"]["status"] == "ambiguous"
    assert output["affected_entities"]["order_ids"] == []


def test_abstention_requires_real_consumed_evidence_and_bounds_refs():
    case, ledger = fixture()
    with pytest.raises(ValueError, match="evidence"):
        build_abstention(case, {})
    template = next(iter(ledger.values()))
    for index in range(25):
        ref = f"ev_demo_extra_{index:020d}"
        ledger[ref] = {**template, "evidence_ref": ref}
    output = build_abstention(case, ledger)
    assert len(output["evidence_refs"]) == 20
    assert set(output["evidence_refs"]) <= set(ledger)
    Contracts(ROOT / "contracts/schemas").validate_output(output, case["case_id"])
    verify_output(case["case_id"], output, ledger)
