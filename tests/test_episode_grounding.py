from copy import deepcopy

import pytest

from student_agent.arithmetic import verify_calculations
from student_agent.decision_plan import compile_plan, prepare_plan_context
from test_decision_plan import fixture, make_plan


def entry(payload, domain):
    return next(e for e in payload["evidence"].values() if e["domain"] == domain)["data"]


def add_items(payload, rows):
    ref = "ev_items_" + "b" * 24
    payload["evidence"][ref] = {"evidence_ref": ref, "domain": "item", "data": rows}


def seller_payload(issue="unavailable_order_paid"):
    payload = fixture()
    order = entry(payload, "order")
    order["order_status"] = "unavailable" if issue == "unavailable_order_paid" else "delivered"
    order.update(
        order_delivered_carrier_date="2020-01-05T00:00:00Z",
        order_delivered_customer_date="2020-01-09T00:00:00Z",
        order_estimated_delivery_date="2020-01-08T00:00:00Z",
    )
    add_items(
        payload,
        [
            dict(
                order_id="order-demo",
                order_item_id="item-real",
                seller_id="seller-real",
                shipping_limit_date="2020-01-03T00:00:00Z",
                price="80.00",
                freight_value="20.00",
            )
        ],
    )
    entry(payload, "policy")["rules"][issue] = {
        "case_status": "action_required",
        "refund_brl": 20,
        "recommended_action": "issue_refund"
        if issue == "unavailable_order_paid"
        else "refund_freight",
        "responsible_parties": [{"party_type": "seller", "party_id": "seller-other-order"}],
    }
    plan = make_plan(payload)
    plan["primary_issue"] = issue
    return payload, plan


def test_policy_example_seller_id_cannot_escape_resolved_order():
    payload, plan = seller_payload()
    result = compile_plan(plan, payload)
    assert result["output"]["root_cause_analysis"]["responsible_parties"] == [
        {"party_type": "seller", "party_id": "seller-real"}
    ]
    verify_calculations(result["output"], result["calculations"], payload["evidence"])


def test_multiple_sellers_do_not_get_arbitrary_policy_example_attribution():
    payload, plan = seller_payload()
    other = deepcopy(entry(payload, "item")[0])
    other.update(seller_id="seller-second", order_item_id="item-second")
    entry(payload, "item").append(other)
    parties = compile_plan(plan, payload)["output"]["root_cause_analysis"]["responsible_parties"]
    assert parties == [{"party_type": "seller", "party_id": None}]


def test_missing_deadline_does_not_remove_a_seller_from_identity_scope():
    payload, plan = seller_payload()
    other = deepcopy(entry(payload, "item")[0])
    other.update(seller_id="seller-second", order_item_id="item-second")
    other.pop("shipping_limit_date")
    entry(payload, "item").append(other)
    parties = compile_plan(plan, payload)["output"]["root_cause_analysis"]["responsible_parties"]
    assert parties == [{"party_type": "seller", "party_id": None}]


def test_seller_delay_lists_only_deadlines_violated_in_selected_episode():
    payload, plan = seller_payload("late_delivery_seller")
    other = deepcopy(entry(payload, "item")[0])
    other.update(
        seller_id="seller-ontime",
        order_item_id="item-ontime",
        shipping_limit_date="2020-01-06T00:00:00Z",
    )
    entry(payload, "item").append(other)
    result = compile_plan(plan, payload)["output"]
    assert result["shipment_analysis"]["late_seller_ids"] == ["seller-real"]
    assert result["root_cause_analysis"]["responsible_parties"] == [
        {"party_type": "seller", "party_id": "seller-real"}
    ]


def test_customer_delivery_late_does_not_alone_prove_seller_delay():
    payload, plan = seller_payload("late_delivery_seller")
    entry(payload, "item")[0]["shipping_limit_date"] = "2020-01-06T00:00:00Z"
    result = compile_plan(plan, payload)["output"]
    assert result["assessment"]["primary_issue"] == "insufficient_evidence"
    assert result["financial_resolution"]["recommended_refund_brl"] == 0


