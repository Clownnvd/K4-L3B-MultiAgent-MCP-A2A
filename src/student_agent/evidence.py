"""Case-scoped evidence ledger, tool permissions and bounded transport retries."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

import httpx2

from .mcp_gateway import MCPToolError

PERMISSIONS = {
    "entity-agent": {"get_order", "get_customer_history"},
    "order-product-agent": {"get_order_items", "get_product_context", "get_sellers"},
    "shipment-agent": {"get_shipment_summary"},
    "payment-agent": {"get_order_payments", "get_payment_timeline", "get_refund_timeline"},
    "policy-agent": {"get_policy"},
}


class CaseEvidence:
    def __init__(self, case_id: str, gateway: Any, trace: Any, timeout: float = 35) -> None:
        self.case_id = case_id
        self.gateway = gateway
        self.trace = trace
        self.timeout = timeout
        self.ledger: dict[str, dict[str, Any]] = {}
        self.failures: list[dict[str, Any]] = []
        self.cache: dict[str, dict[str, Any]] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.calls = 0

    async def fetch(self, actor: str, tool: str, **arguments: str) -> dict[str, Any]:
        if tool not in PERMISSIONS.get(actor, set()):
            raise ValueError(f"Tool permission denied: {actor} / {tool}")
        if "case_id" in arguments:
            raise ValueError("Case scope cannot be overridden")
        key = json.dumps([tool, arguments], sort_keys=True, separators=(",", ":"))
        async with self.locks.setdefault(key, asyncio.Lock()):
            if key in self.cache:
                return deepcopy(self.cache[key])
            for attempt in range(2):
                self.calls += 1
                try:
                    result = await asyncio.wait_for(
                        self.gateway.call(tool, case_id=self.case_id, **arguments),
                        timeout=self.timeout,
                    )
                    break
                except MCPToolError:
                    # Error metadata is not evidence and carries no evidence reference.
                    self.failures.append({
                        "actor": actor, "tool_name": tool, "arguments": dict(arguments),
                        "status": "tool_execution_failed",
                    })
                    self.trace.emit(
                        case_id=self.case_id, event_type="handoff", actor=actor,
                        target="coordinator", decision_code="MCP_TOOL_EXECUTION_FAILURE",
                        tool_name=tool, attributes={"error_type": "MCPToolError", **arguments},
                    )
                    raise
                except (TimeoutError, OSError, httpx2.TransportError) as error:
                    self.trace.emit(
                        case_id=self.case_id, event_type="handoff", actor=actor,
                        target="coordinator", decision_code="MCP_TRANSPORT_FAILURE",
                        tool_name=tool,
                        attributes={"attempt": attempt + 1, "error_type": type(error).__name__},
                    )
                    if attempt:
                        raise
                    await asyncio.sleep(0.25)
            ref = result["evidence_ref"]
            if ref in self.ledger and self.ledger[ref] != result:
                raise ValueError("An evidence reference changed its contents")
            self.ledger[ref] = deepcopy(result)
            self.cache[key] = deepcopy(result)
            self.trace.emit(
                case_id=self.case_id, event_type="tool_result_consumed", actor=actor,
                tool_name=tool, evidence_refs=[ref],
                attributes={"domain": result["domain"], "result_hash": result["result_hash"]},
            )
            return deepcopy(result)

    def handoff(self, actor: str, target: str, refs: list[str], decision_code: str) -> None:
        if any(ref not in self.ledger for ref in refs):
            raise ValueError("Handoff references evidence not consumed in this case")
        self.trace.emit(
            case_id=self.case_id, event_type="handoff", actor=actor, target=target,
            evidence_refs=list(dict.fromkeys(refs)), decision_code=decision_code,
        )
