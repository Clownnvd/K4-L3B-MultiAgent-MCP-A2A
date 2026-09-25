"""Synthetic source-bound arithmetic checks; no model or gateway calls."""

from copy import deepcopy
from pathlib import Path

import pytest

from student_agent.arithmetic import materialize_calculations, verify_calculations
from student_agent.contracts import Contracts
from student_agent.verifier import verify_output
from student_agent.workflow import Orchestrator

REF = "ev_" + "a" * 24


@pytest.fixture
def proposal():
    output = {
        "schema_version": "day09-l3b-output-v2",
        "case_id": "L3B_CASE_001",
        "assessment": {
            "primary_issue": "canceled_order_paid",
            "secondary_issues": [],
            "case_status": "action_required",
            "confidence": 0.8,
        },
        "affected_entities": {
            "order_ids": ["ord-1"],
            "item_ids": [],
            "seller_ids": [],
            "payment_references": [],
            "shipment_ids": [],
        },
        "claim_assessments": [
            {
                "claim_id": "claim-1",
                "verdict": "supported",
                "confidence": 0.8,
                "evidence_refs": [REF],
            }
        ],
        "entity_resolution": {
            "status": "resolved",
            "resolved_order_ids": ["ord-1"],
            "rejected_candidates": [],
            "confidence": 0.9,
        },
        "customer_context": {"customer_unique_id": None, "related_order_ids": []},
        "shipment_analysis": {
            "verdict": "insufficient_evidence",
            "late_seller_ids": [],
            "timeline_complete": False,
        },
        "payment_analysis": {
            "verdict": "reconciled",
            "captured_total_brl": 999,
            "refunded_total_brl": 999,
            "refundable_total_brl": 999,
        },
        "root_cause_analysis": {"ranked_causes": [], "responsible_parties": []},
        "evidence_refs": [REF],
        "data_conflicts": [],
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": 999,
            "refund_lines": [
                {"reason_code": "canceled_order_paid", "amount_brl": 999, "entity_id": "ord-1"}
            ],
        },
        "resolution_actions": ["issue_refund"],
    }
    ledger = {
        REF: {
            "data": {
                "captured": "100.00",
                "refunded": "20.00",
                "excess": "90.00",
                "events": [{"amount": "100.00"}],
            }
        }
    }

    def calc(target, operation, *pointers):
        return {
            "target": target,
            "operation": operation,
            "operands": [{"evidence_ref": REF, "pointer": p} for p in pointers],
        }

    calculations = [
        calc("/payment_analysis/captured_total_brl", "sum", "/captured"),
        calc("/payment_analysis/refunded_total_brl", "sum", "/refunded"),
        calc("/payment_analysis/refundable_total_brl", "remaining", "/captured", "/refunded"),
        calc("/financial_resolution/recommended_refund_brl", "subtract", "/captured", "/refunded"),
        calc(
            "/financial_resolution/refund_lines/0/amount_brl", "remaining", "/captured", "/refunded"
        ),
    ]
    return output, calculations, ledger


def test_materialization_changes_only_declared_money_without_mutating_inputs(proposal):
    snapshot = deepcopy(proposal)
    output, calculations, ledger = proposal
    with pytest.raises(ValueError, match="mismatch"):
        verify_calculations(output, calculations, ledger)
    result = materialize_calculations(*proposal)
    assert proposal == snapshot
    expected = deepcopy(output)
    expected["payment_analysis"].update(
        captured_total_brl=100, refunded_total_brl=20, refundable_total_brl=80
    )
    expected["financial_resolution"]["recommended_refund_brl"] = 80
    expected["financial_resolution"]["refund_lines"][0]["amount_brl"] = 80
    assert result == expected
    contracts = Contracts(Path(__file__).resolve().parents[1] / "contracts/schemas")
    contracts.validate_output(result, "synthetic")
    verify_calculations(result, calculations, ledger)
    verify_output(result["case_id"], result, ledger)
    Orchestrator._check_missing_financial_evidence(result, [])
    Orchestrator._check_claims({"customer_request": {"claims": [{"claim_id": "claim-1"}]}}, result)


