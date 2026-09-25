import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "submit_competition", ROOT / "tools/submit_competition.py")
submit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(submit)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    receipt = {"run_id": "test-run", "source": {"head": "a" * 40, "dirty": False}}
    (run / "receipt.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(submit, "Contracts", lambda root: object())
    monkeypatch.setattr(submit, "_browser_preflight", lambda: None)
    monkeypatch.setattr(submit, "validate_run", lambda *args: {
        "manifest.json": b'{"generated_at":"now","case_set_version":"test"}',
        "trace.jsonl": b"trace", "outputs/CASE_001.json": b"{}",
    })
    def git(root, *args):
        return {"rev-parse": "a" * 40, "status": "", "branch": "main",
                "ls-remote": "a" * 40 + "\trefs/heads/main"}[args[0]]
    monkeypatch.setattr(submit, "_git", git)
    return tmp_path, run, tmp_path / ".local/submission.zip"


def test_default_dry_run_never_touches_browser_and_records_hash(prepared, monkeypatch):
    root, run, output = prepared
    monkeypatch.setattr(submit, "_submit_browser", lambda *args: pytest.fail("browser called"))
    result = submit.execute(root, run, output)
    assert result["state"] == "dry_run"
    assert result["zip_sha256"] == submit.sha256(output)
    assert result["source_head"] == "a" * 40


@pytest.mark.parametrize("reason", ["demo", "incomplete", "tampered hash"])
def test_official_validation_failure_prevents_upload(prepared, monkeypatch, reason):
    root, run, output = prepared
    def fail(*args):
        raise ValueError(reason)
    monkeypatch.setattr(submit, "validate_run", fail)
    monkeypatch.setattr(submit, "_submit_browser", lambda *args: pytest.fail("browser called"))
    with pytest.raises(ValueError, match=reason):
        submit.execute(root, run, output, confirm=True)
    assert not output.exists()


@pytest.mark.parametrize("failure", ["dirty", "unpushed", "run_head", "run_dirty"])
def test_source_gates(prepared, monkeypatch, failure):
    root, run, output = prepared
    original = submit._git
    def git(root, *args):
        if failure == "dirty" and args[0] == "status":
            return " M src/changed.py"
        if failure == "unpushed" and args[0] == "ls-remote":
            return "b" * 40 + "\trefs/heads/main"
        return original(root, *args)
    monkeypatch.setattr(submit, "_git", git)
    if failure.startswith("run_"):
        value = json.loads((run / "receipt.json").read_text())
        value["source"]["head" if failure == "run_head" else "dirty"] = (
            "b" * 40 if failure == "run_head" else True)
        (run / "receipt.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="source|pushed|dirty|HEAD"):
        submit.execute(root, run, output, confirm=True)


def test_uncertain_attempt_never_repeats_post_even_with_new_zip(prepared, monkeypatch):
    root, run, output = prepared
    calls = []
    def uncertain(*args):
        calls.append(1)
        raise TimeoutError("sensitive browser detail")
    monkeypatch.setattr(submit, "_submit_browser", uncertain)
    result = submit.execute(root, run, output, confirm=True)
    assert result["state"] == "attempt_uncertain"
    assert "sensitive" not in json.dumps(result)
    again = submit.execute(root, run, root / ".local/other.zip", confirm=True)
    assert again["state"] == "attempt_uncertain"
    assert calls == [1]


def test_actual_response_and_readonly_recovery(prepared, monkeypatch):
    root, run, output = prepared
    monkeypatch.setattr(submit, "_submit_browser", lambda *args: {"receipt": "server-receipt"})
    monkeypatch.setattr(submit, "_poll", lambda *args: {
        "submission_id": "actual-123", "status": "completed", "score": 0.75,
    })
    result = submit.execute(root, run, output, confirm=True)
    assert result["submission_id"] == "actual-123" and result["score"] == 0.75
    monkeypatch.setattr(submit, "_submit_browser", lambda *args: pytest.fail("repeat POST"))
    checked = submit.execute(root, run, output, check=True)
    assert checked["submission_id"] == "actual-123"


def test_existing_zip_must_match_validated_payload(prepared):
    root, run, output = prepared
    submit.execute(root, run, output)
    output.write_bytes(b"tampered archive")
    with pytest.raises(ValueError, match="ZIP|archive|hash"):
        submit.execute(root, run, output, confirm=True)


def test_missing_browser_dependency_does_not_record_uncertain_attempt(prepared, monkeypatch):
    root, run, output = prepared
    def missing():
        raise RuntimeError("Install browser support: python -m pip install websocket-client")
    monkeypatch.setattr(submit, "_browser_preflight", missing)
    with pytest.raises(RuntimeError, match="pip install websocket-client"):
        submit.execute(root, run, output, confirm=True)
    assert not list((root / ".local/submissions").glob("*/attempt.json"))


@pytest.mark.parametrize("payload,accepted", [({"receipt": "official-receipt"}, True),
                                            ({"unexpected": "not a receipt"}, False)])
def test_browser_single_click_requires_official_response(tmp_path, monkeypatch, payload, accepted):
    from types import SimpleNamespace

    class FakeBrowser:
        def __init__(self):
            self.responses = {"request-1": 200}
            self.finished = {"request-1"}
            self.clicks = 0
            self.closed = False
            self.ws = SimpleNamespace(close=self.close)

        def close(self):
            self.closed = True

        def call(self, method, params=None):
            if method == "DOM.getDocument":
                return {"root": {"nodeId": 1}}
            if method == "DOM.querySelectorAll":
                return {"nodeIds": [2]}
            if method == "Network.getResponseBody":
                return {"body": json.dumps(payload)}
            return {}

        def evaluate(self, expression):
            if ".click()" in expression:
                self.clicks += 1
            return True

    browser = FakeBrowser()
    monkeypatch.setattr(submit, "Browser", lambda: browser)
    if accepted:
        response = submit._submit_browser(tmp_path / "submission.zip", 1)
        assert response["receipt"] == payload["receipt"]
    else:
        with pytest.raises(ValueError, match="Unknown official"):
            submit._submit_browser(tmp_path / "submission.zip", 1)
    assert browser.clicks == 1 and browser.closed


@pytest.mark.parametrize("responses,expected", [
    ([{"ok": True, "data": {"submission_id": "id-1", "status": "queued", "score": None}},
      {"ok": True, "data": {"submission_id": "id-1", "status": "completed", "score": 0.85}}],
     {"submission_id": "id-1", "status": "completed", "score": 0.85}),
    ([{"ok": False, "status": 403}], {}),
    ([{"ok": True, "data": {"unexpected": "unknown response"}}], {}),
])
def test_poll_uses_bounded_same_origin_browser_fetch(monkeypatch, responses, expected):
    from types import SimpleNamespace

    calls = []
    closed = []
    class FakeBrowser:
        ws = SimpleNamespace(close=lambda: closed.append(True))

        def call(self, method, params):
            calls.append((method, params))
            return {"result": {"value": responses.pop(0)}}

    monkeypatch.setattr(submit, "Browser", FakeBrowser)
    monkeypatch.setattr(submit.time, "sleep", lambda value: None)
    assert submit._poll("official-receipt", 3) == expected
    assert closed == [True]
    for method, params in calls:
        assert method == "Runtime.evaluate" and params["awaitPromise"] is True
        expression = params["expression"]
        assert "fetch(" in expression and "/api/v2/submissions/official-receipt" in expression
        assert "AbortController" in expression
        assert all(term not in expression for term in ["Authorization", "Storage", "cookie"])


def test_poll_http_failure_retains_accepted_receipt(prepared, monkeypatch):
    from types import SimpleNamespace

    root, run, output = prepared
    monkeypatch.setattr(submit, "_submit_browser", lambda *args: {"receipt": "actual-receipt"})
    class FakeBrowser:
        ws = SimpleNamespace(close=lambda: None)

        def call(self, method, params):
            return {"result": {"value": {"ok": False, "status": 403}}}

    monkeypatch.setattr(submit, "Browser", FakeBrowser)
    result = submit.execute(root, run, output, confirm=True)
    assert result["state"] == "accepted" and result["receipt"] == "actual-receipt"
    assert "score" not in result and "status" not in result
