import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest

from student_agent.arithmetic import verify_calculations
from student_agent.contracts import Contracts
from student_agent.decision_plan import compile_plan, plan_schema, prepare_plan_context
from student_agent.verifier import verify_output

ROOT = Path(__file__).resolve().parents[1]


def test_model_plan_mode_generates_choices_and_python_compiles_money():
    from student_agent.model_adapter import ModelSettings, OpenAICompatibleModel

    payload = fixture()
    plan = make_plan(payload)

    class Response:
        status_code = 200

        def json(self):
            return {
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(plan)}}]
            }

    class Client:
        async def post(self, url, *, json, headers):
            sent = __import__("json").loads(json["messages"][1]["content"])["payload"]
            assert sent["case"]["case_id"] == "CASE_DEMO_PLAN"
            assert sent["refund_events"]
            assert "output" not in json["response_format"]["json_schema"]["schema"]["required"]
            assert json["max_tokens"] <= 2048
            return Response()

    settings = ModelSettings(
        "Qwen/Qwen3.5-9B", "Qwen/Qwen3.5-9B", "http://local/v1", "", decision_mode="plan"
    )
    result = asyncio.run(
        OpenAICompatibleModel(settings, Client()).complete("decide_policy", payload)
    )
    assert result["decision_plan"] == plan
    assert result["output"]["financial_resolution"]["recommended_refund_brl"] == 40
    assert result["output"]["payment_analysis"]["refundable_total_brl"] == 90


def fixture():
    def envelope(domain, data):
        ref = "ev_" + domain + "_" + "a" * 24
        return ref, {"evidence_ref": ref, "domain": domain, "data": data}

    evidence = dict(
        [
            envelope(
                "order",
                {
                    "order_id": "order-demo",
                    "order_status": "canceled",
                    "order_purchase_timestamp": "2020-01-01T00:00:00Z",
                },
            ),
            envelope(
                "payment",
                {
                    "order_id": "order-demo",
                    "events": [
                        {"event_type": "captured", "status": "confirmed", "amount_brl": "80.00"},
                        {"event_type": "captured", "status": "confirmed", "amount_brl": "20.00"},
                    ],
                },
            ),
            envelope(
                "refund",
                {
                    "order_id": "order-demo",
                    "events": [
                        {
                            "event_type": "refund_completed",
                            "status": "completed",
                            "amount_brl": "10.00",
                        },
                        {
                            "event_type": "refund_requested",
                            "status": "failed",
                            "amount_brl": "50.00",
                        },
                        {
                            "event_type": "refund_requested",
                            "status": "pending",
                            "amount_brl": "30.00",
                        },
                    ],
                },
            ),
            envelope(
                "policy",
                {
                    "rules": {
                        "canceled_order_paid": {
                            "refund_brl": 40,
                            "case_status": "action_required",
                            "recommended_action": "issue_refund",
                            "responsible_parties": [{"party_type": "platform", "party_id": None}],
                        }
                    }
                },
            ),
        ]
    )
    return {
        "case": {
            "case_id": "CASE_DEMO_PLAN",
            "opened_at": "2020-01-10T00:00:00Z",
            "candidate_order_ids": ["order-demo"],
            "customer_request": {
                "claims": [
                    {"claim_id": "claim-a", "topic": "canceled_order_paid"},
                    {"claim_id": "claim-b", "topic": "requested_full_refund"},
                ]
            },
        },
        "entity_resolution": {
            "status": "resolved",
            "resolved_order_ids": ["order-demo"],
            "rejected_candidates": [],
            "confidence": 0.95,
        },
        "evidence": evidence,
        "tool_failures": [],
    }


def make_plan(payload):
    context = prepare_plan_context(payload)
    return {
        "primary_issue": "canceled_order_paid",
        "secondary_issues": [],
        "order_version_id": context["order_versions"][0]["id"],
        "capture_ids": [row["id"] for row in context["capture_choices"]],
        "refund_ids": [row["id"] for row in context["refund_choices"]],
        "claim_verdicts": {"claim-a": "supported", "claim-b": "partially_supported"},
        "confidence_tier": "medium",
    }


