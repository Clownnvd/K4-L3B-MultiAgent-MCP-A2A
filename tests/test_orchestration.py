import asyncio
import json
from pathlib import Path

import pytest

from student_agent.arithmetic import verify_calculations
from student_agent.batch import execute_batch
from student_agent.contracts import Contracts
from student_agent.demo import DemoGateway, DemoModel, demo_cases
from student_agent.model_adapter import ModelSettings
from student_agent.run_package import package_run
from student_agent.trace import TraceWriter
from student_agent.workflow import Orchestrator

ROOT = Path(__file__).resolve().parents[1]


def test_policy_repair_receives_previous_candidate_and_validation_feedback(tmp_path):
    class RepairModel(DemoModel):
        policy_calls = 0

        async def complete(self, task, payload):
            result = await super().complete(task, payload)
            if task == "decide_policy":
                self.policy_calls += 1
                if self.policy_calls == 1:
                    result["output"]["financial_resolution"]["recommended_refund_brl"] = 101
                else:
                    assert payload["validation_feedback"]
                    prior = payload["previous_response"]["output"]["financial_resolution"]
                    assert prior["recommended_refund_brl"] == 101
            return result

    model = RepairModel()
    output, _ = run_one(tmp_path, model=model)
    assert model.policy_calls == 2
    assert output["financial_resolution"]["recommended_refund_brl"] == 100


def run_one(tmp_path, *, model=None, critic=None):
    contracts = Contracts(ROOT / "contracts/schemas")
    case = demo_cases(1)[0]
    gateway = DemoGateway([case])
    trace = TraceWriter(tmp_path / "trace.jsonl", contracts)
    solver = Orchestrator(contracts, model or DemoModel(), critic=critic)
    return asyncio.run(solver.solve(case, gateway, trace)), gateway


def test_end_to_end_real_orchestrator_with_synthetic_evidence(tmp_path):
    output, gateway = run_one(tmp_path)
    assert output["financial_resolution"]["recommended_refund_brl"] == 100
    assert output["entity_resolution"]["rejected_candidates"] == ["wrong-order-0001"]
    assert all(call[1]["case_id"] == "DEMO_CASE_001" for call in gateway.calls)
    events = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    actors = {e["actor"] for e in events}
    assert {"entity-agent", "order-product-agent", "payment-agent", "shipment-agent",
            "policy-agent", "conflict-resolver", "verifier"} <= actors
    assert events[0]["event_type"] == "case_received"
    assert events[-1]["event_type"] == "case_finalized"
    consumed = {r for e in events if e["event_type"] == "tool_result_consumed"
                for r in e["evidence_refs"]}
    assert set(output["evidence_refs"]) <= consumed


def test_model_cannot_resolve_a_fabricated_order(tmp_path):
    class BadModel(DemoModel):
        async def complete(self, task, payload):
            result = await super().complete(task, payload)
            if task == "resolve_entity":
                result["resolved_order_ids"] = ["invented-order"]
            return result
    with pytest.raises(ValueError, match="candidate"):
        run_one(tmp_path, model=BadModel())


def test_critic_rejection_is_not_overwritten(tmp_path):
    class Critic:
        async def complete(self, task, payload):
            return {"approved": False, "issues": ["evidence incomplete"]}
    with pytest.raises(ValueError, match="critic"):
        run_one(tmp_path, critic=Critic())


def test_money_must_be_computed_from_evidence():
    ledger = {"ev_demo_abcdefghijklmnopqrst": {"data": {"paid": "10.10", "returned": 2}}}
    output = {"financial_resolution": {"recommended_refund_brl": 8.1, "refund_lines": []},
              "payment_analysis": {"captured_total_brl": None, "refunded_total_brl": None,
                                   "refundable_total_brl": None}}
    calculations = [{"target": "/financial_resolution/recommended_refund_brl",
                     "operation": "subtract", "operands": [
                         {"evidence_ref": next(iter(ledger)), "pointer": "/paid"},
                         {"evidence_ref": next(iter(ledger)), "pointer": "/returned"}]}]
    verify_calculations(output, calculations, ledger)
    output["financial_resolution"]["recommended_refund_brl"] = 9
    with pytest.raises(ValueError, match="arithmetic"):
        verify_calculations(output, calculations, ledger)


def test_model_not_configured_and_unknown_checkpoint_rejected(monkeypatch):
    monkeypatch.setenv("MODEL_CHECKPOINT", "")
    with pytest.raises(ValueError, match="MODEL_CHECKPOINT"):
        ModelSettings.load()
    monkeypatch.setenv("MODEL_CHECKPOINT", "arbitrary-14B-quantized")
    with pytest.raises(ValueError, match="approved"):
        ModelSettings.load()


def test_hundred_case_demo_is_complete_but_cannot_be_submitted(tmp_path):
    cases = demo_cases(100)
    contracts = Contracts(ROOT / "contracts/schemas")
    gateway = DemoGateway(cases)
    solver = Orchestrator(contracts, DemoModel())
    receipt = asyncio.run(execute_batch(cases, gateway, solver, tmp_path, mode="demo",
                                       concurrency=4, case_set_version="demo-v1"))
    assert receipt["completed"] == 100
    assert receipt["failed"] == 0
    assert len(list((tmp_path / "outputs").glob("*.json"))) == 100
    with pytest.raises(ValueError, match="demo"):
        package_run(tmp_path, tmp_path / "submission.zip", contracts)
