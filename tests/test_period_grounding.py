import pytest

from student_agent.decision_plan import compile_plan, prepare_plan_context
from test_decision_plan import fixture, make_plan


def test_future_purchases_excluded_but_later_events_retained_for_episode_binding():
    payload = fixture()
    for entry in payload["evidence"].values():
        if entry["domain"] == "payment":
            entry["data"]["events"][0]["event_at"] = payload["case"]["opened_at"]
            entry["data"]["events"][1]["event_at"] = "2020-02-01T00:00:00Z"
            entry["data"]["payments"] = [{"payment_type": "voucher", "payment_value": "80.00"}]
        if entry["domain"] == "order":
            entry["data"]["order_purchase_timestamp"] = "2020-02-01T00:00:00Z"
        if entry["domain"] == "refund":
            entry["data"]["events"][0]["event_at"] = "2020-02-01T00:00:00Z"
    context = prepare_plan_context(payload)
    assert context["order_versions"] == []
    assert len(context["capture_choices"]) == 2
    assert context["capture_choices"][0]["value"] == "80.00"
    assert len(context["refund_choices"]) == 1
    assert len(context["excluded_future"]["order_versions"]) == 1
    assert any(row["after_case_opened"] is True for row in context["refund_events"])
    assert context["source_facts"]["order_observations"]
    assert context["payment_records"][0]["payment_type"] == "voucher"
    assert context["payment_records"][0]["pointer"] == "/payments/0"


def test_missing_timezone_or_timestamp_is_unknown_not_silently_filtered():
    payload = fixture()
    for entry in payload["evidence"].values():
        if entry["domain"] == "payment":
            entry["data"]["events"][0]["event_at"] = "2022-01-01T00:00:00"
        if entry["domain"] == "order":
            entry["data"]["order_purchase_timestamp"] = "2022-01-01T00:00:00"
    context = prepare_plan_context(payload)
    assert len(context["capture_choices"]) == 2
    assert len(context["order_versions"]) == 1
    assert all(row["after_case_opened"] is None for row in context["capture_choices"])


def test_capture_before_selected_purchase_is_rejected():
    payload = fixture()
    for entry in payload["evidence"].values():
        if entry["domain"] == "payment":
            entry["data"]["events"][0]["event_at"] = "2019-12-31T00:00:00Z"
    with pytest.raises(ValueError, match="before selected purchase"):
        compile_plan(make_plan(payload), payload)


def test_refund_failure_after_opening_can_describe_same_purchase_episode():
    payload = fixture()
    for entry in payload["evidence"].values():
        if entry["domain"] == "refund":
            entry["data"]["events"][1]["event_at"] = "2020-02-01T00:00:00Z"
    plan = make_plan(payload)
    plan["primary_issue"] = "refund_failed"
    assert compile_plan(plan, payload)["output"]["assessment"]["primary_issue"] == (
        "refund_failed"
    )