def test_old_deadline_cannot_blame_current_seller():
    payload, plan = seller_payload("late_delivery_seller")
    entry(payload, "item")[0]["shipping_limit_date"] = "2019-12-31T00:00:00Z"
    assert (
        compile_plan(plan, payload)["output"]["assessment"]["primary_issue"]
        == "insufficient_evidence"
    )


def test_failed_refund_from_old_purchase_is_not_current_failure():
    payload = fixture()
    entry(payload, "refund")["events"][1]["event_at"] = "2019-12-30T00:00:00Z"
    plan = make_plan(payload)
    plan["primary_issue"] = "refund_failed"
    assert (
        compile_plan(plan, payload)["output"]["assessment"]["primary_issue"]
        == "insufficient_evidence"
    )


def test_omitting_known_current_completed_refund_cannot_inflate_balance():
    payload = fixture()
    entry(payload, "refund")["events"] = [
        dict(
            event_type="refund_completed",
            status="completed",
            amount_brl="10",
            event_at="2020-01-04T00:00:00Z",
        ),
        dict(
            event_type="refund_completed",
            status="completed",
            amount_brl="15",
            event_at="2020-01-05T00:00:00Z",
        ),
    ]
    plan = make_plan(payload)
    plan["refund_ids"] = plan["refund_ids"][:1]
    with pytest.raises(ValueError, match="omits.*refund"):
        compile_plan(plan, payload)


def test_duplicate_observation_of_same_refund_transaction_is_counted_once():
    payload = fixture()
    refund = dict(
        refund_id="refund-same",
        event_type="refund_completed",
        status="completed",
        amount_brl="60",
        event_at="2020-01-05T00:00:00Z",
    )
    entry(payload, "refund")["events"] = [refund, deepcopy(refund)]
    plan = make_plan(payload)
    assert len(plan["refund_ids"]) == 1
    result = compile_plan(plan, payload)
    assert result["output"]["payment_analysis"]["refunded_total_brl"] == 60
    verify_calculations(result["output"], result["calculations"], payload["evidence"])


def test_different_ids_with_same_refund_amount_are_not_deduplicated():
    payload = fixture()
    refund = dict(
        refund_id="refund-a",
        event_type="refund_completed",
        status="completed",
        amount_brl="20",
        event_at="2020-01-05T00:00:00Z",
    )
    other = {**refund, "refund_id": "refund-b"}
    entry(payload, "refund")["events"] = [refund, other]
    result = compile_plan(make_plan(payload), payload)["output"]
    assert result["payment_analysis"]["refunded_total_brl"] == 40


def test_conflicting_amounts_for_same_refund_identity_cannot_be_added():
    payload = fixture()
    refund = dict(
        refund_id="refund-same",
        event_type="refund_completed",
        status="completed",
        amount_brl="20",
        event_at="2020-01-05T00:00:00Z",
    )
    entry(payload, "refund")["events"] = [refund, {**refund, "amount_brl": "30"}]
    with pytest.raises(ValueError, match="Conflicting financial transaction"):
        compile_plan(make_plan(payload), payload)


def test_plural_seller_ids_are_valid_identity_witnesses_for_grounding():
    from student_agent.output_builder import build_grounded_output

    payload, plan = seller_payload()
    output = compile_plan(plan, payload)["output"]
    for row in entry(payload, "item"):
        row.pop("seller_id")
    ref = "ev_seller_" + "e" * 24
    payload["evidence"][ref] = {
        "evidence_ref": ref,
        "domain": "seller",
        "data": {"order_id": "order-demo", "seller_ids": ["seller-real"]},
    }
    result = build_grounded_output(output, payload["case"], payload["evidence"])
    assert result["root_cause_analysis"]["responsible_parties"] == [
        {"party_type": "seller", "party_id": "seller-real"}
    ]