@pytest.mark.parametrize(
    "pointer",
    [
        "/missing",
        "/data/captured",
        "/events/-1/amount",
        "/events/00/amount",
        "/events/+0/amount",
        "/captured~2",
    ],
)
def test_invalid_source_pointers_fail_without_source_guessing(proposal, pointer):
    proposal[1][0]["operands"][0]["pointer"] = pointer
    snapshot = deepcopy(proposal)
    with pytest.raises(ValueError):
        materialize_calculations(*proposal)
    assert proposal == snapshot


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "uncited",
        "duplicate_operand",
        "duplicate_target",
        "literal",
        "forbidden_target",
        "unbound",
    ],
)
def test_unsafe_proposals_are_rejected(proposal, mutation):
    output, calculations, _ = proposal
    if mutation == "unknown":
        calculations[0]["operands"][0]["evidence_ref"] = "ev_unknown"
    elif mutation == "uncited":
        output["evidence_refs"] = []
    elif mutation == "duplicate_operand":
        calculations[0]["operands"] *= 2
    elif mutation == "duplicate_target":
        calculations.append(deepcopy(calculations[0]))
    elif mutation == "literal":
        calculations[0]["operands"] = [{"value": 100}]
    elif mutation == "forbidden_target":
        calculations[0]["target"] = "/assessment/confidence"
    else:
        calculations.pop()
    with pytest.raises(ValueError):
        materialize_calculations(*proposal)


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "-Infinity"])
def test_missing_boolean_and_nonfinite_source_values_fail(proposal, value):
    proposal[2][REF]["data"]["captured"] = value
    with pytest.raises(ValueError):
        materialize_calculations(*proposal)


def test_over_balance_refund_still_fails_business_verification(proposal):
    for index in [3, 4]:
        proposal[1][index].update(
            operation="sum", operands=[{"evidence_ref": REF, "pointer": "/excess"}]
        )
    result = materialize_calculations(*proposal)
    with pytest.raises(ValueError, match="exceeds refundable balance"):
        verify_output(result["case_id"], result, proposal[2])


def test_missing_refund_history_still_rejects_nonnull_totals(proposal):
    result = materialize_calculations(*proposal)
    failures = [{"tool_name": "get_refund_timeline", "arguments": {"order_id": "ord-1"}}]
    with pytest.raises(ValueError, match="null financial totals"):
        Orchestrator._check_missing_financial_evidence(result, failures)


def test_rounding_and_zero_minimum_operations(proposal):
    proposal[2][REF]["data"]["captured"] = "100.005"
    assert materialize_calculations(*proposal)["payment_analysis"]["captured_total_brl"] == 100.01
    proposal[1][1].update(operation="zero", operands=[])
    proposal[1][2]["operation"] = "minimum"
    result = materialize_calculations(*proposal)
    assert result["payment_analysis"]["refunded_total_brl"] == 0
    assert result["payment_analysis"]["refundable_total_brl"] == 20


def test_remaining_floors_at_zero_but_subtract_cannot_materialize_negative_money(proposal):
    proposal[2][REF]["data"]["refunded"] = "120"
    with pytest.raises(ValueError, match="Negative"):
        materialize_calculations(*proposal)
    proposal[1][3]["operation"] = "remaining"
    result = materialize_calculations(*proposal)
    assert result["financial_resolution"]["recommended_refund_brl"] == 0


@pytest.mark.parametrize(
    "operation,operands",
    [
        ("eval", []),
        ("sum", []),
        ("subtract", []),
        ("remaining", []),
        ("minimum", []),
        ("zero", [{"evidence_ref": REF, "pointer": "/captured"}]),
    ],
)
def test_unsupported_operations_and_arities_are_rejected(proposal, operation, operands):
    proposal[1][0].update(operation=operation, operands=operands)
    with pytest.raises(ValueError, match="operation or arity"):
        materialize_calculations(*proposal)


