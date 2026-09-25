from copy import deepcopy

from student_agent.facts import source_fact_state


def test_versions_keep_conflicts_and_only_suggest_latest_eligible_purchase():
    case = {"opened_at": "2020-02-15T00:00:00Z", "candidate_order_ids": ["unwitnessed"]}
    earlier = {"order_id": "order-1", "order_purchase_timestamp": "2020-01-01T00:00:00Z",
               "order_status": "canceled", "order_delivered_customer_date": None}
    future = {"order_id": "order-1", "order_purchase_timestamp": "2020-03-01T00:00:00Z",
              "order_status": "delivered", "order_delivered_customer_date": "2020-03-10T00:00:00Z",
              "order_estimated_delivery_date": "2020-03-11T00:00:00Z"}
    ledger = {"ev_old": {"domain": "customer", "data": {"orders": [earlier]}},
              "ev_new": {"domain": "order", "data": future}}
    original = deepcopy(ledger)
    state = source_fact_state(case, ledger)
    assert state["allowed_entity_ids"]["order_ids"] == ["order-1"]
    assert [row["purchase_after_case_opened"] for row in state["order_observations"]] == [
        False, True]
    status_conflict = next(c for c in state["data_conflicts"] if c["field"] == "order_status")
    assert set(status_conflict["sources"]) == {"ev_old", "ev_new"}
    assert status_conflict["selected_source"] is None
    assert {o["pointer"] for o in status_conflict["observations"]} == {
        "/orders/0/order_status", "/order_status"}
    candidate = state["candidate_interpretations"][0]
    assert candidate["suggestion_only"] is True
    assert candidate["purchase_timestamp"] == earlier["order_purchase_timestamp"]
    assert ledger == original


def test_ids_come_only_from_matching_data_fields_not_refs_or_case_hints():
    ledger = {"ev_reference": {"domain": "item", "data": {
        "a/b~c": {"order_id": "order-1", "order_item_id": "item-1", "seller_id": "seller-1",
                   "payment_reference": "payment-1", "shipment_id": "shipment-1"},
        "items": [{"item_id": "item-2"}, {"item_id": "ev_fake"}],
        "payment_id": "payment-2", "evidence_ref": "ev_unrelated", "text": "order-in-prose"}}}
    state = source_fact_state({"candidate_order_ids": ["unwitnessed"]}, ledger)
    assert state["allowed_entity_ids"] == {
        "order_ids": ["order-1"], "item_ids": ["item-1", "item-2"],
        "seller_ids": ["seller-1"], "payment_references": ["payment-1", "payment-2"],
        "shipment_ids": ["shipment-1"]}
    assert state["entity_sources"]["order_ids"]["order-1"][0]["pointer"] == "/a~1b~0c/order_id"


def test_delivery_comparisons_are_record_local_missing_dates_stay_unknown():
    ledger = {"ev_history": {"domain": "customer", "data": {"orders": [
        {"order_id": "o", "order_purchase_timestamp": "2020-01-01T00:00:00Z",
         "order_delivered_customer_date": "2020-01-02T00:00:00Z",
         "order_estimated_delivery_date": "2020-01-03T00:00:00Z"},
        {"order_id": "o", "order_purchase_timestamp": "2020-02-01T00:00:00Z",
         "order_delivered_customer_date": "2020-02-02T00:00:00Z",
         "order_estimated_delivery_date": "2020-02-03T00:00:00Z"},
        {"order_id": "missing", "order_status": "delivered"}]}},
        "ev_shipment": {"domain": "shipment", "data": {
            "order_id": "o", "delivered_carrier_at": "2020-01-04T00:00:00Z",
            "shipping_limits": [{"seller_id": "s", "shipping_limit_at": "2020-01-03T00:00:00Z"}]}}}
    state = source_fact_state({}, ledger)
    assert [row["delivery_comparison"]["verdict"] for row in state["order_observations"]] == [
        "on_time", "on_time", "unknown", "unknown"]
    shipment = state["order_observations"][-1]
    assert shipment["handoff_comparisons"][0]["verdict"] == "late"
    assert shipment["handoff_comparisons"][0]["deadline_source"]["pointer"] == (
        "/shipping_limits/0/shipping_limit_at")
    assert state["candidate_interpretations"] == []
    assert all(row["purchase_after_case_opened"] is None for row in state["order_observations"])
    conflict = next(c for c in state["data_conflicts"] if c["field"] == "purchase_timestamp")
    assert conflict["sources"] == ["ev_history"]
    assert len({row["pointer"] for row in conflict["observations"]}) == 2


def test_contradictory_latest_version_and_unknown_timezone_do_not_get_selected():
    case = {"opened_at": "2020-02-01T00:00:00Z"}
    ledger = {"ev_history": {"domain": "customer", "data": {"orders": [
        {"order_id": "o", "order_purchase_timestamp": "2020-01-01T00:00:00Z",
         "order_status": "canceled"},
        {"order_id": "o", "order_purchase_timestamp": "2020-01-01T00:00:00Z",
         "order_status": "delivered"},
        {"order_id": "unknown", "order_purchase_timestamp": "2020-01-01",
         "order_delivered_customer_date": "invalid", "shipping_limits": None}]}}}
    state = source_fact_state(case, ledger)
    assert state["candidate_interpretations"] == []
    unknown = state["order_observations"][-1]
    assert unknown["purchase_after_case_opened"] is None
    assert unknown["delivery_comparison"]["verdict"] == "unknown"
