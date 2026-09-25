"""Compile a bounded model decision plan into source-bound official output.

The model chooses issue/version/event indices and claim verdicts. Python owns
identifiers, JSON structure, arithmetic, policy amounts and actions. Confidence
tiers (0.35/0.65/0.85) are explicit reporting heuristics, not calibrated likelihoods.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from jsonschema import Draft202012Validator

from .abstention import build_abstention
from .arithmetic import numeric
from .decision_support import derive_decision_support
from .episode_grounding import bind_parties, in_episode, shipment_support
from .facts import source_fact_state
from .verifier import verify_output

ISSUES = [
    "canceled_order_paid",
    "unavailable_order_paid",
    "late_delivery_seller",
    "late_delivery_logistics",
    "valid_split_payment",
    "payment_mismatch",
    "duplicate_charge",
    "refund_pending",
    "refund_failed",
    "unsupported_claim",
    "insufficient_evidence",
]
VERDICTS = ["supported", "unsupported", "partially_supported", "insufficient_evidence"]
TIERS = {"low": 0.35, "medium": 0.65, "high": 0.85}
_SETTLED = {"confirmed", "completed", "settled", "succeeded", "success"}


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _after(left: object, right: object) -> bool | None:
    """Compare aware timestamps only; absent timezone/date remains unknown."""
    if not isinstance(left, str) or not isinstance(right, str):
        return None
    try:
        first = datetime.fromisoformat(left.replace("Z", "+00:00"))
        second = datetime.fromisoformat(right.replace("Z", "+00:00"))
    except ValueError:
        return None
    if first.tzinfo is None or second.tzinfo is None:
        return None
    return first > second


def _events(ledger: dict, domain: str, order_ids: set[str]) -> list[dict]:
    rows = []
    for ref, envelope in sorted(ledger.items()):
        data = envelope["data"]
        if envelope["domain"] != domain or not isinstance(data, dict):
            continue
        for index, event in enumerate(data.get("events", [])):
            if not isinstance(event, dict):
                continue
            order = event.get("order_id", data.get("order_id"))
            if order not in order_ids:
                continue
            row = deepcopy(event)
            row.update(
                {"order_id": order, "evidence_ref": ref, "event_pointer": f"/events/{index}"}
            )
            rows.append(row)
    return rows


def prepare_plan_context(payload: dict) -> dict:
    """Prepare stable choices exclusively from received source records."""
    case, ledger = payload["case"], payload["evidence"]
    resolution = payload["entity_resolution"]
    orders = set(resolution["resolved_order_ids"])
    facts = source_fact_state(case, ledger)
    opened_at = case.get("opened_at")
    excluded = {
        "order_versions": [],
        "capture_choices": [],
        "refund_choices": [],
        "payment_events": [],
        "refund_events": [],
        "shipment_events": [],
    }
    versions = []
    version_count = 0
    for row in sorted(facts["order_observations"], key=lambda r: (r["evidence_ref"], r["pointer"])):
        if row["order_id"] in orders and row["fields"].get("purchase_timestamp"):
            version_count += 1
            purchases = row["fields"]["purchase_timestamp"]
            after_opened = _after(purchases[0]["value"], opened_at) if len(purchases) == 1 else None
            version = {
                "id": f"version_{version_count:04d}",
                **row,
                "after_case_opened": after_opened,
            }
            (excluded["order_versions"] if after_opened is True else versions).append(version)

    scoped_events = {}
    for domain in ("payment", "refund", "shipment"):
        scoped_events[domain] = []
        for event in _events(ledger, domain, orders):
            event["after_case_opened"] = _after(event.get("event_at"), opened_at)
            scoped_events[domain].append(event)

    def atoms(domain: str, event_types: set[str], prefix: str) -> list[dict]:
        choices = []
        identities = {}
        for event in _events(ledger, domain, orders):
            if event.get("event_type") not in event_types or event.get("status") not in _SETTLED:
                continue
            try:
                amount = numeric(event.get("amount_brl"))
            except ValueError:
                continue
            if amount < 0:
                continue
            identity_keys = (
                ("refund_id", "event_id") if domain == "refund" else ("capture_id", "event_id")
            )
            identity = next(
                (
                    (key, event[key])
                    for key in identity_keys
                    if isinstance(event.get(key), str) and event[key]
                ),
                None,
            )
            signature = (amount, event.get("event_at"), event["status"], event["event_type"])
            identity_key = (event["order_id"], identity) if identity else None
            if identity_key in identities:
                previous, previous_signature = identities[identity_key]
                if signature == previous_signature:
                    # Same explicit transaction/event identity and identical content.
                    # Amount equality without identity is never enough to merge charges.
                    continue
                previous["identity_conflict"] = True
            choices.append(
                {
                    "id": f"{prefix}_{len(choices) + 1:04d}",
                    "evidence_ref": event["evidence_ref"],
                    "pointer": event["event_pointer"] + "/amount_brl",
                    "value": event["amount_brl"],
                    "order_id": event["order_id"],
                    "event_at": event.get("event_at"),
                    "status": event["status"],
                    "event_type": event["event_type"],
                    "identity_conflict": bool(identity_key in identities),
                }
            )
            if identity_key is not None and identity_key not in identities:
                identities[identity_key] = (choices[-1], signature)
        for choice in choices:
            choice["after_case_opened"] = _after(choice["event_at"], opened_at)
        return choices

    payment_records = []
    for ref, envelope in sorted(ledger.items()):
        data = envelope["data"]
        if envelope["domain"] != "payment" or not isinstance(data, dict):
            continue
        for index, record in enumerate(data.get("payments", [])):
            if isinstance(record, dict) and record.get("order_id", data.get("order_id")) in orders:
                payment_records.append(
                    {**deepcopy(record), "evidence_ref": ref, "pointer": f"/payments/{index}"}
                )

    policies = [
        {"evidence_ref": ref, "rules": deepcopy(entry["data"].get("rules", {}))}
        for ref, entry in sorted(ledger.items())
        if entry["domain"] == "policy"
        and isinstance(entry["data"], dict)
        and (
            not case.get("policy_version")
            or entry["data"].get("policy_version") == case["policy_version"]
        )
    ]
    result = {
        "case": deepcopy(case),
        "entity_resolution": deepcopy(resolution),
        "order_versions": versions,
        "capture_choices": atoms(
            "payment", {"captured", "payment_captured", "capture_completed"}, "capture"
        ),
        "refund_choices": atoms(
            "refund", {"refund_completed", "refunded", "refund_settled"}, "refund"
        ),
        "policy_sources": policies,
        "payment_events": scoped_events["payment"],
        "refund_events": scoped_events["refund"],
        "shipment_events": scoped_events["shipment"],
        "payment_records": payment_records,
        "excluded_future": excluded,
        "source_facts": facts,
        "claim_ids": [
            claim["claim_id"] for claim in case.get("customer_request", {}).get("claims", [])
        ],
        "tool_failures": deepcopy(payload.get("tool_failures", [])),
        "confidence_tiers": dict(TIERS),
        "instructions": (
            "Choose only listed indices; no literal amounts, entities, evidence refs or actions. "
            "Case opening is NOT an accounting cutoff. Later captures/refunds can resolve an "
            "earlier complaint. Purchases after opening remain excluded from candidate episodes, "
            "but received events stay available. Bind events to the selected purchase episode "
            "until the next distinct purchase timestamp of the same order. "
            "Missing dates/timezones are unknown. episode_support gives handoff comparisons "
            "with sources; delivery lateness alone does not establish seller blame. "
            "Include every known completed refund in the selected episode, not a subset. "
            "Select the relevant dated order version and payment/refund events without mixing "
            "different purchase episodes. Future records stay visible, not automatically valid. "
            "Pending/failed refund requests are not completed refunds. No source precedence is "
            "assumed. Customer claim topics are allegations, not answer labels. "
            "Use case opening time and transaction version when assessing event relevance. "
            "decision_support summarizes code-checked candidates and compatible version/event "
            "indices. Use direct current-period refund states and reconciled payment methods "
            "instead of abstaining solely because an unrelated old/future version exists. "
            "A split-payment subset with other captures still requires reviewing that conflict. "
            "These signals are evidence summaries, not claim labels or guaranteed answers. "
            "Choose insufficient_evidence and unknown when interpretation is unresolved."
        ),
    }
    result["decision_support"] = derive_decision_support(result, ledger)
    result["episode_support"] = {
        version["id"]: shipment_support(version, result, ledger) for version in versions
    }
    return result


def plan_schema(payload: dict) -> dict:
    context = prepare_plan_context(payload)

    def choices(rows: list[dict]) -> dict:
        ids = [row["id"] for row in rows]
        return {
            "type": "array",
            "uniqueItems": True,
            "maxItems": len(ids),
            "items": {"type": "string", **({"enum": ids} if ids else {})},
        }

    properties = {
        "primary_issue": {"type": "string", "enum": ISSUES},
        "secondary_issues": {
            "type": "array",
            "maxItems": 10,
            "uniqueItems": True,
            "items": {"type": "string", "enum": ISSUES},
        },
        "order_version_id": {
            "type": "string",
            "enum": ["unknown", *[row["id"] for row in context["order_versions"]]],
        },
        "capture_ids": choices(context["capture_choices"]),
        "refund_ids": choices(context["refund_choices"]),
        "claim_verdicts": {
            "type": "object",
            "additionalProperties": False,
            "required": context["claim_ids"],
            "properties": {
                claim: {"type": "string", "enum": VERDICTS} for claim in context["claim_ids"]
            },
        },
        "confidence_tier": {"type": "string", "enum": list(TIERS)},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _money(value: Decimal) -> float:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    encoded = float(rounded)
    if numeric(encoded) != rounded:
        raise ValueError("Compiled money loses source precision")
    return encoded


def compile_plan(plan: dict, payload: dict) -> dict:
    """Compile choices; unknown balances never authorize a refund or a refund action."""
    error = next(Draft202012Validator(plan_schema(payload)).iter_errors(plan), None)
    if error is not None:
        raise ValueError("Invalid decision plan: " + "/".join(map(str, error.absolute_path)))
    context = prepare_plan_context(payload)
    case, ledger, resolution = payload["case"], payload["evidence"], payload["entity_resolution"]
    output = build_abstention(case, ledger, resolution)
    if output["entity_resolution"] != resolution:
        raise ValueError("Decision plan received an unvalidated entity resolution")
    if len(ledger) > 20:
        raise ValueError("Decision plan evidence exceeds bounded trace citation budget")
    refs = sorted(ledger)
    output["evidence_refs"] = refs
    selected = {row["id"]: row for row in context["order_versions"]}.get(plan["order_version_id"])
    captures = [row for row in context["capture_choices"] if row["id"] in plan["capture_ids"]]
    refunds = [row for row in context["refund_choices"] if row["id"] in plan["refund_ids"]]
    if any(row.get("identity_conflict") for row in [*captures, *refunds]):
        raise ValueError("Conflicting financial transaction identity needs investigation")
    if selected:
        purchases = selected["fields"].get("purchase_timestamp", [])
        if len(purchases) == 1:
            for event in [*captures, *refunds]:
                if _after(purchases[0]["value"], event.get("event_at")) is True:
                    raise ValueError("Decision plan event occurs before selected purchase")
                if in_episode(event, selected, context) is False:
                    raise ValueError("Decision plan event belongs to another purchase episode")
    orders = set(resolution["resolved_order_ids"])
    failures = {
        failure["tool_name"]
        for failure in payload.get("tool_failures", [])
        if failure["arguments"].get("order_id") in orders
    }

    def available(domain: str) -> bool:
        present = {
            entry["data"].get("order_id")
            for entry in ledger.values()
            if entry["domain"] == domain and isinstance(entry["data"], dict)
        }
        return bool(orders) and orders <= present

    captured = sum((numeric(row["value"]) for row in captures), Decimal(0)) if captures else None
    episode_refunds = [
        row
        for row in context["refund_choices"]
        if selected is None or in_episode(row, selected, context) is not False
    ]
    certain_refunds = {
        row["id"]
        for row in episode_refunds
        if selected is not None and in_episode(row, selected, context) is True
    }
    if certain_refunds - set(plan["refund_ids"]):
        raise ValueError("Decision plan omits known completed refund in selected episode")
    refund_known = (
        available("refund")
        and "get_refund_timeline" not in failures
        and (not episode_refunds or {r["id"] for r in episode_refunds} <= set(plan["refund_ids"]))
    )
    refunded = sum((numeric(row["value"]) for row in refunds), Decimal(0)) if refund_known else None
    if "get_payment_timeline" in failures or not available("payment"):
        captured = None
    balance = (
        max(Decimal(0), captured - refunded)
        if (captured is not None and refunded is not None)
        else None
    )
    calculations = []

    def bind(target: str, rows: list[dict]) -> None:
        calculations.append(
            {
                "target": target,
                "operation": "sum" if rows else "zero",
                "operands": [
                    {"evidence_ref": row["evidence_ref"], "pointer": row["pointer"]} for row in rows
                ],
            }
        )

    for field, amount, rows in [
        ("captured_total_brl", captured, captures),
        ("refunded_total_brl", refunded, refunds),
    ]:
        output["payment_analysis"][field] = None if amount is None else _money(amount)
        if amount is not None:
            bind("/payment_analysis/" + field, rows)
    if balance is not None:
        output["payment_analysis"]["refundable_total_brl"] = _money(balance)
        calculations.append(
            {
                "target": "/payment_analysis/refundable_total_brl",
                "operation": "net",
                "positive_count": len(captures),
                "operands": [
                    {"evidence_ref": row["evidence_ref"], "pointer": row["pointer"]}
                    for row in [*captures, *refunds]
                ],
            }
        )

    issue = plan["primary_issue"]
    support = context["episode_support"].get(plan["order_version_id"], {})
    statuses = (
        {source["value"] for source in selected["fields"].get("order_status", [])}
        if (selected)
        else set()
    )
    if issue in {"canceled_order_paid", "unavailable_order_paid"}:
        expected = "canceled" if issue == "canceled_order_paid" else "unavailable"
        if statuses != {expected} or not captured:
            issue = "insufficient_evidence"
    if issue.startswith("late_delivery_") and (
        selected is None or selected["delivery_comparison"]["verdict"] != "late"
    ):
        issue = "insufficient_evidence"
    if issue == "late_delivery_seller" and support.get("handoff_verdict") != "seller_delay":
        issue = "insufficient_evidence"
    if issue in {"valid_split_payment", "duplicate_charge"} and len(captures) < 2:
        issue = "insufficient_evidence"
    if issue in {"refund_pending", "refund_failed"}:
        status = "pending" if issue == "refund_pending" else "failed"
        if not any(
            event.get("status") == status
            and str(event.get("event_type", "")).startswith("refund")
            and selected is not None
            and in_episode(event, selected, context) is not False
            for event in context["refund_events"]
        ):
            issue = "insufficient_evidence"
    if issue == "payment_mismatch" and not any(
        "mismatch" in event.get("event_type", "")
        for event in context["payment_events"]
        if selected is not None and in_episode(event, selected, context) is not False
    ):
        issue = "insufficient_evidence"
    if resolution["status"] != "resolved" or selected is None:
        issue = "insufficient_evidence"

    policy = context["policy_sources"][0] if len(context["policy_sources"]) == 1 else None
    rule = policy["rules"].get(issue, {}) if policy else {}
    if not isinstance(rule, dict):
        rule = {}
    status = rule.get("case_status", "needs_investigation")
    if status not in {"action_required", "no_action", "needs_investigation"}:
        status = "needs_investigation"
    try:
        policy_amount = numeric(rule.get("refund_brl"))
    except ValueError:
        policy_amount = None
    recommended = Decimal(0)
    if (
        issue != "insufficient_evidence"
        and policy_amount is not None
        and policy_amount >= 0
        and balance is not None
        and policy_amount <= balance
    ):
        recommended = policy_amount
    if (
        balance is None
        or policy_amount is None
        or policy_amount < 0
        or (policy_amount > 0 and recommended == 0)
        or issue == "insufficient_evidence"
    ):
        status = "needs_investigation"
    if recommended and status == "no_action":
        recommended, status = Decimal(0), "needs_investigation"
    confidence = 0 if issue == "insufficient_evidence" else TIERS[plan["confidence_tier"]]
    output["assessment"].update(
        {
            "primary_issue": issue,
            "case_status": status,
            "confidence": confidence,
            "secondary_issues": [value for value in plan["secondary_issues"] if value != issue],
        }
    )
    allowed = context["source_facts"]["allowed_entity_ids"]
    for category in ("item_ids", "seller_ids", "payment_references", "shipment_ids"):
        output["affected_entities"][category] = allowed[category][:20]
    customers = {
        entry["data"].get("customer_unique_id")
        for entry in ledger.values()
        if entry["domain"] == "customer"
        and isinstance(entry["data"], dict)
        and isinstance(entry["data"].get("customer_unique_id"), str)
    }
    if len(customers) == 1:
        output["customer_context"] = {
            "customer_unique_id": next(iter(customers)),
            "related_order_ids": allowed["order_ids"][:20],
        }
    if selected:
        comparison = selected["delivery_comparison"]
        shipment = "on_time" if comparison["verdict"] == "on_time" else "insufficient_evidence"
        if comparison["verdict"] == "late" and issue.startswith("late_delivery_"):
            shipment = "seller_delay" if issue == "late_delivery_seller" else "logistics_delay"
        output["shipment_analysis"].update(
            {"verdict": shipment, "timeline_complete": comparison["verdict"] != "unknown"}
        )
        if shipment == "seller_delay":
            output["shipment_analysis"]["late_seller_ids"] = support.get("late_seller_ids", [])
    if captured is not None and refunded is not None:
        payment_verdict = {
            "duplicate_charge": "duplicate_capture",
            "payment_mismatch": "capture_mismatch",
            "refund_pending": "refund_pending",
            "refund_failed": "refund_failed",
        }.get(issue, "refunded" if refunded >= captured and captured > 0 else "reconciled")
        output["payment_analysis"]["verdict"] = payment_verdict
    if recommended:
        output["financial_resolution"].update(
            {
                "recommended_refund_brl": _money(recommended),
                "refund_lines": [
                    {
                        "reason_code": issue.upper(),
                        "amount_brl": _money(recommended),
                        "entity_id": selected["order_id"] if selected else None,
                    }
                ],
            }
        )
        source = [
            {
                "evidence_ref": policy["evidence_ref"],
                "pointer": "/rules/" + _escape(issue) + "/refund_brl",
            }
        ]
        bind("/financial_resolution/recommended_refund_brl", source)
        bind("/financial_resolution/refund_lines/0/amount_brl", source)
    action = rule.get("recommended_action")
    if (
        isinstance(action, str)
        and 0 < len(action) <= 80
        and (recommended or "refund" not in action or action == "monitor_refund")
    ):
        output["resolution_actions"] = [action]
    parties = rule.get("responsible_parties", [])
    if isinstance(parties, list) and len(parties) <= 5:
        output["root_cause_analysis"]["responsible_parties"] = bind_parties(parties, issue, support)
    if issue != "insufficient_evidence":
        output["root_cause_analysis"]["ranked_causes"] = [{"cause_code": issue.upper(), "rank": 1}]
    for claim in output["claim_assessments"]:
        verdict = plan["claim_verdicts"][claim["claim_id"]]
        if issue == "insufficient_evidence":
            verdict = "insufficient_evidence"
        claim.update(
            {
                "verdict": verdict,
                "confidence": 0 if verdict == "insufficient_evidence" else confidence,
                "evidence_refs": list(refs),
            }
        )
    for conflict in context["source_facts"]["data_conflicts"]:
        if 2 <= len(conflict["sources"]) <= 5 and all(
            len(ref) <= 80 for ref in conflict["sources"]
        ):
            output["data_conflicts"].append(
                {
                    "field": conflict["field"],
                    "sources": conflict["sources"],
                    "selected_source": None,
                    "resolution_code": "UNRESOLVED_SOURCE_VERSION_CONFLICT",
                }
            )
        if len(output["data_conflicts"]) == 5:
            break
    verify_output(case["case_id"], output, ledger)
    return {"output": output, "calculations": calculations}
