"""Bounded, synthetic fixtures for exercising orchestration without network access.

DemoModel is a deterministic fixture interpreter, NOT an LLM or a competition
solver. It only understands the five scenarios generated here. Its monetary
proposals are still derived from received evidence and checked by the real
orchestrator. These cases and references must never be submitted as live work.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from .arithmetic import numeric
from .verifier import verify_output

_SCENARIOS = ("canceled", "partial_refund", "refunded", "delivered", "ambiguous")
_DOMAINS = {
    "get_order": "order",
    "get_customer_history": "customer",
    "get_order_items": "item",
    "get_product_context": "product",
    "get_sellers": "seller",
    "get_shipment_summary": "shipment",
    "get_payment_timeline": "payment",
    "get_refund_timeline": "refund",
    "get_policy": "policy",
}


def demo_cases(count: int = 100) -> list[dict[str, Any]]:
    """Generate up to 1,000 obviously synthetic cases; the first refund is BRL 100."""
    if type(count) is not int or not 0 <= count <= 1000:
        raise ValueError("Demo count must be an integer between 0 and 1000")
    cases = []
    for index in range(1, count + 1):
        order = f"demo-order-{index:04d}"
        wrong = f"wrong-order-{index:04d}"
        cases.append({
            "case_id": f"DEMO_CASE_{index:03d}",
            "policy_version": "demo-policy-v1",
            "customer_unique_id_hint": f"demo-customer-{index:04d}",
            "candidate_order_ids": [order, wrong],
            "customer_request": {
                "claimed_order_id": wrong,
                "claims": [{"claim_id": f"demo-claim-{index:04d}",
                            "topic": "refund_due"}],
            },
            "demo_scenario": _SCENARIOS[(index - 1) % len(_SCENARIOS)],
        })
    return cases


class DemoGateway:
    """Read-only in-memory gateway, with case scope and MCP-shaped responses."""

    available_tools = frozenset(_DOMAINS)

    def __init__(self, cases: list[dict[str, Any]]) -> None:
        self.cases = {}
        self.calls: list[tuple[str, dict[str, str]]] = []
        for case in cases:
            case_id = case.get("case_id", "")
            if not re.fullmatch(r"DEMO_CASE_\d{3,4}", case_id):
                raise ValueError("Gateway accepts synthetic demo cases only")
            index = int(case_id.rsplit("_", 1)[1])
            if not 1 <= index <= 1000:
                raise ValueError("Invalid demo case index")
            expected = [f"demo-order-{index:04d}", f"wrong-order-{index:04d}"]
            if (case.get("candidate_order_ids") != expected
                    or case.get("customer_unique_id_hint") != f"demo-customer-{index:04d}"
                    or case.get("policy_version") != "demo-policy-v1"
                    or case.get("demo_scenario") not in _SCENARIOS):
                raise ValueError("Invalid synthetic demo fixture")
            if case_id in self.cases:
                raise ValueError("Duplicate demo case ID")
            self.cases[case_id] = deepcopy(case)

    async def list_tools(self) -> list[str]:
        return sorted(self.available_tools)

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        if tool_name not in self.available_tools:
            raise ValueError("Unknown demo tool")
        if case_id not in self.cases:
            raise ValueError("Unknown demo case")
        case = self.cases[case_id]
        if tool_name == "get_policy":
            expected = {"policy_version": case["policy_version"]}
        elif tool_name == "get_customer_history":
            expected = {"customer_unique_id": case["customer_unique_id_hint"]}
        else:
            if arguments.get("order_id") not in case["candidate_order_ids"]:
                raise ValueError("Order escapes demo case scope")
            expected = {"order_id": arguments["order_id"]}
        if arguments != expected:
            raise ValueError("Arguments escape demo tool scope")
        self.calls.append((tool_name, {"case_id": case_id, **arguments}))
        data = self._data(case, tool_name, arguments)
        canonical = json.dumps({"case_id": case_id, "tool": tool_name,
                                "arguments": arguments, "data": data},
                               sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        return {"schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": "ev_demo_" + digest,
                "result_hash": "sha256:" + digest,
                "domain": _DOMAINS[tool_name], "data": data,
                "warnings": ["SYNTHETIC_DEMO_ONLY"]}

    @staticmethod
    def _data(case: dict, tool: str, arguments: dict) -> dict:
        index = int(case["case_id"].rsplit("_", 1)[1])
        scenario = case["demo_scenario"]
        order = arguments.get("order_id")
        customer = case["customer_unique_id_hint"]
        right_order, wrong_order = case["candidate_order_ids"]
        captured = 100 + (index - 1) % 17
        refunded = captured if scenario == "refunded" else 25 if scenario == "partial_refund" else 0
        base = {"synthetic_demo": True}
        if order:
            base["order_id"] = order
        if tool == "get_order":
            base.update({"customer_unique_id": customer if (
                order == right_order or scenario == "ambiguous") else f"demo-other-{index:04d}",
                "order_status": "delivered" if scenario == "delivered" else "canceled"})
        elif tool == "get_customer_history":
            base.update({"customer_unique_id": customer,
                         "related_order_ids": [right_order, wrong_order] if (
                             scenario == "ambiguous") else [right_order]})
        elif tool == "get_order_items":
            base["items"] = [{"item_id": f"demo-item-{index:04d}",
                              "seller_id": f"demo-seller-{index:04d}"}]
        elif tool == "get_product_context":
            base["products"] = [{"product_id": f"demo-product-{index:04d}",
                                 "name": "Synthetic demo product"}]
        elif tool == "get_sellers":
            base["seller_ids"] = [f"demo-seller-{index:04d}"]
        elif tool == "get_shipment_summary":
            base.update({"shipment_ids": [f"demo-shipment-{index:04d}"] if (
                scenario == "delivered") else [],
                "verdict": "on_time" if scenario == "delivered" else "insufficient_evidence",
                "timeline_complete": scenario == "delivered"})
        elif tool == "get_payment_timeline":
            base.update({"captured_total_brl": captured,
                         "payment_references": [f"demo-payment-{index:04d}"]})
        elif tool == "get_refund_timeline":
            base["refunded_total_brl"] = refunded
        elif tool == "get_policy":
            base.update({"policy_version": case["policy_version"],
                         "refundable_order_statuses": ["canceled", "unavailable"],
                         "deduct_completed_refunds": True})
        return base


class DemoModel:
    """Deterministic test double over demo evidence; never makes model/API calls."""

    async def complete(self, task: str, payload: dict[str, Any]) -> dict[str, Any]:
        if task not in {"resolve_entity", "decide_policy", "review_decision"}:
            raise ValueError("Unsupported demo model task")
        case = payload["case"]
        ledger = payload["evidence"]
        if not case.get("case_id", "").startswith("DEMO_CASE_") or not ledger or any(
            not ref.startswith("ev_demo_")
            or entry.get("data", {}).get("synthetic_demo") is not True
            for ref, entry in ledger.items()
        ):
            raise ValueError("Demo model only interprets synthetic demo evidence")
        if task == "resolve_entity":
            return self._resolve(payload)
        if task == "review_decision":
            try:
                verify_output(case["case_id"], payload["output"], ledger)
            except (ValueError, KeyError, TypeError):
                return {"approved": False, "issues": ["DEMO_STRUCTURAL_CHECK_FAILED"]}
            return {"approved": True, "issues": []}
        return self._decide(payload)

    @staticmethod
    def _resolve(payload: dict) -> dict:
        hint = payload["case"].get("customer_unique_id_hint")
        candidates = payload["candidates"]
        matching = [order for order, entry in candidates.items()
                    if entry["data"].get("order_id") == order
                    and entry["data"].get("customer_unique_id") == hint]
        resolved = matching if len(matching) == 1 else []
        return {"status": "resolved" if resolved else "ambiguous" if matching else "not_found",
                "resolved_order_ids": resolved,
                "rejected_candidates": [order for order in candidates if order not in matching],
                "confidence": 1 if resolved else 0}

    @staticmethod
    def _decide(payload: dict) -> dict:
        case, ledger = payload["case"], payload["evidence"]
        resolution = deepcopy(payload["entity_resolution"])
        orders = resolution["resolved_order_ids"]
        refs = list(ledger)
        output = {
            "schema_version": "day09-l3b-output-v2", "case_id": case["case_id"],
            "assessment": {"primary_issue": "insufficient_evidence", "secondary_issues": [],
                           "case_status": "needs_investigation", "confidence": 0},
            "affected_entities": {"order_ids": orders, "item_ids": [], "seller_ids": [],
                                  "payment_references": [], "shipment_ids": []},
            "entity_resolution": resolution,
            "customer_context": {"customer_unique_id": None, "related_order_ids": []},
            "shipment_analysis": {"verdict": "insufficient_evidence", "late_seller_ids": [],
                                  "timeline_complete": False},
            "payment_analysis": {"verdict": "insufficient_evidence", "captured_total_brl": None,
                                 "refunded_total_brl": None, "refundable_total_brl": None},
            "root_cause_analysis": {"ranked_causes": [], "responsible_parties": []},
            "claim_assessments": [], "evidence_refs": refs, "data_conflicts": [],
            "financial_resolution": {"currency": "BRL", "recommended_refund_brl": 0,
                                     "refund_lines": []},
            "resolution_actions": ["DEMO_INVESTIGATE_ENTITY"],
        }
        calculations = []
        verdict = "insufficient_evidence"

        def find(domain: str, order: str | None = None) -> tuple[str, dict]:
            matches = [(ref, entry["data"]) for ref, entry in ledger.items()
                       if entry["domain"] == domain and (
                           order is None or entry["data"].get("order_id") == order)]
            if len(matches) != 1:
                raise ValueError(f"Expected one received demo {domain} evidence object")
            return matches[0]

        _, history = find("customer")
        output["customer_context"] = {"customer_unique_id": history["customer_unique_id"],
                                      "related_order_ids": history["related_order_ids"]}
        if resolution["status"] == "resolved":
            if len(orders) != 1:
                raise ValueError("Demo model supports exactly one resolved order")
            order = orders[0]
            _, order_data = find("order", order)
            _, policy = find("policy")
            _, items = find("item", order)
            _, sellers = find("seller", order)
            _, shipment = find("shipment", order)
            payment_ref, payment = find("payment", order)
            refund_ref, refund = find("refund", order)
            if policy.get("deduct_completed_refunds") is not True:
                raise ValueError("Unsupported synthetic refund policy")
            captured = numeric(payment["captured_total_brl"])
            refunded = numeric(refund["refunded_total_brl"])
            available = max(numeric(0), captured - refunded)
            eligible = order_data["order_status"] in policy["refundable_order_statuses"]
            recommended = available if eligible else numeric(0)
            verdict = "supported" if recommended else "unsupported"
            issue = ("canceled_order_paid" if order_data["order_status"] == "canceled"
                     else "unavailable_order_paid") if eligible else "unsupported_claim"
            output["assessment"].update({"primary_issue": issue, "confidence": 1,
                "case_status": "action_required" if recommended else "no_action"})
            output["affected_entities"].update({
                "item_ids": [item["item_id"] for item in items["items"]],
                "seller_ids": sellers["seller_ids"],
                "payment_references": payment["payment_references"],
                "shipment_ids": shipment["shipment_ids"],
            })
            output["shipment_analysis"].update({"verdict": shipment["verdict"],
                "timeline_complete": shipment["timeline_complete"]})
            output["payment_analysis"].update({
                "verdict": "refunded" if refunded >= captured and captured > 0 else "reconciled",
                "captured_total_brl": float(captured), "refunded_total_brl": float(refunded),
                "refundable_total_brl": float(available),
            })
            output["financial_resolution"]["recommended_refund_brl"] = float(recommended)
            output["resolution_actions"] = ["DEMO_RECOMMEND_REFUND" if recommended
                                             else "DEMO_NO_ACTION"]
            captured_source = {"evidence_ref": payment_ref, "pointer": "/captured_total_brl"}
            refunded_source = {"evidence_ref": refund_ref, "pointer": "/refunded_total_brl"}
            for field, operation, operands in [
                ("captured_total_brl", "sum", [captured_source]),
                ("refunded_total_brl", "sum", [refunded_source]),
                ("refundable_total_brl", "remaining", [captured_source, refunded_source]),
            ]:
                calculations.append({"target": "/payment_analysis/" + field,
                                     "operation": operation, "operands": operands})
            if recommended:
                output["financial_resolution"]["refund_lines"] = [{
                    "reason_code": "DEMO_CANCELED_OR_UNAVAILABLE_BALANCE",
                    "amount_brl": float(recommended), "entity_id": order}]
                for target in ["/financial_resolution/recommended_refund_brl",
                               "/financial_resolution/refund_lines/0/amount_brl"]:
                    calculations.append({"target": target, "operation": "remaining",
                                         "operands": [captured_source, refunded_source]})
        for claim in case.get("customer_request", {}).get("claims", []):
            claim_verdict = (verdict if claim.get("topic") == "refund_due"
                             else "insufficient_evidence")
            output["claim_assessments"].append({
                "claim_id": claim["claim_id"], "verdict": claim_verdict,
                "confidence": 0 if claim_verdict == "insufficient_evidence" else 1,
                "evidence_refs": refs,
            })
        return {"output": output, "calculations": calculations}