@pytest.mark.parametrize("amount", ["9007199254740993.01", "1e100"])
def test_decimal_values_that_cannot_be_represented_safely_fail(proposal, amount):
    proposal[2][REF]["data"]["captured"] = amount
    with pytest.raises(ValueError, match="precision"):
        materialize_calculations(*proposal)


def test_real_escaped_dictionary_keys_are_resolved_without_aliases(proposal):
    proposal[2][REF]["data"]["cap/tured~"] = "100.00"
    proposal[1][0]["operands"][0]["pointer"] = "/cap~1tured~0"
    result = materialize_calculations(*proposal)
    assert result["payment_analysis"]["captured_total_brl"] == 100


def net_proposal(proposal, refunded="10.00"):
    output, calculations, ledger = proposal
    ledger[REF]["data"].update(capture_a="44.50", capture_b="44.50", refund_net=refunded)
    net = calculations.pop(2)
    net.update(
        operation="net",
        positive_count=2,
        operands=[
            {"evidence_ref": REF, "pointer": pointer}
            for pointer in ("/capture_a", "/capture_b", "/refund_net")
        ],
    )
    calculations.insert(0, net)
    return output, calculations, ledger


@pytest.mark.parametrize("refunded,expected", [("10.00", 79), ("100.00", 0)])
def test_net_sums_capture_sources_and_subtracts_refund_sources(proposal, refunded, expected):
    net_proposal(proposal, refunded)
    snapshot = deepcopy(proposal)
    result = materialize_calculations(*proposal)
    assert result["payment_analysis"]["refundable_total_brl"] == expected
    verify_calculations(result, proposal[1], proposal[2])
    assert proposal == snapshot


@pytest.mark.parametrize("count", [0, -1, 4, True, False, 1.5, "2", None])
@pytest.mark.parametrize("function", [materialize_calculations, verify_calculations])
def test_net_rejects_invalid_structural_positive_count(proposal, count, function):
    net_proposal(proposal)
    proposal[1][0]["positive_count"] = count
    with pytest.raises(ValueError, match="positive_count"):
        function(*proposal)


@pytest.mark.parametrize("function", [materialize_calculations, verify_calculations])
@pytest.mark.parametrize("mutation", ["net_missing_count", "net_literal", "sum_extra_count"])
def test_net_and_other_operation_fields_are_exact(proposal, function, mutation):
    if mutation.startswith("net"):
        net_proposal(proposal)
        if mutation == "net_missing_count":
            proposal[1][0].pop("positive_count")
        else:
            proposal[1][0]["literal"] = 10
    else:
        proposal[1][0]["positive_count"] = 1
    with pytest.raises(ValueError, match="fields"):
        function(*proposal)


def test_net_can_sum_all_operands_without_negative_sources(proposal):
    net_proposal(proposal)
    proposal[1][0]["positive_count"] = 3
    result = materialize_calculations(*proposal)
    assert result["payment_analysis"]["refundable_total_brl"] == 99
    verify_calculations(result, proposal[1], proposal[2])


@pytest.mark.parametrize("function", [materialize_calculations, verify_calculations])
@pytest.mark.parametrize("mutation", ["duplicate", "alias", "unknown"])
def test_net_preserves_source_validation(proposal, function, mutation):
    net_proposal(proposal)
    operands = proposal[1][0]["operands"]
    if mutation == "duplicate":
        operands[1] = deepcopy(operands[0])
    elif mutation == "alias":
        operands[0]["pointer"] = "/events/00/amount"
    else:
        operands[0]["evidence_ref"] = "ev_unknown"
    with pytest.raises(ValueError):
        function(*proposal)
