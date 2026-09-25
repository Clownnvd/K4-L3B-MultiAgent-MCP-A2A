from copy import deepcopy

from student_agent.decision_support import derive_decision_support


def test_plan_context_exposes_support_from_evidence_not_claim_topic():
    from student_agent.decision_plan import prepare_plan_context
    from test_decision_plan import fixture

    payload = fixture()
    for claim in payload["case"]["customer_request"]["claims"]:
        claim["topic"] = "unsupported_claim"
    for e in payload["evidence"].values():
        if e["domain"] == "payment":
            for event in e["data"]["events"]:
                event["event_at"] = "2020-01-02T00:00:00Z"
    context = prepare_plan_context(payload)
    assert any(s["issue"] == "canceled_order_paid" for s in context["decision_support"]["signals"])


def sample():
    return {
        "entity_resolution": {"resolved_order_ids": ["order-a"]},
        "source_facts": {
            "candidate_interpretations": [
                {"order_id": "order-a", "purchase_timestamp": "2020-01-01T00:00:00Z"}
            ]
        },
        "order_versions": [
            {
                "id": "v1",
                "fields": {
                    "purchase_timestamp": [{"value": "2020-01-01T00:00:00Z"}],
                    "order_status": [{"value": "delivered"}],
                },
            }
        ],
        "capture_choices": [
            {"id": "c1", "order_id": "order-a", "event_at": "2020-01-02T00:00:00Z", "value": 60},
            {"id": "c2", "order_id": "order-a", "event_at": "2020-01-02T01:00:00Z", "value": 40},
        ],
        "payment_events": [],
        "refund_events": [],
        "payment_records": [
            {
                "order_id": "order-a",
                "payment_sequential": "1",
                "payment_type": "card",
                "payment_value": 60,
            },
            {
                "order_id": "order-a",
                "payment_sequential": "2",
                "payment_type": "voucher",
                "payment_value": 40,
            },
        ],
    }, {
        "item-ref": {
            "domain": "item",
            "data": [
                {"order_id": "order-a", "order_item_id": "i1", "price": 90, "freight_value": 10}
            ],
        }
    }


def test_distinct_payment_methods_reconcile_without_claim_labels():
    context, ledger = sample()
    before = deepcopy((context, ledger))
    result = derive_decision_support(context, ledger)
    signal = next(s for s in result["signals"] if s["issue"] == "valid_split_payment")
    assert signal["capture_ids"] == ["c1", "c2"]
    assert before == (context, ledger)


def test_item_version_disagreement_does_not_establish_split():
    context, ledger = sample()
    ledger["item-ref"]["data"].append(
        {"order_id": "order-a", "order_item_id": "i1", "price": 100, "freight_value": 10}
    )
    assert not derive_decision_support(context, ledger)["signals"]


def test_latest_refund_status_requires_real_current_period_event():
    context, ledger = sample()
    context["capture_choices"] = []
    context["refund_events"] = [
        {
            "order_id": "order-a",
            "event_type": "refund_requested",
            "status": "pending",
            "event_at": "2020-01-03T00:00:00Z",
            "evidence_ref": "actual-ref",
            "event_pointer": "/events/0",
        }
    ]
    assert derive_decision_support(context, ledger)["signals"][0]["issue"] == "refund_pending"
    context["refund_events"][0]["event_at"] = "2019-12-01T00:00:00Z"
    assert not derive_decision_support(context, ledger)["signals"]
