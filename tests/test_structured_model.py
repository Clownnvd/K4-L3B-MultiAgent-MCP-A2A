import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from student_agent.model_adapter import (
    ModelSettings,
    OpenAICompatibleModel,
    build_response_schema,
    generation_schema,
)

ROOT = Path(__file__).resolve().parents[1]


def test_generation_schema_omits_unsupported_unique_items_only():
    source = {"type": "array", "uniqueItems": True,
              "items": {"type": "object", "properties": {"xs": {
                  "type": "array", "uniqueItems": True, "maxItems": 3}}}}
    generated = generation_schema(source)
    assert "uniqueItems" not in json.dumps(generated)
    assert generated["items"]["properties"]["xs"]["maxItems"] == 3
    assert source["uniqueItems"] is True


def schemas():
    return {path.name: json.loads(path.read_text()) for path in
            (ROOT / "contracts/schemas").glob("*output-v2.schema.json")}


def test_policy_schema_requires_wrapper_and_inlines_official_contract():
    schema = build_response_schema({"output_schemas": schemas()}, "decide_policy")
    Draft202012Validator.check_schema(schema)
    assert schema["required"] == ["output", "calculations"]
    assert schema["additionalProperties"] is False
    assert "$ref" not in json.dumps(schema)
    official = schema["properties"]["output"]
    assert "case_id" in official["required"]
    assert official["properties"]["schema_version"]["const"] == "day09-l3b-output-v2"
    calculation = schema["properties"]["calculations"]["items"]
    assert set(calculation["required"]) == {"target", "operation", "operands"}
    operand = calculation["properties"]["operands"]["items"]
    assert set(operand["required"]) == {"evidence_ref", "pointer"}


def test_entity_schema_excludes_unreadable_ids():
    schema = build_response_schema({"allowed_ids_in_either_result_list": ["known-order"],
                                    "unreadable_candidates": ["missing-order"]}, "resolve_entity")
    validator = Draft202012Validator(schema)
    output = {"status": "resolved", "resolved_order_ids": ["known-order"],
              "rejected_candidates": [], "confidence": 1}
    assert validator.is_valid(output)
    output["resolved_order_ids"] = ["missing-order"]
    assert not validator.is_valid(output)
    output["resolved_order_ids"] = ["known-order"]
    output["rejected_candidates"] = ["missing-order"]
    assert not validator.is_valid(output)


@pytest.mark.parametrize("content", ['{"case_id":"CASE_001"}', '[]', '{"output":{}}'])
def test_model_sends_schema_and_rejects_bypassed_flat_or_malformed_output(content):
    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}

    class Client:
        async def post(self, url, *, json, headers):
            fmt = json["response_format"]
            assert fmt["type"] == "json_schema"
            assert fmt["json_schema"]["strict"] is True
            assert fmt["json_schema"]["schema"]["required"] == ["output", "calculations"]
            assert json["chat_template_kwargs"] == {"enable_thinking": False}
            return Response()

    settings = ModelSettings("Qwen/Qwen3.5-9B", "Qwen/Qwen3.5-9B", "http://localhost/v1", "")
    with pytest.raises(ValueError):
        asyncio.run(OpenAICompatibleModel(settings, Client()).complete(
            "decide_policy", {"output_schemas": schemas()}))


def test_connectivity_probe_retains_json_object_mode():
    assert build_response_schema({}, "connectivity_probe") is None


@pytest.mark.parametrize("tool,unknown", [
    ("get_refund_timeline", "refunded_total_brl"),
    ("get_payment_timeline", "captured_total_brl"),
])
def test_schema_preserves_resolution_and_existing_missing_money_invariants(tool, unknown):
    resolution = {"status": "resolved", "resolved_order_ids": ["order-1"],
                  "rejected_candidates": [], "confidence": 1}
    payload = {"output_schemas": schemas(), "entity_resolution": resolution,
               "tool_failures": [{"tool_name": tool, "arguments": {"order_id": "order-1"}}]}
    schema = build_response_schema(payload, "decide_policy")
    Draft202012Validator.check_schema(schema)
    properties = schema["properties"]["output"]["properties"]
    assert properties["entity_resolution"]["const"] == resolution
    payment = properties["payment_analysis"]["properties"]
    assert payment[unknown]["const"] is None
    assert payment["refundable_total_brl"]["const"] is None
    finance = properties["financial_resolution"]["properties"]
    assert finance["recommended_refund_brl"]["const"] == 0
    assert finance["refund_lines"]["maxItems"] == 0
    assert properties["assessment"]["properties"]["case_status"]["const"] == "needs_investigation"
    assert "const" not in payload["output_schemas"]["l3b-output-v2.schema.json"][
        "properties"]["payment_analysis"]["properties"][unknown]
