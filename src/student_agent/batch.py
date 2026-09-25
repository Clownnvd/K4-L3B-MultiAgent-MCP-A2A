"""Bounded case execution with isolated, inspectable checkpoints."""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .cases import CASE_ID_PATTERN
from .trace import TraceWriter
from .verifier import verify_output


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json_bytes(value))
    temporary.replace(path)


def source_metadata(root: Path) -> dict[str, Any]:
    """Read revision and worktree state without recording file paths or Git diagnostics."""
    try:
        head = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=3,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), "status", "--porcelain",
             "--untracked-files=normal"],
            capture_output=True, text=True, check=True, timeout=3,
        ).stdout
        return {"head": head, "dirty": bool(status.strip())}
    except (OSError, subprocess.SubprocessError):
        return {"head": None, "dirty": None}


def _model_metadata(solver: Any, mode: str) -> dict[str, Any]:
    if mode == "demo":
        return {"mode": "deterministic_fixture"}
    settings = getattr(getattr(solver, "model", None), "settings", None)
    if settings is None:
        return {"mode": "unreported"}
    # Explicit whitelist: settings also contains credentials and a potentially authenticated URL.
    return {
        "checkpoint": settings.checkpoint, "served_name": settings.served_name,
        "max_tokens": settings.max_tokens, "temperature": 0.7, "mode": "nonthinking",
    }


async def execute_batch(
    cases: list[dict[str, Any]], gateway: Any, solver: Any, run_dir: Path, *,
    mode: str = "demo", concurrency: int = 4, case_set_version: str = "demo-v1",
) -> dict[str, Any]:
    if mode not in {"demo", "live"}:
        raise ValueError("mode must be demo or live")
    if (isinstance(concurrency, bool) or not isinstance(concurrency, int)
            or not 1 <= concurrency <= 32):
        raise ValueError("concurrency must be an integer between 1 and 32")
    cases = list(cases)
    ids = [case.get("case_id") for case in cases]
    if not ids or any(not isinstance(i, str) or not CASE_ID_PATTERN.fullmatch(i) for i in ids):
        raise ValueError("Batch requires valid case IDs")
    if len(set(ids)) != len(ids):
        raise ValueError("Batch case IDs must be unique")
    if not isinstance(case_set_version, str) or not case_set_version:
        raise ValueError("case_set_version is required")
    run_dir = Path(run_dir).resolve()
    if run_dir.exists() and (not run_dir.is_dir() or any(run_dir.iterdir())):
        raise ValueError("Run directory must be fresh and empty; existing results are preserved")
    run_dir.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also catches concurrent attempts to claim the same empty directory.
    with (run_dir / ".run-lock").open("x", encoding="utf-8") as handle:
        handle.write("This run directory cannot be reused.\n")
    receipt: dict[str, Any] = {
        "schema_version": "student-agent-run-v1", "run_id": uuid.uuid4().hex,
        "mode": mode, "case_set_version": case_set_version, "case_ids": ids,
        "started_at": datetime.now(UTC).isoformat(), "state": "running",
        "completed": 0, "failed": 0, "abstained": 0, "cases": {},
        "source": source_metadata(solver.contracts.root.parent.parent),
        "model": _model_metadata(solver, mode),
    }
    write_json(run_dir / "receipt.json", receipt)
    semaphore = asyncio.Semaphore(concurrency)

    async def execute(case: dict[str, Any]) -> None:
        async with semaphore:
            case_id = case["case_id"]
            input_path = run_dir / "inputs" / f"{case_id}.json"
            output_path = run_dir / "outputs" / f"{case_id}.json"
            evidence_path = run_dir / "evidence" / f"{case_id}.json"
            trace_path = run_dir / "traces" / "cases" / f"{case_id}.jsonl"
            write_json(input_path, case)
            trace = TraceWriter(trace_path, solver.contracts)
            trace_path.touch(exist_ok=False)
            record: dict[str, Any] = {
                "state": "failed", "input_sha256": sha256(input_path), "abstained": False,
            }
            try:
                output = await solver.solve(case, gateway, trace, evidence_path=evidence_path)
                solver.contracts.validate_output(output, case_id)
                ledger = json.loads(evidence_path.read_text(encoding="utf-8"))
                verify_output(case_id, output, ledger)
                events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8")
                          .splitlines() if line.strip()]
                abstained = any(event.get("case_id") == case_id
                                and event.get("event_type") == "handoff"
                                and event.get("decision_code") == "AGENT_ABSTAINED"
                                for event in events)
                write_json(output_path, output)
                record.update(state="completed", output_sha256=sha256(output_path),
                              abstained=abstained)
            except Exception as error:
                # Transport/model exceptions can contain credentials or customer data.
                record["error_type"] = type(error).__name__
            finally:
                record["trace_sha256"] = sha256(trace_path)
                if evidence_path.is_file():
                    record["evidence_sha256"] = sha256(evidence_path)
                receipt["cases"][case_id] = record
                key = "completed" if record["state"] == "completed" else "failed"
                receipt[key] += 1
                receipt["abstained"] += int(record["state"] == "completed" and record["abstained"])
                write_json(run_dir / "receipt.json", receipt)

    await asyncio.gather(*(execute(case) for case in cases))
    merged = run_dir / "traces" / "trace.jsonl"
    with merged.open("wb") as handle:
        for case_id in ids:
            handle.write((run_dir / "traces" / "cases" / f"{case_id}.jsonl").read_bytes())
    receipt.update(state="complete" if not receipt["failed"] else "incomplete",
                   trace_sha256=sha256(merged), finished_at=datetime.now(UTC).isoformat())
    write_json(run_dir / "receipt.json", receipt)
    return receipt
