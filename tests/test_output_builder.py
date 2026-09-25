from copy import deepcopy

import pytest

from student_agent.output_builder import GroundingError, build_grounded_output


def fixture():
    case = {"case_id": "CASE_SAMPLE", "policy_version": "POLICY_A"}
    output = {
        "case_id": "CASE_SAMPLE",
        "assessment": {"primary_issue": "payment_mismatch", "secondary_issues": [],
                       "case_status": "needs_investigation", "confidence": 0.71},
        "entity_resolution": {"status": "resolved", "resolved_order_ids": ["order-a"]},
        "affected_entities": {"order_ids": ["order-a", "order-b"],
                              "item_ids": ["item-a", "item-b", "invented-item"],
                              "seller_ids": ["seller-a", "seller-b"],
                              "payment_references": ["ev_payment", "payment-a", "payment-b"],
                              "shipment_ids": ["ev_shipment", "shipment-a", "shipment-b"]},
        "resolution_actions": ["reconcile_payment_mismatch"], "evidence_refs": ["ev_order"],
        "shipment_analysis": {"verdict": "insufficient_evidence"},
        "payment_analysis": {"verdict": "insufficient_evidence", "captured_total_brl": 12},
        "financial_resolution": {"recommended_refund_brl": 0, "refund_lines": []},
        "claim_assessments": [],
    }
    ledger = {
        "ev_order": {"domain": "order", "data": {"order_id": "order-a"}},
        "ev_items": {"domain": "item", "data": [
            {"order_id": "order-a", "order_item_id": "item-a", "seller_id": "seller-a"},
            {"order_id": "order-b", "order_item_id": "item-b", "seller_id": "seller-b"}]},
        "ev_payment": {"domain": "payment", "data": {"order_id": "order-a",
            "payment_references": ["payment-a"], "related": [
                {"order_id": "order-b", "payment_reference": "payment-b"}]}},
        "ev_shipment": {"domain": "shipment", "data": {"order_id": "order-a",
            "shipment_id": "shipment-a", "related": [
                {"order_id": "order-b", "shipment_id": "shipment-b"}]}},
        "ev_policy": {"domain": "policy", "data": {"policy_version": "POLICY_A", "rules": {
            "payment_mismatch": {"recommended_action": "reconcile_payment", "refund_brl": 12}}}},
    }
    return output, case, ledger


def test_source_key_and_resolved_order_intersection_and_policy_citation():
    output, case, ledger = fixture()
    before = deepcopy((output, case, ledger))
    result = build_grounded_output(output, case, ledger)
    assert result["affected_entities"] == {
        "order_ids": ["order-a"], "item_ids": ["item-a"], "seller_ids": ["seller-a"],
        "payment_references": ["payment-a"], "shipment_ids": ["shipment-a"],
    }
    assert result["resolution_actions"] == ["reconcile_payment"]
    assert "ev_policy" in result["evidence_refs"]
    assert result["assessment"] == output["assessment"]
    assert result["financial_resolution"] == output["financial_resolution"]
    assert result["payment_analysis"] == output["payment_analysis"]
    assert (output, case, ledger) == before
    assert result is not output


def test_actual_ev_prefixed_source_identifier_is_not_blindly_removed():
    output, case, ledger = fixture()
    ledger["ev_payment"]["data"]["payment_references"] = ["ev_payment"]
    assert build_grounded_output(output, case, ledger)["affected_entities"][
        "payment_references"] == ["ev_payment"]


@pytest.mark.parametrize("verdict", ["logistics_delay", "seller_delay"])
def test_all_complete_source_versions_on_time_refutes_lateness(verdict):
    output, case, ledger = fixture()
    output["shipment_analysis"]["verdict"] = verdict
    ledger["ev_shipment"]["data"].update({
        "delivered_carrier_at": "2020-01-02T00:00:00Z",
        "delivered_customer_at": "2020-01-05T00:00:00Z",
        "estimated_delivery_at": "2020-01-06T00:00:00Z",
        "shipping_limits": [{"shipping_limit_at": "2020-01-03T00:00:00Z"}], "events": [],
    })
    with pytest.raises(GroundingError, match="SHIPMENT_DELAY_CONTRADICTED"):
        build_grounded_output(output, case, ledger)