def test_old_completed_refund_does_not_make_current_empty_history_unknown():
    payload = fixture()
    entry(payload, "refund")["events"] = [
        dict(
            event_type="refund_completed",
            status="completed",
            amount_brl="10",
            event_at="2019-12-15T00:00:00Z",
        )
    ]
    plan = make_plan(payload)
    plan["refund_ids"] = []
    result = compile_plan(plan, payload)["output"]
    assert result["payment_analysis"]["refunded_total_brl"] == 0
    assert result["payment_analysis"]["refundable_total_brl"] == 100


def test_policy_version_mismatch_cannot_authorize_money():
    payload = fixture()
    payload["case"]["policy_version"] = "current"
    entry(payload, "policy")["policy_version"] = "obsolete"
    result = compile_plan(make_plan(payload), payload)["output"]
    assert result["financial_resolution"]["recommended_refund_brl"] == 0
    assert result["assessment"]["case_status"] == "needs_investigation"


def test_context_explains_current_seller_handoff_without_model_date_math():
    payload, _ = seller_payload("late_delivery_seller")
    context = prepare_plan_context(payload)
    support = context["episode_support"][context["order_versions"][0]["id"]]
    assert support["late_seller_ids"] == ["seller-real"]
    assert support["handoff_verdict"] == "seller_delay"


def test_completed_refund_after_complaint_blocks_a_second_refund():
    payload = fixture()
    entry(payload, "refund")["events"] = [
        dict(
            event_type="refund_completed",
            status="completed",
            amount_brl="100",
            event_at="2020-01-11T00:00:00Z",
        )
    ]
    result = compile_plan(make_plan(payload), payload)
    assert result["output"]["payment_analysis"]["refunded_total_brl"] == 100
    assert result["output"]["payment_analysis"]["refundable_total_brl"] == 0
    assert result["output"]["financial_resolution"]["recommended_refund_brl"] == 0
    verify_calculations(result["output"], result["calculations"], payload["evidence"])


def test_refund_at_next_purchase_boundary_cannot_be_mixed_with_selected_purchase():
    payload = fixture()
    ref = "ev_customer_" + "c" * 24
    payload["evidence"][ref] = {
        "evidence_ref": ref,
        "domain": "customer",
        "data": {
            "customer_unique_id": "customer-real",
            "orders": [
                dict(order_id="order-demo", order_purchase_timestamp="2020-02-01T00:00:00Z")
            ],
        },
    }
    entry(payload, "refund")["events"] = [
        dict(
            event_type="refund_completed",
            status="completed",
            amount_brl="10",
            event_at="2020-02-01T00:00:00Z",
        )
    ]
    plan = make_plan(payload)
    with pytest.raises(ValueError, match="another purchase episode"):
        compile_plan(plan, payload)


def test_undated_completed_refund_not_selected_remains_unknown():
    payload = fixture()
    plan = make_plan(payload)
    plan["refund_ids"] = []
    result = compile_plan(plan, payload)["output"]
    assert result["payment_analysis"]["refunded_total_brl"] is None
    assert result["financial_resolution"]["recommended_refund_brl"] == 0


def test_logistics_event_cannot_bypass_seller_handoff_contradiction_gate():
    from student_agent.output_builder import GroundingError, build_grounded_output

    payload, plan = seller_payload("late_delivery_seller")
    output = compile_plan(plan, payload)["output"]
    entry(payload, "item")[0]["shipping_limit_date"] = "2020-01-06T00:00:00Z"
    ref = "ev_shipment_" + "d" * 24
    payload["evidence"][ref] = {
        "evidence_ref": ref,
        "domain": "shipment",
        "data": {
            "order_id": "order-demo",
            "delivered_carrier_at": "2020-01-05T00:00:00Z",
            "shipping_limits": [{"shipping_limit_at": "2020-01-06T00:00:00Z"}],
            "events": [{"event_type": "delivered_late", "actor": "logistics_provider"}],
        },
    }
    # Keep one complete shipment comparison: a separate order lacks a shipping deadline.
    entry(payload, "order").pop("order_delivered_carrier_date")
    with pytest.raises(GroundingError, match="SHIPMENT_DELAY_CONTRADICTED"):
        build_grounded_output(output, payload["case"], payload["evidence"])
