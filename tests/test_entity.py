from copy import deepcopy

from student_agent.entity import resolve_from_sources


def fixture():
    case = {"candidate_order_ids": ["order-a", "unreadable-placeholder"],
            "customer_unique_id_hint": "customer-a",
            "customer_request": {"claimed_order_id": "order-a"}}
    order = {"evidence_ref": "ev_order", "domain": "order", "data": {
        "order_id": "order-a", "order_status": "canceled", "date": "2020-01-01"}}
    history = {"evidence_ref": "ev_customer", "domain": "customer", "data": {
        "customer_unique_id": "customer-a", "orders": [
            {"order_id": "order-a", "order_status": "canceled", "date": "2020-01-01"},
            {"order_id": "order-a", "order_status": "delivered", "date": "2020-02-01"}]}}
    return case, {"order-a": order}, {"ev_order": order, "ev_customer": history}


def test_one_received_customer_linked_identity_resolves_despite_fact_versions():
    case, orders, ledger = fixture()
    original = deepcopy((case, orders, ledger))
    assert resolve_from_sources(case, orders, ledger) == {
        "status": "resolved", "resolved_order_ids": ["order-a"],
        "rejected_candidates": [], "confidence": 0.95,
    }
    assert (case, orders, ledger) == original


def test_wrong_customer_hint_and_missing_hint_require_fallback():
    case, orders, ledger = fixture()
    case["customer_unique_id_hint"] = "different-customer"
    assert resolve_from_sources(case, orders, ledger) is None
    case.pop("customer_unique_id_hint")
    assert resolve_from_sources(case, orders, ledger) is None


def test_multiple_readable_customer_linked_candidates_require_fallback():
    case, orders, ledger = fixture()
    case["candidate_order_ids"].append("order-b")
    orders["order-b"] = {"evidence_ref": "ev_second", "domain": "order", "data": {
        "order_id": "order-b"}}
    ledger["ev_second"] = orders["order-b"]
    ledger["ev_customer"]["data"]["orders"].append({"order_id": "order-b"})
    assert resolve_from_sources(case, orders, ledger) is None


def test_missing_history_requires_fallback():
    case, orders, ledger = fixture()
    ledger.pop("ev_customer")
    assert resolve_from_sources(case, orders, ledger) is None


def test_unreceived_or_changed_order_evidence_requires_fallback():
    case, orders, ledger = fixture()
    ledger.pop("ev_order")
    assert resolve_from_sources(case, orders, ledger) is None
    ledger["ev_order"] = deepcopy(orders["order-a"])
    ledger["ev_order"]["data"]["order_id"] = "forged-order"
    assert resolve_from_sources(case, orders, ledger) is None


def test_out_of_scope_order_and_history_ref_mismatch_are_not_accepted():
    case, orders, ledger = fixture()
    case["candidate_order_ids"] = []
    case["customer_request"] = {}
    assert resolve_from_sources(case, orders, ledger) is None
    case["candidate_order_ids"] = ["order-a"]
    ledger["ev_customer"]["evidence_ref"] = "not-received"
    assert resolve_from_sources(case, orders, ledger) is None


def test_conflicting_customer_history_membership_requires_fallback():
    case, orders, ledger = fixture()
    ledger["ev_conflict"] = {"evidence_ref": "ev_conflict", "domain": "customer", "data": {
        "customer_unique_id": "different-customer", "orders": [{"order_id": "order-a"}]}}
    assert resolve_from_sources(case, orders, ledger) is None


def test_tool_failure_is_not_a_readable_order():
    case, orders, ledger = fixture()
    orders["order-a"] = {"status": "tool_execution_failed", "data": None}
    assert resolve_from_sources(case, orders, ledger) is None
