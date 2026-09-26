import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from student_agent.evidence import CaseEvidence
from student_agent.model_policy import require_allowed_model
from student_agent.verifier import verify_output


class Trace:
    def __init__(self):
        self.events = []

    def emit(self, **event):
        self.events.append(event)


def evidence(ref="ev_" + "a" * 24):
    return {"schema_version": "day09-mcp-evidence-v1", "domain": "order",
            "evidence_ref": ref, "result_hash": "sha256:" + "a" * 64,
            "data": {"order_id": "order-test"}}


def test_model_policy_rejects_quantized_14b_and_unknown_size():
    require_allowed_model("deterministic", 0)
    require_allowed_model("verified-small-model", 8_200_000_000)
    require_allowed_model("limit", 10_000_000_000)
    with pytest.raises(ValueError):
        require_allowed_model("14B-AWQ", 14_700_000_000)
    with pytest.raises(ValueError):
        require_allowed_model("unknown", None)


def test_case_evidence_caches_only_within_case_and_returns_copies():
    class Gateway:
        def __init__(self):
            self.calls = []

        async def call(self, tool, **arguments):
            self.calls.append((tool, arguments))
            return evidence()

    async def exercise():
        g = Gateway()
        c = CaseEvidence("CASE_A", g, Trace())
        first = await c.fetch("entity-agent", "get_order", order_id="order-test")
        first["data"]["order_id"] = "tampered"
        second = await c.fetch("entity-agent", "get_order", order_id="order-test")
        assert second["data"]["order_id"] == "order-test"
        assert len(g.calls) == 1
        other = CaseEvidence("CASE_B", g, Trace())
        await other.fetch("entity-agent", "get_order", order_id="order-test")
        assert len(g.calls) == 2
        assert g.calls[-1][1]["case_id"] == "CASE_B"
    asyncio.run(exercise())


def test_reused_evidence_ref_cannot_change_contents():
    """An evidence reference is immutable inside one case-scoped ledger."""

    class ConflictingGateway:
        async def call(self, tool, **arguments):
            result = evidence()
            result["data"]["order_id"] = arguments["order_id"]
            return result

    async def exercise():
        scoped = CaseEvidence("CASE_NGAN_AUDIT", ConflictingGateway(), Trace())
        await scoped.fetch("entity-agent", "get_order", order_id="order-a")
        with pytest.raises(ValueError, match="changed its contents"):
            await scoped.fetch("entity-agent", "get_order", order_id="order-b")

    asyncio.run(exercise())


def test_agent_cannot_call_outside_permission():
    c = CaseEvidence("CASE_A", SimpleNamespace(), Trace())
    with pytest.raises(ValueError, match="permission"):
        asyncio.run(c.fetch("shipment-agent", "get_customer_history", customer_unique_id="x"))


def output_fixture():
    return {
        "case_id": "CASE_A", "assessment": {"primary_issue": "valid_split_payment",
          "case_status": "no_action", "confidence": 0.9},
        "entity_resolution": {"status": "resolved", "resolved_order_ids": ["o1"],
          "rejected_candidates": ["o2"], "confidence": 0.9},
        "affected_entities": {"order_ids": ["o1"]},
        "evidence_refs": ["ev_" + "a" * 24], "claim_assessments": [],
        "data_conflicts": [], "resolution_actions": ["explain_valid_split_payment"],
        "payment_analysis": {"captured_total_brl": 20, "refunded_total_brl": 0,
          "refundable_total_brl": 20},
        "financial_resolution": {"recommended_refund_brl": 0, "refund_lines": []},
    }


def test_verifier_rejects_fabricated_ref_and_double_refund():
    valid = output_fixture()
    ledger = {valid["evidence_refs"][0]: evidence()}
    verify_output("CASE_A", valid, ledger)
    bad = deepcopy(valid)
    bad["evidence_refs"].append("ev_" + "z" * 24)
    with pytest.raises(ValueError, match="evidence"):
        verify_output("CASE_A", bad, ledger)
    bad = deepcopy(valid)
    bad["assessment"]["case_status"] = "action_required"
    bad["financial_resolution"] = {"recommended_refund_brl": 25,
                                  "refund_lines": [{"amount_brl": 25}]}
    with pytest.raises(ValueError, match="refundable"):
        verify_output("CASE_A", bad, ledger)


def test_verifier_rejects_candidate_overlap_and_bad_sum():
    valid = output_fixture()
    ledger = {valid["evidence_refs"][0]: evidence()}
    bad = deepcopy(valid)
    bad["entity_resolution"]["rejected_candidates"] = ["o1"]
    with pytest.raises(ValueError, match="candidate"):
        verify_output("CASE_A", bad, ledger)
    bad = deepcopy(valid)
    bad["financial_resolution"]["refund_lines"] = [{"amount_brl": 1}]
    with pytest.raises(ValueError, match="sum"):
        verify_output("CASE_A", bad, ledger)
