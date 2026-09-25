import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from student_agent.contracts import Contracts
from student_agent.demo import DemoGateway, DemoModel, demo_cases
from student_agent.evidence import CaseEvidence
from student_agent.mcp_gateway import EvidenceGateway, MCPToolError
from student_agent.specialists import investigate
from student_agent.trace import TraceWriter
from student_agent.workflow import Orchestrator

ROOT = Path(__file__).resolve().parents[1]


def setup_trace(tmp_path):
    contracts = Contracts(ROOT / "contracts/schemas")
    return contracts, TraceWriter(tmp_path / "trace.jsonl", contracts)


def test_server_error_is_typed_but_invalid_contract_is_not():
    class Session:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(text="tool failed")])

    contracts = Contracts(ROOT / "contracts/schemas")
    with pytest.raises(MCPToolError, match="tool failed") as error:
        asyncio.run(EvidenceGateway(Session(), contracts).call("get_order", case_id="CASE_001"))
    assert error.value.tool_name == "get_order"

    class InvalidSession:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(is_error=False, structured_content={}, content=[])

    with pytest.raises(ValueError):
        asyncio.run(EvidenceGateway(InvalidSession(), contracts).call(
            "get_order", case_id="CASE_001"))


def test_optional_failure_has_explicit_fact_and_no_evidence(tmp_path):
    class FailingGateway:
        async def call(self, tool, **arguments):
            raise MCPToolError(tool, "server unavailable")

    async def check():
        _, trace = setup_trace(tmp_path)
        book = CaseEvidence("CASE_001", FailingGateway(), trace)
        message = await investigate(book, "payment-agent", ["order-1"], ["get_refund_timeline"])
        assert book.ledger == {}
        assert message.evidence_refs == ()
        assert message.facts["order-1"]["get_refund_timeline"]["status"] == "tool_execution_failed"
        assert book.failures[0]["arguments"] == {"order_id": "order-1"}
        events = [json.loads(line) for line in trace.path.read_text().splitlines()]
        assert any(event.get("decision_code") == "MCP_TOOL_EXECUTION_FAILURE" for event in events)
        assert not any(event["event_type"] == "tool_result_consumed" for event in events)
    asyncio.run(check())


@pytest.mark.parametrize("error", [ValueError("bad schema or hash"), OSError("transport")])
def test_specialists_do_not_swallow_non_tool_errors(tmp_path, error):
    class BadGateway:
        async def call(self, tool, **arguments):
            raise error

    async def check():
        _, trace = setup_trace(tmp_path)
        book = CaseEvidence("CASE_001", BadGateway(), trace)
        with pytest.raises(type(error), match=str(error)):
            await investigate(book, "payment-agent", ["order-1"], ["get_refund_timeline"])
        assert book.ledger == {}
    asyncio.run(check())


def test_unreadable_candidate_is_not_rejected_as_wrong(tmp_path):
    class MissingCandidate(DemoGateway):
        async def call(self, tool_name, *, case_id, **arguments):
            if tool_name == "get_order" and arguments["order_id"].startswith("wrong-"):
                raise MCPToolError(tool_name, "execution error")
            return await super().call(tool_name, case_id=case_id, **arguments)

    class InspectModel(DemoModel):
        async def complete(self, task, payload):
            if task == "resolve_entity":
                assert payload["unreadable_candidates"] == ["wrong-order-0001"]
                assert "wrong-order-0001" not in payload["candidates"]
            result = await super().complete(task, payload)
            return result

    async def check():
        contracts, trace = setup_trace(tmp_path)
        case = demo_cases(1)[0]
        output = await Orchestrator(contracts, InspectModel()).solve(
            case, MissingCandidate([case]), trace)
        assert output["entity_resolution"]["rejected_candidates"] == []
    asyncio.run(check())


def test_all_candidate_failures_abort_without_model_or_evidence(tmp_path):
    class MissingOrders(DemoGateway):
        async def call(self, tool_name, *, case_id, **arguments):
            raise MCPToolError(tool_name, "execution error")

    class NoModel:
        async def complete(self, task, payload):
            pytest.fail("All unreadable candidates must abort before inference")

    async def check():
        contracts, trace = setup_trace(tmp_path)
        case = demo_cases(1)[0]
        evidence_path = tmp_path / "evidence.json"
        with pytest.raises(ValueError, match="readable"):
            await Orchestrator(contracts, NoModel()).solve(
                case, MissingOrders([case]), trace, evidence_path)
        assert json.loads(evidence_path.read_text()) == {}
    asyncio.run(check())


@pytest.mark.parametrize("invent_zero", [False, True])
def test_failed_refund_cannot_become_zero_history(tmp_path, invent_zero):
    class MissingRefund(DemoGateway):
        async def call(self, tool_name, *, case_id, **arguments):
            if tool_name == "get_refund_timeline":
                raise MCPToolError(tool_name, "execution error")
            return await super().call(tool_name, case_id=case_id, **arguments)

    async def check():
        contracts, trace = setup_trace(tmp_path)
        case = demo_cases(1)[0]
        baseline = await Orchestrator(contracts, DemoModel()).solve(
            case, DemoGateway([case]), trace)

        class ConservativeModel(DemoModel):
            async def complete(self, task, payload):
                if task != "decide_policy":
                    return await super().complete(task, payload)
                assert payload["tool_failures"][0]["tool_name"] == "get_refund_timeline"
                assert "null" in payload["rules"]
                output = deepcopy(baseline)
                refs = list(payload["evidence"])
                output["evidence_refs"] = refs
                output["assessment"]["case_status"] = "needs_investigation"
                output["payment_analysis"].update({"refunded_total_brl": 0 if invent_zero else None,
                    "refundable_total_brl": None, "verdict": "insufficient_evidence"})
                output["financial_resolution"].update({"recommended_refund_brl": 0,
                                                       "refund_lines": []})
                for claim in output["claim_assessments"]:
                    claim.update({"verdict": "insufficient_evidence", "evidence_refs": refs})
                payment_ref = next(ref for ref, value in payload["evidence"].items()
                                   if value["domain"] == "payment")
                return {"output": output, "calculations": [{
                    "target": "/payment_analysis/captured_total_brl", "operation": "sum",
                    "operands": [{"evidence_ref": payment_ref, "pointer": "/captured_total_brl"}]}]}

        solver = Orchestrator(contracts, ConservativeModel())
        if invent_zero:
            with pytest.raises(ValueError, match="refund"):
                await solver.solve(case, MissingRefund([case]), trace)
        else:
            output = await solver.solve(case, MissingRefund([case]), trace)
            assert output["payment_analysis"]["refunded_total_brl"] is None
    asyncio.run(check())
