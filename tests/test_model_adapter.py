import asyncio

from student_agent.model_adapter import ModelSettings, OpenAICompatibleModel


def test_qwen35_verified_checkpoint_and_non_thinking_request(monkeypatch):
    monkeypatch.setenv("MODEL_CHECKPOINT", "Qwen/Qwen3.5-9B")
    monkeypatch.setenv("MODEL_BASE_URL", "http://127.0.0.1:18000/v1")
    settings = ModelSettings.load()

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]}

    class Client:
        async def post(self, url, *, json, headers):
            assert url == "http://127.0.0.1:18000/v1/chat/completions"
            assert json["model"] == "Qwen/Qwen3.5-9B"
            assert json["chat_template_kwargs"] == {"enable_thinking": False}
            assert json["response_format"] == {"type": "json_object"}
            return Response()

    assert asyncio.run(OpenAICompatibleModel(settings, Client()).complete("test", {})) == {
        "ok": True
    }
