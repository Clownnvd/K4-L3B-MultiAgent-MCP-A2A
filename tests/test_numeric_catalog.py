import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from student_agent.arithmetic import pointer_get
from student_agent.model_adapter import (
    ModelSettings,
    OpenAICompatibleModel,
    build_response_schema,
    generation_schema,
    numeric_source_catalog,
)

ROOT = Path(__file__).resolve().parents[1]


def ledger():
    return {"ev_payment": {"domain": "payment", "data": {
        "payments": [{"payment_value": "12.50"}, {"payment_value": 7}],
        "a/b~c": 3.25, "valid": True, "empty": None, "text": "not money",
        "nan": float("nan"), "inf": "Infinity", "false": False}},
        "ev_refund": {"domain": "refund", "data": {"amount": "2.50"}}}


def payload(evidence):
    return {"evidence": evidence, "output_schemas": {
        path.name: json.loads(path.read_text())
        for path in (ROOT / "contracts/schemas").glob("*output-v2.schema.json")}}


def test_catalog_preserves_values_escapes_keys_and_excludes_non_numbers():
    evidence = ledger()
    rows = numeric_source_catalog(evidence)
    assert [(row["evidence_ref"], row["pointer"], row["value"]) for row in rows] == [
        ("ev_payment", "/payments/0/payment_value", "12.50"),
        ("ev_payment", "/payments/1/payment_value", 7),
        ("ev_payment", "/a~1b~0c", 3.25), ("ev_refund", "/amount", "2.50")]
    for row in rows:
        assert pointer_get(evidence[row["evidence_ref"]]["data"], row["pointer"]) == row["value"]
        assert row["domain"] == evidence[row["evidence_ref"]]["domain"]


def test_operand_schema_rejects_nonexistent_or_wrong_reference_pointer_pairs():
    schema = build_response_schema(payload(ledger()), "decide_policy")
    operands = schema["properties"]["calculations"]["items"]["properties"]["operands"]
    validator = Draft202012Validator(operands)
    valid = {"evidence_ref": "ev_payment", "pointer": "/payments/0/payment_value"}
    assert validator.is_valid([valid])
    for ref, pointer in [("ev_refund", valid["pointer"]),
                         ("ev_payment", "/data/payments/0/payment_value"),
                         ("ev_payment", "/payments/2/payment_value")]:
        assert not validator.is_valid([{"evidence_ref": ref, "pointer": pointer}])
    assert not validator.is_valid([valid, valid])
    assert "uniqueItems" not in json.dumps(generation_schema(schema))


def test_no_numeric_sources_permits_only_zero_with_empty_operands():
    schema = build_response_schema(payload({}), "decide_policy")
    calc = schema["properties"]["calculations"]["items"]
    validator = Draft202012Validator(calc)
    output = {"target": "/financial_resolution/recommended_refund_brl",
              "operation": "zero", "operands": []}
    assert validator.is_valid(output)
    output["operation"] = "sum"
    assert not validator.is_valid(output)


def test_request_enriches_prompt_without_mutating_caller():
    original = payload({"ev_payment": {"domain": "payment", "data": {"paid": "12.50"}}})
    preserved = deepcopy(original)

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}

    class Client:
        async def post(self, url, *, json: dict, headers):
            message = __import__("json").loads(json["messages"][1]["content"])["payload"]
            assert message["numeric_source_catalog"] == [
                {"evidence_ref": "ev_payment", "pointer": "/paid",
                 "value": "12.50", "domain": "payment"}]
            assert "zero" in message["numeric_source_rules"]
            return Response()

    settings = ModelSettings("Qwen/Qwen3.5-9B", "test", "http://localhost/v1", "")
    with pytest.raises(ValueError, match="schema"):
        asyncio.run(OpenAICompatibleModel(settings, Client()).complete("decide_policy", original))
    assert original == preserved