def test_compiler_binds_policy_refund_and_net_balance_without_model_numbers():
    payload = fixture()
    preserved = deepcopy(payload)
    proposal = compile_plan(make_plan(payload), payload)
    output = proposal["output"]
    Contracts(ROOT / "contracts/schemas").validate_output(output, "plan")
    verify_output(payload["case"]["case_id"], output, payload["evidence"])
    verify_calculations(output, proposal["calculations"], payload["evidence"])
    assert output["financial_resolution"]["recommended_refund_brl"] == 40
    assert output["payment_analysis"]["captured_total_brl"] == 100
    assert output["payment_analysis"]["refunded_total_brl"] == 10
    assert output["payment_analysis"]["refundable_total_brl"] == 90
    assert output["resolution_actions"] == ["issue_refund"]
    assert payload == preserved


def test_missing_refund_timeline_stays_unknown_and_cannot_issue_refund():
    payload = fixture()
    payload["evidence"] = {
        ref: row for ref, row in payload["evidence"].items() if row["domain"] != "refund"
    }
    payload["tool_failures"] = [
        {"tool_name": "get_refund_timeline", "arguments": {"order_id": "order-demo"}}
    ]
    proposal = compile_plan(make_plan(payload), payload)
    output = proposal["output"]
    assert output["payment_analysis"]["refunded_total_brl"] is None
    assert output["payment_analysis"]["refundable_total_brl"] is None
    assert output["financial_resolution"]["recommended_refund_brl"] == 0
    assert output["assessment"]["case_status"] == "needs_investigation"
    assert "issue_refund" not in output["resolution_actions"]
    verify_calculations(output, proposal["calculations"], payload["evidence"])


def test_plan_indices_are_stable_and_exclude_failed_pending_refunds():
    payload = fixture()
    context = prepare_plan_context(payload)
    assert len(context["refund_choices"]) == 1
    assert context["refund_choices"][0]["value"] == "10.00"
    reordered = {**payload, "evidence": dict(reversed(list(payload["evidence"].items())))}
    assert prepare_plan_context(reordered)["capture_choices"] == context["capture_choices"]
    plan = make_plan(payload)
    plan["refund_ids"] = ["refund_9999"]
    with pytest.raises(ValueError, match="plan"):
        compile_plan(plan, payload)
    assert "claim-b" in plan_schema(payload)["properties"]["claim_verdicts"]["required"]


def test_policy_amount_larger_than_balance_is_not_clamped_into_new_policy():
    payload = fixture()
    for row in payload["evidence"].values():
        if row["domain"] == "policy":
            row["data"]["rules"]["canceled_order_paid"]["refund_brl"] = 500
    output = compile_plan(make_plan(payload), payload)["output"]
    assert output["financial_resolution"]["recommended_refund_brl"] == 0
    assert output["assessment"]["case_status"] == "needs_investigation"


@pytest.mark.parametrize(
    "issue,amount,status,action",
    [
        ("valid_split_payment", 0, "no_action", "document_no_action"),
        ("duplicate_charge", 20, "action_required", "refund_duplicate_charge"),
        ("refund_pending", 0, "needs_investigation", "monitor_refund"),
        ("refund_failed", 50, "action_required", "retry_refund"),
    ],
)
def test_plan_policy_actions_and_amounts_remain_source_bound(issue, amount, status, action):
    payload = fixture()
    policy = next(row for row in payload["evidence"].values() if row["domain"] == "policy")
    policy["data"]["rules"][issue] = {
        "refund_brl": amount,
        "case_status": status,
        "recommended_action": action,
        "responsible_parties": [],
    }
    plan = make_plan(payload)
    plan["primary_issue"] = issue
    proposal = compile_plan(plan, payload)
    output = proposal["output"]
    assert output["assessment"]["primary_issue"] == issue
    assert output["financial_resolution"]["recommended_refund_brl"] == amount
    assert output["resolution_actions"] == [action]
    Contracts(ROOT / "contracts/schemas").validate_output(output, "plan")
    verify_calculations(output, proposal["calculations"], payload["evidence"])


def test_canceled_issue_cannot_override_selected_delivered_record():
    payload = fixture()
    for row in payload["evidence"].values():
        if row["domain"] == "order":
            row["data"]["order_status"] = "delivered"
    output = compile_plan(make_plan(payload), payload)["output"]
    assert output["assessment"]["primary_issue"] == "insufficient_evidence"
    assert output["assessment"]["confidence"] == 0
    assert output["financial_resolution"]["recommended_refund_brl"] == 0