@pytest.mark.parametrize("exception", ["late_event", "late_version", "missing_date"])
def test_lateness_is_not_overruled_when_sources_incomplete_or_conflicting(exception):
    output, case, ledger = fixture()
    output["shipment_analysis"]["verdict"] = "logistics_delay"
    data = ledger["ev_shipment"]["data"]
    data.update({"delivered_customer_at": "2020-01-05T00:00:00Z",
                 "estimated_delivery_at": "2020-01-06T00:00:00Z"})
    if exception == "late_event":
        data["events"] = [{"event_type": "delivered_late", "actor": "logistics_provider"}]
    elif exception == "late_version":
        ledger["ev_history"] = {"domain": "customer", "data": {"orders": [{
            "order_id": "order-a", "order_delivered_customer_date": "2020-02-10T00:00:00Z",
            "order_estimated_delivery_date": "2020-02-06T00:00:00Z"}]}}
    else:
        data["delivered_customer_at"] = None
    assert build_grounded_output(output, case, ledger)["shipment_analysis"]["verdict"] == (
        "logistics_delay")


def test_refund_failure_requires_business_event_not_transport_failure():
    output, case, ledger = fixture()
    output["payment_analysis"]["verdict"] = "refund_failed"
    ledger["ev_error"] = {"domain": "refund", "data": {"order_id": "order-a",
        "status": "tool_execution_failed", "events": []}}
    with pytest.raises(GroundingError, match="REFUND_FAILURE_UNWITNESSED"):
        build_grounded_output(output, case, ledger)
    ledger["ev_error"]["data"]["events"] = [
        {"event_type": "refund_requested", "status": "failed"}]
    assert build_grounded_output(output, case, ledger)["payment_analysis"]["verdict"] == (
        "refund_failed")


def test_refund_event_for_another_order_does_not_justify_verdict():
    output, case, ledger = fixture()
    output["payment_analysis"]["verdict"] = "refund_failed"
    ledger["ev_other"] = {"domain": "refund", "data": {"order_id": "order-b",
        "events": [{"event_type": "refund_requested", "status": "failed"}]}}
    with pytest.raises(GroundingError, match="REFUND_FAILURE_UNWITNESSED"):
        build_grounded_output(output, case, ledger)


def test_policy_version_mismatch_leaves_actions_and_money_unchanged():
    output, case, ledger = fixture()
    ledger["ev_policy"]["data"]["policy_version"] = "OTHER_POLICY"
    result = build_grounded_output(output, case, ledger)
    assert result["resolution_actions"] == output["resolution_actions"]
    assert result["financial_resolution"] == output["financial_resolution"]


def test_conservative_zero_refund_does_not_acquire_money_moving_policy_action():
    output, case, ledger = fixture()
    ledger["ev_policy"]["data"]["rules"]["payment_mismatch"]["recommended_action"] = "issue_refund"
    result = build_grounded_output(output, case, ledger)
    assert result["resolution_actions"] == []
    assert result["assessment"] == output["assessment"]
    assert result["financial_resolution"] == output["financial_resolution"]
    output["financial_resolution"]["recommended_refund_brl"] = 12
    output["assessment"]["case_status"] = "action_required"
    assert build_grounded_output(output, case, ledger)["resolution_actions"] == ["issue_refund"]


def test_complete_ontime_logistics_versions_are_checked_together():
    output, case, ledger = fixture()
    output["shipment_analysis"]["verdict"] = "logistics_delay"
    ledger["ev_history"] = {"domain": "customer", "data": {"orders": [
        {"order_id": "order-a", "order_delivered_customer_date": "2020-01-05T00:00:00Z",
         "order_estimated_delivery_date": "2020-01-06T00:00:00Z"},
        {"order_id": "order-a", "order_delivered_customer_date": "2020-02-05T00:00:00Z",
         "order_estimated_delivery_date": "2020-02-06T00:00:00Z"}]}}
    with pytest.raises(GroundingError, match="SHIPMENT_DELAY_CONTRADICTED"):
        build_grounded_output(output, case, ledger)


def test_conflicting_policy_actions_are_not_selected_arbitrarily():
    output, case, ledger = fixture()
    ledger["ev_policy_other"] = deepcopy(ledger["ev_policy"])
    ledger["ev_policy_other"]["data"]["rules"]["payment_mismatch"][
        "recommended_action"] = "monitor_refund"
    with pytest.raises(GroundingError, match="POLICY_ACTION_CONFLICT"):
        build_grounded_output(output, case, ledger)


def test_incomplete_seller_version_prevents_false_contradiction():
    output, case, ledger = fixture()
    output["shipment_analysis"]["verdict"] = "seller_delay"
    ledger["ev_shipment"]["data"].update({
        "delivered_carrier_at": "2020-01-02T00:00:00Z",
        "shipping_limits": [{"shipping_limit_at": "2020-01-03T00:00:00Z"}],
    })
    ledger["ev_history"] = {"domain": "customer", "data": {"orders": [
        {"order_id": "order-a", "order_delivered_carrier_date": "2020-02-05T00:00:00Z"}]}}
    assert build_grounded_output(output, case, ledger)["shipment_analysis"]["verdict"] == (
        "seller_delay")
