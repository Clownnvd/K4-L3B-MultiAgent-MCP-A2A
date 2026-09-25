import asyncio
from types import SimpleNamespace

import pytest

from student_agent.mcp_gateway import EvidenceGateway


class ContractsStub:
    def validate_evidence(self, evidence, label):
        assert evidence == {"domain": "order", "data": {}}


def test_gateway_supports_sdk_v2_snake_case():
    class Session:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                is_error=False,
                structured_content={"domain": "order", "data": {}},
                content=[],
            )
    gateway = EvidenceGateway(Session(), ContractsStub())
    assert asyncio.run(gateway.call("get_order", case_id="CASE_TEST"))["domain"] == "order"


def test_gateway_rejects_sdk_v2_error():
    class Session:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                is_error=True,
                content=[SimpleNamespace(text="tool unavailable")],
            )
    gateway = EvidenceGateway(Session(), ContractsStub())
    with pytest.raises(RuntimeError, match="tool unavailable"):
        asyncio.run(gateway.call("get_order", case_id="CASE_TEST"))
