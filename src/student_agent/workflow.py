from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from .arithmetic import verify_calculations
from .contracts import Contracts
from .evidence import CaseEvidence
from .mcp_gateway import EvidenceGateway, MCPToolError
from .model_adapter import DecisionModel, ModelSettings, OpenAICompatibleModel
from .specialists import investigate, values_for_key
from .trace import TraceWriter
from .verifier import verify_output


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Starter-compatible entry point; refuses live work without a configured model."""
    model = OpenAICompatibleModel(ModelSettings.load())
    solver = Orchestrator(trace.contracts, model)
    return await solver.solve(case, gateway, trace)


class Orchestrator:
    def __init__(
        self,
        contracts: Contracts,
        model: DecisionModel,
        critic: DecisionModel | None = None,
        repair_attempts: int = 1,
    ):
        self.contracts = contracts
        self.model = model
        self.critic = critic
        self.repair_attempts = min(max(repair_attempts, 0), 1)
        schema_root = contracts.root
        self.schemas = {
            p.name: json.loads(p.read_text(encoding="utf-8"))
            for p in schema_root.glob("*output-v2.schema.json")
        }

    async def solve(
        self, case: dict, gateway: Any, trace: TraceWriter, evidence_path: Path | None = None
    ) -> dict:
        case_id = case["case_id"]
        book = CaseEvidence(case_id, gateway, trace)
        trace.emit(case_id=case_id, event_type="case_received", actor="coordinator")
        available = getattr(gateway, "available_tools", None)
        if available is None:
            available = set(await gateway.list_tools())
        required = {
            "get_order",
            "get_customer_history",
            "get_order_items",
            "get_product_context",
            "get_sellers",
            "get_shipment_summary",
            "get_payment_timeline",
            "get_refund_timeline",
            "get_policy",
        }
        if not required <= set(available):
            raise ValueError("Required MCP tools absent from discovery")
        try:
            resolution, candidate_ids = await self._entity(case, book)
            order_ids = resolution["resolved_order_ids"]
            if len(order_ids) > 3:
                raise ValueError("Resolved order count exceeds bounded investigation budget")
            messages = await asyncio.gather(
                investigate(
                    book,
                    "order-product-agent",
                    order_ids,
                    ["get_order_items", "get_product_context", "get_sellers"],
                ),
                investigate(book, "shipment-agent", order_ids, ["get_shipment_summary"]),
                investigate(
                    book,
                    "payment-agent",
                    order_ids,
                    ["get_payment_timeline", "get_refund_timeline"],
                ),
            )
            trace.emit(
                case_id=case_id,
                event_type="task_assigned",
                actor="coordinator",
                target="policy-agent",
            )
            policy = await book.fetch(
                "policy-agent", "get_policy", policy_version=case["policy_version"]
            )
            book.handoff(
                "policy-agent",
                "conflict-resolver",
                [policy["evidence_ref"]],
                "AUTHORITATIVE_POLICY_RECEIVED",
            )
            trace.emit(
                case_id=case_id,
                event_type="task_assigned",
                actor="coordinator",
                target="conflict-resolver",
            )
            payload = {
                "case": case,
                "entity_resolution": resolution,
                "specialists": [message.payload() for message in messages],
                "evidence": book.ledger,
                "tool_failures": book.failures,
                "output_schemas": self.schemas,
                "required_response": {
                    "output": "Complete official L3B output JSON, with the supplied resolution.",
                    "calculations": [
                        {
                            "target": "/payment_analysis/captured_total_brl",
                            "operation": "sum",
                            "operands": [
                                {
                                    "evidence_ref": "existing ref",
                                    "pointer": "/path/to/number/in/data",
                                }
                            ],
                        }
                    ],
                },
                "rules": (
                    "Return exactly two top-level keys: output and calculations. "
                    "Place the official case object INSIDE output, never at the top level. "
                    "A tool_execution_failed event is a technical retrieval failure, NOT "
                    "a business refund_failed or payment_failed event. Do not infer "
                    "business failure or payment-provider responsibility from tool failure. "
                    "Evidence refs (ev_...) are NOT entity IDs: never put them in payment "
                    "references, shipment IDs or other affected entity lists. Unknown IDs "
                    "stay empty. Use actual IDs explicitly present in the source data. "
                    "Resolve conflicting timelines before counting duplicate items/payments; "
                    "do not sum different versions of the same record. Report source "
                    "conflicts explicitly, preserving unresolved conflicts when necessary. "
                    "Use exact recommended_action codes from policy, not free-text sentences. "
                    "Cite policy evidence whenever policy informs the decision. "
                    "Use MCP policy data for precedence and source conflicts. Do not treat claim "
                    "topics as answers. Include every input claim_id in claim_assessments. "
                    "Use only relevant received refs. Preserve entity_resolution exactly. "
                    "Bind every positive monetary value, including refund_lines, "
                    "with calculations. "
                    "Allowed operations: sum, subtract (two operands), remaining (first minus "
                    "the rest, floored at zero), minimum, zero (no operands). Each operand is "
                    "a JSON pointer into the data of a received evidence object. No literals. "
                    "Unknown data stays null. Ambiguous entities cannot receive a refund. "
                    "tool_failures and failed specialist facts describe unavailable tools, "
                    "not evidence. No conclusion may be supported by a failed call. "
                    "Missing refund history is unknown, never zero. If refund timeline failed, "
                    "refunded_total_brl and refundable_total_brl must be null. If payment "
                    "timeline failed, captured_total_brl and refundable_total_brl must be null. "
                    "Either financial failure requires recommended_refund_brl=0 and "
                    "case_status=needs_investigation. Other issues may still be established "
                    "by the evidence that was successfully received."
                ),
            }
            output = None
            for attempt in range(self.repair_attempts + 1):
                proposal = await self.model.complete("decide_policy", payload)
                try:
                    output = proposal["output"]
                    calculations = proposal["calculations"]
                    self.contracts.validate_output(output, case_id)
                    if output["entity_resolution"] != resolution:
                        raise ValueError("Policy model changed entity resolution")
                    for item in calculations:
                        if any(
                            o["evidence_ref"] not in output["evidence_refs"]
                            for o in item["operands"]
                        ):
                            raise ValueError("Arithmetic used evidence absent from output")
                    verify_calculations(output, calculations, book.ledger)
                    verify_output(case_id, output, book.ledger)
                    self._check_missing_financial_evidence(output, book.failures)
                    self._check_claims(case, output)
                    if not set(output["affected_entities"]["order_ids"]) <= candidate_ids:
                        raise ValueError("Output entity escapes candidate scope")
                    if resolution["status"] != "resolved":
                        if output["financial_resolution"]["recommended_refund_brl"] != 0:
                            raise ValueError("Ambiguous entity cannot receive a refund")
                        if output["assessment"]["case_status"] != "needs_investigation":
                            raise ValueError("Unresolved entity requires investigation")
                    break
                except (ValueError, KeyError, TypeError) as error:
                    trace.emit(
                        case_id=case_id,
                        event_type="handoff",
                        actor="verifier",
                        target="policy-agent",
                        decision_code="PROPOSAL_REJECTED",
                        attributes={"attempt": attempt + 1, "error_type": type(error).__name__},
                    )
                    if attempt == self.repair_attempts:
                        raise
                    payload["validation_feedback"] = str(error)[:800]
            assert output is not None
            book.handoff(
                "conflict-resolver", "verifier", output["evidence_refs"], "CONFLICTS_EXAMINED"
            )
            trace.emit(
                case_id=case_id,
                event_type="policy_decided",
                actor="policy-agent",
                decision_code=output["assessment"]["primary_issue"].upper(),
                evidence_refs=output["evidence_refs"],
            )
            if self.critic:
                trace.emit(
                    case_id=case_id,
                    event_type="task_assigned",
                    actor="coordinator",
                    target="critic-agent",
                )
                review = await self.critic.complete(
                    "review_decision",
                    {
                        "case": case,
                        "output": output,
                        "evidence": book.ledger,
                        "tool_failures": book.failures,
                        "response": {"approved": "boolean", "issues": "list"},
                    },
                )
                if review.get("approved") is not True:
                    raise ValueError("Independent critic rejected the decision")
                book.handoff("critic-agent", "verifier", output["evidence_refs"], "CRITIC_APPROVED")
            trace.emit(
                case_id=case_id,
                event_type="verification_completed",
                actor="verifier",
                evidence_refs=output["evidence_refs"],
                attributes={
                    "schema": True,
                    "scope": True,
                    "arithmetic": True,
                    "tool_calls": book.calls,
                },
            )
            trace.emit(case_id=case_id, event_type="case_finalized", actor="coordinator")
            return output
        finally:
            if evidence_path is not None:
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text(
                    json.dumps(book.ledger, ensure_ascii=False, indent=2), encoding="utf-8"
                )

    async def _entity(self, case: dict, book: CaseEvidence) -> tuple[dict, set[str]]:
        candidates = list(
            dict.fromkeys(
                [
                    *case.get("candidate_order_ids", []),
                    *(
                        [case["customer_request"]["claimed_order_id"]]
                        if case.get("customer_request", {}).get("claimed_order_id")
                        else []
                    ),
                ]
            )
        )
        if not candidates or len(candidates) > 20:
            raise ValueError("Need 1-20 explicit scoped candidate order IDs")
        book.trace.emit(
            case_id=book.case_id,
            event_type="task_assigned",
            actor="coordinator",
            target="entity-agent",
            attributes={"candidate_count": len(candidates)},
        )
        orders = {}
        unreadable = []
        for order in candidates:
            try:
                orders[order] = await book.fetch("entity-agent", "get_order", order_id=order)
            except MCPToolError:
                unreadable.append(order)
        if not orders:
            raise ValueError("No readable candidate order evidence; investigation cannot proceed")
        hint = case.get("customer_unique_id_hint")
        known_customers = set().union(
            *(values_for_key(e["data"], "customer_unique_id") for e in orders.values())
        )
        customer_id = hint or (next(iter(known_customers)) if len(known_customers) == 1 else None)
        if customer_id:
            # Failure stays explicit in the payload, never becomes empty history.
            with suppress(MCPToolError):
                await book.fetch(
                    "entity-agent", "get_customer_history", customer_unique_id=customer_id
                )
        payload = {
            "case": case,
            "candidates": orders,
            "unreadable_candidates": unreadable,
            "allowed_ids_in_either_result_list": sorted(orders),
            "tool_failures": book.failures,
            "evidence": book.ledger,
            "required_response": self.schemas["l3b-output-v2.schema.json"]["properties"][
                "entity_resolution"
            ],
            "rules": "Resolve only from received evidence; not from the alleged order alone. "
            "Return status, resolved_order_ids, rejected_candidates, confidence. "
            "Use ambiguous or not_found when justified; never invent an ID. "
            "Unreadable candidates are unknown, not disproved. Do not select or reject "
            "them based on failed calls. Missing customer history is unknown, not empty. "
            "The rejected_candidates list can be empty; do not force every input "
            "candidate into a result list. Omit every unreadable candidate from both lists.",
        }
        witnessed = set().union(*(values_for_key(e["data"], "order_id") for e in orders.values()))
        for attempt in range(self.repair_attempts + 1):
            resolution = await self.model.complete("resolve_entity", payload)
            try:
                self._check_resolution(resolution, set(candidates), set(unreadable), witnessed)
                break
            except (ValueError, TypeError, KeyError) as error:
                book.trace.emit(
                    case_id=book.case_id,
                    event_type="handoff",
                    actor="verifier",
                    target="entity-agent",
                    decision_code="ENTITY_RESOLUTION_REJECTED",
                    attributes={"attempt": attempt + 1, "error_type": type(error).__name__},
                )
                if attempt == self.repair_attempts:
                    raise
                payload["validation_feedback"] = str(error)[:800]
                payload["previous_response"] = resolution
        book.handoff("entity-agent", "coordinator", list(book.ledger), "ENTITY_RESOLVED")
        return resolution, set(candidates)

    @staticmethod
    def _check_resolution(
        resolution: dict, candidates: set[str], unreadable: set[str], witnessed: set[str]
    ) -> None:
        required = {"status", "resolved_order_ids", "rejected_candidates", "confidence"}
        if set(resolution) != required or resolution["status"] not in {
            "resolved",
            "ambiguous",
            "not_found",
        }:
            raise ValueError("Invalid entity resolution response")
        selected = set(resolution["resolved_order_ids"])
        rejected = set(resolution["rejected_candidates"])
        if not (selected | rejected) <= candidates or selected & rejected:
            raise ValueError("Model resolution escaped candidate scope")
        if (selected | rejected) & unreadable:
            raise ValueError("Unreadable candidate cannot be resolved or rejected without evidence")
        if resolution["status"] == "resolved" and not selected:
            raise ValueError("Resolved entity response is empty")
        if not selected <= witnessed:
            raise ValueError("Resolved candidate has no matching received order evidence")

    @staticmethod
    def _check_missing_financial_evidence(output: dict, failures: list[dict]) -> None:
        """Missing financial sources cannot silently become a zero refund history."""
        orders = set(output["entity_resolution"]["resolved_order_ids"])
        failed_tools = {
            failure["tool_name"]
            for failure in failures
            if failure["arguments"].get("order_id") in orders
        }
        missing_fields = set()
        if "get_refund_timeline" in failed_tools:
            missing_fields.update({"refunded_total_brl", "refundable_total_brl"})
        if "get_payment_timeline" in failed_tools:
            missing_fields.update({"captured_total_brl", "refundable_total_brl"})
        if not missing_fields:
            return
        if any(output["payment_analysis"][field] is not None for field in missing_fields):
            raise ValueError("Missing payment/refund evidence requires null financial totals")
        if output["financial_resolution"]["recommended_refund_brl"] != 0:
            raise ValueError("Missing payment/refund evidence cannot authorize a refund")
        if output["assessment"]["case_status"] != "needs_investigation":
            raise ValueError("Missing payment/refund evidence requires investigation")

    @staticmethod
    def _check_claims(case: dict, output: dict) -> None:
        expected = {c["claim_id"] for c in case.get("customer_request", {}).get("claims", [])}
        actual = [c["claim_id"] for c in output.get("claim_assessments", [])]
        if set(actual) != expected or len(actual) != len(set(actual)):
            raise ValueError("Claim assessments do not exactly match input claims")
