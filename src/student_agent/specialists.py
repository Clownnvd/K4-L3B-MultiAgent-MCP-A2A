"""Evidence specialists communicate through explicit case-correlated handoffs."""
from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from typing import Any

from .evidence import CaseEvidence


@dataclass(frozen=True)
class AgentMessage:
    message_id: str
    case_id: str
    sender: str
    recipient: str
    evidence_refs: tuple[str, ...]
    facts: dict[str, Any]

    def payload(self) -> dict:
        return asdict(self)


async def investigate(
    book: CaseEvidence, actor: str, order_ids: list[str], tools: list[str],
) -> AgentMessage:
    book.trace.emit(case_id=book.case_id, event_type="task_assigned", actor="coordinator",
                    target=actor, attributes={"tools": ",".join(tools)})
    facts, refs = {}, []
    for order in order_ids:
        facts[order] = {}
        for tool in tools:
            result = await book.fetch(actor, tool, order_id=order)
            facts[order][tool] = result["data"]
            refs.append(result["evidence_ref"])
    message = AgentMessage("msg_" + secrets.token_hex(8), book.case_id, actor,
                           "coordinator", tuple(refs), facts)
    book.handoff(actor, "coordinator", refs, "SPECIALIST_EVIDENCE_READY")
    return message


def values_for_key(value: Any, key: str) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        found = value.get(key)
        if isinstance(found, str) and found:
            result.add(found)
        for child in value.values():
            result.update(values_for_key(child, key))
    elif isinstance(value, list):
        for child in value:
            result.update(values_for_key(child, key))
    return result
