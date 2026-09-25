"""Validate, pin to pushed source, and optionally submit once through Chrome 9222.

Default is a local dry run. --confirm authorizes one UI submission attempt.
--check only polls an already captured official receipt; it never uploads.
Browser support: install websocket-client in the interpreter running this script.
No browser cookies, storage, authorization headers or API keys are inspected.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from contextlib import suppress
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from student_agent.batch import sha256, write_json  # noqa: E402
from student_agent.contracts import Contracts  # noqa: E402
from student_agent.run_package import validate_run  # noqa: E402

ORIGIN = "https://n7-competition.pages.dev"
CDP = "http://127.0.0.1:9222"


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *args],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Could not verify source against origin; submission refused") from error


def check_source(root: Path, receipt: dict) -> str:
    head = _git(root, "rev-parse", "HEAD")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Tracked source is dirty; commit and push before submission")
    source = receipt.get("source", {})
    if source.get("head") != head or source.get("dirty") is not False:
        raise ValueError("Run source must match HEAD and have dirty=false")
    branch = _git(root, "branch", "--show-current")
    if not branch:
        raise ValueError("Source HEAD is detached; a pushed branch is required")
    remote = _git(root, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    if remote.split() != [head, f"refs/heads/{branch}"]:
        raise ValueError("Source HEAD is not pushed to the exact origin branch")
    return head


def _zip_matches(path: Path, payloads: dict[str, bytes]) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            if sorted(archive.namelist()) != sorted(payloads):
                raise ValueError("ZIP inventory does not match the validated run")
            for name, expected in payloads.items():
                actual = archive.read(name)
                if name == "manifest.json":
                    first, second = json.loads(actual), json.loads(expected)
                    first.pop("generated_at", None)
                    second.pop("generated_at", None)
                    if first == second:
                        continue
                if actual != expected:
                    raise ValueError("ZIP content does not match the validated run")
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("Invalid submission ZIP archive") from error


class Browser:
    def __init__(self):
        try:
            import websocket
        except ImportError as error:
            raise RuntimeError(
                "Install browser support: python -m pip install websocket-client"
            ) from error
        with urllib.request.urlopen(CDP + "/json/list", timeout=3) as response:
            pages = json.load(response)
        targets = [page for page in pages if page.get("type") == "page"
                   and page.get("url", "").split("?", 1)[0].rstrip("/") == ORIGIN + "/l3b"]
        if len(targets) != 1:
            raise ValueError("Open exactly one logged-in official /l3b tab in Chrome 9222")
        self.ws = websocket.create_connection(targets[0]["webSocketDebuggerUrl"],
                                               timeout=1, suppress_origin=True)
        self.timeout_error = websocket.WebSocketTimeoutException
        self.sequence = 0
        self.responses = {}
        self.finished = set()

    def receive(self):
        message = json.loads(self.ws.recv())
        params = message.get("params", {})
        if message.get("method") == "Network.responseReceived":
            response = params["response"]
            # Deliberately extract only URL/status, never headers or request data.
            if response["url"] == ORIGIN + "/api/v2/submissions":
                self.responses[params["requestId"]] = response["status"]
        elif message.get("method") == "Network.loadingFinished":
            self.finished.add(params["requestId"])
        return message

    def call(self, method, params=None):
        self.sequence += 1
        number = self.sequence
        self.ws.send(json.dumps({"id": number, "method": method, "params": params or {}}))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                reply = self.receive()
            except self.timeout_error:
                continue
            if reply.get("id") == number:
                if "error" in reply:
                    raise RuntimeError(f"Browser command failed: {method}")
                return reply.get("result", {})
        raise TimeoutError("Browser command timed out")

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        if "exceptionDetails" in result:
            raise RuntimeError("Browser expression failed")
        return result.get("result", {}).get("value")


BUTTON = """[...document.querySelectorAll('button')].filter(b =>
    b.textContent.normalize('NFC').trim().replace(/\\s+/g,' ') === 'Nộp để chấm')"""


def _submit_browser(path: Path, timeout: int) -> dict:
    browser = Browser()
    try:
        root = browser.call("DOM.getDocument")["root"]["nodeId"]
        nodes = browser.call("DOM.querySelectorAll", {
            "nodeId": root, "selector": 'input[type="file"]',
        })["nodeIds"]
        if len(nodes) != 1:
            raise ValueError("Expected exactly one submission file input")
        browser.call("DOM.setFileInputFiles", {"nodeId": nodes[0], "files": [str(path)]})
        browser.evaluate("""(() => { const input=document.querySelector('input[type=file]');
            input.dispatchEvent(new Event('change',{bubbles:true}));
            return input.files.length; })()""")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            ready = f"(() => {{const b={BUTTON};return b.length===1&&!b[0].disabled}})()"
            if browser.evaluate(ready):
                break
            time.sleep(0.2)
        else:
            raise ValueError("Official submission button is missing or disabled")
        browser.call("Network.enable")
        clicked = browser.evaluate(f"""(() => {{const b={BUTTON};
            if(b.length!==1||b[0].disabled)return false;b[0].click();return true;}})()""")
        if clicked is not True:
            raise ValueError("Submission button could not be clicked")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for request_id, status in list(browser.responses.items()):
                if request_id not in browser.finished:
                    continue
                if not 200 <= status < 300:
                    return {"http_status": status, "rejected": True}
                body = browser.call("Network.getResponseBody", {"requestId": request_id})
                raw = base64.b64decode(body["body"]) if body.get("base64Encoded") else body["body"]
                value = json.loads(raw)
                receipt = value.get("receipt") if isinstance(value, dict) else None
                if not isinstance(receipt, str) or not 1 <= len(receipt) <= 256:
                    raise ValueError("Unknown official submit response schema")
                return {"receipt": receipt, "http_status": status}
            with suppress(browser.timeout_error):
                browser.receive()
        raise TimeoutError("No authoritative submission response captured")
    finally:
        browser.ws.close()


def _poll(receipt: str, timeout: int) -> dict:
    deadline = time.monotonic() + min(max(timeout, 1), 120)
    latest = {}
    browser = None
    try:
        browser = Browser()
        while time.monotonic() < deadline:
            remaining_ms = max(1, int(min(10, deadline - time.monotonic()) * 1000))
            path = "/api/v2/submissions/" + urllib.parse.quote(receipt, safe="")
            expression = f"""(async () => {{
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), {remaining_ms});
                try {{
                    const response = await fetch({json.dumps(path)}, {{
                        method: 'GET', signal: controller.signal
                    }});
                    if (!response.ok) return {{ok:false, status:response.status}};
                    return {{ok:true, data:await response.json()}};
                }} catch {{ return {{ok:false}}; }}
                finally {{ clearTimeout(timer); }}
            }})()"""
            result = browser.call("Runtime.evaluate", {
                "expression": expression, "returnByValue": True, "awaitPromise": True,
            })
            envelope = result.get("result", {}).get("value")
            if not isinstance(envelope, dict) or envelope.get("ok") is not True:
                return latest
            value = envelope.get("data")
            if (not isinstance(value, dict)
                    or not isinstance(value.get("submission_id"), str)
                    or not value["submission_id"]
                    or not isinstance(value.get("status"), str)):
                return latest
            for key in ("submission_id", "status"):
                latest[key] = value[key]
            if (isinstance(value.get("score"), (int, float))
                    and not isinstance(value["score"], bool)):
                latest["score"] = value["score"]
            if latest.get("status") not in {"queued", "processing"}:
                return latest
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    except Exception:
        # A received acknowledgment stays authoritative even when browser polling is unavailable.
        return latest
    finally:
        if browser is not None:
            with suppress(Exception):
                browser.ws.close()
    return latest


def _browser_preflight():
    if importlib.util.find_spec("websocket") is None:
        raise RuntimeError("Install browser support: python -m pip install websocket-client")


def execute(root: Path, run_dir: Path, output: Path, *, confirm=False, check=False, timeout=30):
    root, run_dir, output = root.resolve(), run_dir.resolve(), output.resolve()
    receipt = json.loads((run_dir / "receipt.json").read_text(encoding="utf-8"))
    identity = receipt.get("run_id")
    if not isinstance(identity, str) or not identity:
        raise ValueError("Run receipt has no stable run_id")
    key = hashlib.sha256(identity.encode()).hexdigest()
    directory = root / ".local" / "submissions" / key
    attempt_path = directory / "attempt.json"
    if attempt_path.exists():
        prior = json.loads(attempt_path.read_text(encoding="utf-8"))
        if check and prior.get("receipt"):
            prior.update(_poll(prior["receipt"], timeout))
            write_json(attempt_path, prior)
        return prior  # A new ZIP path must never bypass the once-per-run boundary.
    if check:
        raise ValueError("No recorded submission attempt to check")
    payloads = validate_run(run_dir, Contracts(root / "contracts/schemas"))
    head = check_source(root, receipt)
    if output.exists():
        _zip_matches(output, payloads)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(payloads.items()):
                archive.writestr(name, data)
    result = {"state": "dry_run", "run_id": identity, "source_head": head,
              "zip_sha256": sha256(output), "zip_path": str(output),
              "run_receipt_sha256": sha256(run_dir / "receipt.json")}
    write_json(directory / "prepared.json", result)
    if not confirm:
        return result
    _browser_preflight()  # Missing optional dependency cannot consume the one-attempt boundary.
    # Record the uncertain boundary BEFORE interacting with the page, using exclusive creation.
    result["state"] = "attempt_uncertain"
    with attempt_path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    try:
        if sha256(output) != result["zip_sha256"]:
            raise ValueError("ZIP hash changed before upload")
        response = _submit_browser(output, timeout)
        if response.get("rejected"):
            result.update(state="rejected", http_status=response["http_status"])
        elif isinstance(response.get("receipt"), str):
            result.update(state="accepted", receipt=response["receipt"])
            write_json(attempt_path, result)  # Preserve acknowledgment even if polling fails.
            result.update(_poll(response["receipt"], timeout))
        else:
            raise ValueError("Unknown official submission response")
    except Exception as error:
        result["error_type"] = type(error).__name__  # Never persist raw browser/server errors.
    write_json(attempt_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".local/submission.zip"))
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--confirm", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--timeout", type=int, choices=range(1, 121), default=30)
    args = parser.parse_args()
    try:
        result = execute(args.root, args.root / args.run_dir, args.root / args.output,
                         confirm=args.confirm, check=args.check, timeout=args.timeout)
        # Official receipt is retained locally; print only the operational outcome and IDs.
        print(json.dumps({k: v for k, v in result.items() if k != "receipt"}, ensure_ascii=False))
        if result["state"] in {"attempt_uncertain", "rejected"}:
            raise SystemExit(2)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Submission refused ({type(error).__name__}): {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
