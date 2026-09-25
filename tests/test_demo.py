"""Offline fixture acceptance checks; no live model or service is contacted."""
import asyncio
from pathlib import Path

import pytest

from student_agent.contracts import Contracts
from student_agent.demo import DemoGateway, DemoModel, demo_cases
from student_agent.trace import TraceWriter
from student_agent.workflow import Orchestrator

ROOT = Path(__file__).resolve().parents[1]


def test_demo_gateway_contract_scope_and_immutable_responses():
    async def check():
        contracts = Contracts(ROOT / "contracts/schemas")
        cases = demo_cases(2)
        gateway = DemoGateway(cases)
        arguments = {"case_id": cases[0]["case_id"],
                     "order_id": cases[0]["candidate_order_ids"][0]}
        response = await gateway.call("get_order", **arguments)
        contracts.validate_evidence(response)
        assert response["evidence_ref"].startswith("ev_demo_")
        assert response["warnings"] == ["SYNTHETIC_DEMO_ONLY"]
        response["data"]["order_status"] = "corrupted"
        assert (await gateway.call("get_order", **arguments))["data"]["order_status"] != "corrupted"
        with pytest.raises(ValueError, match="scope"):
            await gateway.call("get_order", case_id=cases[1]["case_id"],
                               order_id=arguments["order_id"])
        with pytest.raises(ValueError, match="tool"):
            await gateway.call("write_refund", **arguments)
        with pytest.raises(ValueError, match="case"):
            await gateway.call("get_order", case_id="REAL_CASE_001", order_id="real-order")
    asyncio.run(check())


def test_hundred_synthetic_cases_pass_real_orchestrator(tmp_path):
    async def check():
        contracts = Contracts(ROOT / "contracts/schemas")
        cases = demo_cases(100)
        assert len({case["case_id"] for case in cases}) == 100
        gateway = DemoGateway(cases)
        solver = Orchestrator(contracts, DemoModel())
        trace = TraceWriter(tmp_path / "trace.jsonl", contracts)
        outputs = await asyncio.gather(*(solver.solve(case, gateway, trace) for case in cases))
        assert outputs[0]["financial_resolution"]["recommended_refund_brl"] == 100
        assert outputs[0]["entity_resolution"]["rejected_candidates"] == ["wrong-order-0001"]
        assert {output["assessment"]["case_status"] for output in outputs} == {
            "action_required", "no_action", "needs_investigation"}
        assert {output["entity_resolution"]["status"] for output in outputs} == {
            "resolved", "ambiguous"}
        for output in outputs:
            assert all(ref.startswith("ev_demo_") for ref in output["evidence_refs"])
            if output["entity_resolution"]["status"] != "resolved":
                assert output["financial_resolution"]["recommended_refund_brl"] == 0
                assert output["payment_analysis"]["captured_total_brl"] is None
        assert len(gateway.calls) < 1200
    asyncio.run(check())


def test_demo_decisions_follow_received_money(tmp_path):
    class ChangedEvidence(DemoGateway):
        async def call(self, tool_name, *, case_id, **arguments):
            result = await super().call(tool_name, case_id=case_id, **arguments)
            if tool_name == "get_payment_timeline":
                result["data"]["captured_total_brl"] = 149.25
            if tool_name == "get_refund_timeline":
                result["data"]["refunded_total_brl"] = 19.15
            return result

    async def check():
        contracts = Contracts(ROOT / "contracts/schemas")
        case = demo_cases(1)[0]
        output = await Orchestrator(contracts, DemoModel()).solve(
            case, ChangedEvidence([case]), TraceWriter(tmp_path / "trace.jsonl", contracts))
        assert output["financial_resolution"]["recommended_refund_brl"] == 130.10
    asyncio.run(check())


def test_demo_decisions_follow_received_policy(tmp_path):
    class NoRefundPolicy(DemoGateway):
        async def call(self, tool_name, *, case_id, **arguments):
            result = await super().call(tool_name, case_id=case_id, **arguments)
            if tool_name == "get_policy":
                result["data"]["refundable_order_statuses"] = []
            return result

    async def check():
        contracts = Contracts(ROOT / "contracts/schemas")
        case = demo_cases(1)[0]
        output = await Orchestrator(contracts, DemoModel()).solve(
            case, NoRefundPolicy([case]), TraceWriter(tmp_path / "trace.jsonl", contracts))
        assert output["financial_resolution"]["recommended_refund_brl"] == 0
        assert output["assessment"]["case_status"] == "no_action"
    asyncio.run(check())


@pytest.mark.parametrize("count", [-1, True, 1001, 1.5])
def test_demo_count_is_bounded(count):
    with pytest.raises(ValueError, match="count"):
        demo_cases(count)


def test_demo_fixture_boundary():
    assert demo_cases(0) == []
    with pytest.raises(ValueError, match="demo"):
        DemoGateway([{"case_id": "REAL_CASE_001"}])
    with pytest.raises(ValueError, match="task"):
        asyncio.run(DemoModel().complete("write_refund", {}))
