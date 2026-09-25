"""Tool budgets must follow received identity evidence, never ID naming conventions."""
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from student_agent.contracts import Contracts
from student_agent.demo import DemoGateway, DemoModel, demo_cases
from student_agent.evidence import CaseEvidence
from student_agent.mcp_gateway import MCPToolError
from student_agent.trace import TraceWriter
from student_agent.workflow import Orchestrator

ROOT = Path(__file__).resolve().parents[1]


class Trace:
    def __init__(self):
        self.events = []

    def emit(self, **event):
        self.events.append(event)


class Model:
    settings = SimpleNamespace(decision_mode="plan")

    def __init__(self):
        self.payloads = []

    async def complete(self, task, payload):
        assert task == "resolve_entity"
        self.payloads.append(deepcopy(payload))
        return {"status": "ambiguous", "resolved_order_ids": [],
                "rejected_candidates": [], "confidence": 0.1}


class Gateway:
    def __init__(self, history, *, failed_orders=(), conflicting_orders=()):
        self.history = history
        self.failed_orders = set(failed_orders)
        self.conflicting_orders = set(conflicting_orders)
        self.calls = []

    async def call(self, tool, **arguments):
        self.calls.append((tool, arguments))
        if tool == "get_customer_history":
            if isinstance(self.history, Exception):
                raise self.history
            domain, data, suffix = "customer", self.history, "history"
        else:
            order = arguments["order_id"]
            if order in self.failed_orders:
                raise MCPToolError(tool, "unavailable")
            domain, suffix = "order", order
            data = {"order_id": order, "customer_unique_id": (
                "someone-else" if order in self.conflicting_orders else "customer-a")}
        return {"evidence_ref": f"ev_{suffix}", "domain": domain,
                "result_hash": "sha256:" + "a" * 64, "data": deepcopy(data)}


def case_and_history(order="order-a", count=3):
    case = {"case_id": "SYNTHETIC_CASE", "customer_unique_id_hint": "customer-a",
            "candidate_order_ids": [order, *[f"other-{n}" for n in range(count - 1)]],
            "customer_request": {"claimed_order_id": order}}
    history = {"customer_unique_id": "customer-a", "orders": [{"order_id": order}]}
    return case, history


def entity(case, gateway, model=None):
    model = model or Model()
    book = CaseEvidence(case["case_id"], gateway, Trace())
    solver = Orchestrator(Contracts(ROOT / "contracts/schemas"), model)
    resolution, candidates = asyncio.run(solver._entity(case, book))
    return resolution, candidates, book, model


@pytest.mark.parametrize("order", ["order-a", "candidate-001"])
@pytest.mark.parametrize("count", [2, 20])
def test_unique_history_link_fetches_only_that_order_without_rejecting_skipped_ids(order, count):
    case, history = case_and_history(order, count)
    # Repeated versions of an ID do not create two different identities.
    history["orders"].append({"order_id": order, "order_status": "canceled"})
    gateway = Gateway(history)
    resolution, candidates, book, model = entity(case, gateway)
    assert resolution["resolved_order_ids"] == [order]
    assert resolution["rejected_candidates"] == []
    assert candidates == set(case["candidate_order_ids"])
    assert [tool for tool, _ in gateway.calls] == ["get_customer_history", "get_order"]
    assert gateway.calls[1][1]["order_id"] == order
    assert all(args["case_id"] == case["case_id"] for _, args in gateway.calls)
    assert book.calls == 2 and len(book.ledger) == 2
    assert model.payloads == []


@pytest.mark.parametrize("problem", ["ambiguous", "empty", "wrong_customer", "failed",
                                     "missing_hint", "malformed", "outside_scope"])
def test_history_that_cannot_narrow_keeps_all_candidate_lookups(problem):
    case, history = case_and_history()
    if problem == "ambiguous":
        history["orders"].append({"order_id": "other-0"})
    elif problem == "empty":
        history["orders"] = []
    elif problem == "wrong_customer":
        history["customer_unique_id"] = "different-customer"
    elif problem == "failed":
        history = MCPToolError("get_customer_history", "unavailable")
    elif problem == "missing_hint":
        case.pop("customer_unique_id_hint")
    elif problem == "malformed":
        history["orders"] = "order-a"
    elif problem == "outside_scope":
        history["orders"] = [{"order_id": "unscoped-order"}]
    gateway = Gateway(history)
    _, _, book, _ = entity(case, gateway)
    fetched = [args["order_id"] for tool, args in gateway.calls if tool == "get_order"]
    assert fetched == case["candidate_order_ids"]
    assert sum(tool == "get_customer_history" for tool, _ in gateway.calls) == 1
    assert book.calls == len(case["candidate_order_ids"]) + 1


@pytest.mark.parametrize("problem", ["failed_order", "conflicting_customer"])
def test_unconfirmed_history_link_falls_back_without_duplicate_fetches(problem):
    case, history = case_and_history()
    gateway = Gateway(history, failed_orders=["order-a"] if problem == "failed_order" else [],
                      conflicting_orders=["order-a"] if problem == "conflicting_customer" else [])
    resolution, _, book, model = entity(case, gateway)
    assert resolution["status"] == "ambiguous"
    assert model.payloads and resolution["rejected_candidates"] == []
    assert len(gateway.calls) == 4
    assert [tool for tool, _ in gateway.calls][:2] == ["get_customer_history", "get_order"]
    assert sum(args.get("order_id") == "order-a" for _, args in gateway.calls) == 1
    if problem == "failed_order":
        assert len(book.failures) == 1
        assert model.payloads[0]["unreadable_candidates"] == ["order-a"]


def test_negative_cache_is_case_and_argument_scoped_even_for_concurrent_requests():
    async def check():
        gateway = Gateway({}, failed_orders=["order-a", "order-b"])
        book = CaseEvidence("CASE_A", gateway, Trace())
        results = await asyncio.gather(*[
            book.fetch("entity-agent", "get_order", order_id="order-a") for _ in range(4)
        ], return_exceptions=True)
        assert all(isinstance(error, MCPToolError) for error in results)
        assert book.calls == 1 and len(book.failures) == 1
        assert book.ledger == {} and book.cache == {}
        with pytest.raises(MCPToolError):
            await book.fetch("entity-agent", "get_order", order_id="order-b")
        other_case = CaseEvidence("CASE_B", gateway, Trace())
        with pytest.raises(MCPToolError):
            await other_case.fetch("entity-agent", "get_order", order_id="order-a")
        assert book.calls == 2 and other_case.calls == 1 and len(gateway.calls) == 3
        assert sum(event.get("decision_code") == "MCP_TOOL_EXECUTION_FAILURE"
                   for event in book.trace.events) == 2

    asyncio.run(check())


def test_transport_retry_still_recovers_before_caching_success():
    class RecoveringGateway(Gateway):
        async def call(self, tool, **arguments):
            if not self.calls:
                self.calls.append((tool, arguments))
                raise OSError("temporary transport failure")
            return await super().call(tool, **arguments)

    async def check():
        gateway = RecoveringGateway({})
        book = CaseEvidence("CASE_A", gateway, Trace())
        result = await book.fetch("entity-agent", "get_order", order_id="order-a")
        assert result == await book.fetch("entity-agent", "get_order", order_id="order-a")
        assert book.calls == 2 and len(book.failures) == 0

    asyncio.run(check())


class HistoryRowsGateway(DemoGateway):
    @staticmethod
    def _data(case, tool, arguments):
        data = DemoGateway._data(case, tool, arguments)
        if tool == "get_customer_history":
            data["orders"] = [{"order_id": order} for order in data["related_order_ids"]]
        return data


class PlanDemoModel(DemoModel):
    settings = SimpleNamespace(decision_mode="plan")


def test_optimized_full_case_preserves_all_specialist_and_policy_sources(tmp_path):
    case = demo_cases(1)[0]
    gateway = HistoryRowsGateway([case])
    contracts = Contracts(ROOT / "contracts/schemas")
    trace = TraceWriter(tmp_path / "trace.jsonl", contracts)
    evidence_path = tmp_path / "evidence.json"
    output = asyncio.run(Orchestrator(contracts, PlanDemoModel()).solve(
        case, gateway, trace, evidence_path
    ))
    assert output["financial_resolution"]["recommended_refund_brl"] == 100
    assert output["entity_resolution"]["rejected_candidates"] == []
    assert len(gateway.calls) == 9
    ledger = json.loads(evidence_path.read_text())
    assert {entry["domain"] for entry in ledger.values()} == {
        "customer", "order", "item", "product", "seller", "shipment", "payment", "refund", "policy"
    }
    assert set(output["evidence_refs"]) <= set(ledger)
    assert all(args["case_id"] == case["case_id"] for _, args in gateway.calls)
