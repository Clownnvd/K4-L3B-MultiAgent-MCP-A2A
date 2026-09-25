"""Bounded case execution with isolated, inspectable checkpoints."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
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
        "decision_mode": getattr(settings, "decision_mode", "output"),
    }


def _execution_metadata(solver: Any, mode: str) -> dict[str, Any]:
    critic = getattr(solver, "critic", None)
    return {
        "allow_abstention": getattr(solver, "allow_abstention", False),
        "repair_attempts": getattr(solver, "repair_attempts", 1),
        "critic": (_model_metadata(type("Critic", (), {"model": critic})(), mode)
                   if critic is not None else None),
    }


def validate_resume(cases: list[dict[str, Any]], solver: Any, run_dir: Path, *,
                    mode: str, case_set_version: str) -> dict[str, Any]:
    """Read-only preflight; execute_batch repeats it under the exclusive resume lock."""
    # Lazy import avoids the package verifier's dependency on batch hashing helpers.
    from .run_package import _check_hash, _object, _trace_check

    run_dir = Path(run_dir).resolve()
    if any(path.is_symlink() or getattr(path, "is_junction", lambda: False)()
           for path in run_dir.rglob("*")):
        raise ValueError("Resume does not accept linked checkpoints")
    receipt = _object(run_dir / "receipt.json")
    ids = [case["case_id"] for case in cases]
    records = receipt.get("cases")
    if (receipt.get("schema_version") != "student-agent-run-v1"
            or receipt.get("state") != "incomplete" or not receipt.get("finished_at")
            or not receipt.get("run_id") or not receipt.get("started_at")
            or not isinstance(records, dict) or set(records) != set(ids)
            or receipt.get("case_ids") != ids
            or any(not isinstance(r, dict) or r.get("state") not in {"completed", "failed"}
                   for r in records.values())):
        raise ValueError("Resume requires a closed incomplete run with the exact original cases")
    failed = [cid for cid in ids if records[cid]["state"] == "failed"]
    if (not failed or receipt.get("failed") != len(failed)
            or receipt.get("completed") != len(ids) - len(failed)):
        raise ValueError("Resume requires consistent incomplete run counts and failed cases")
    source = source_metadata(solver.contracts.root.parent.parent)
    if (not source.get("head") or source.get("dirty") is not False
            or source != receipt.get("source")):
        raise ValueError("Resume requires the identical clean source revision")
    if (receipt.get("mode") != mode or receipt.get("case_set_version") != case_set_version
            or receipt.get("model") != _model_metadata(solver, mode)
            or receipt.get("execution") != _execution_metadata(solver, mode)):
        raise ValueError("Resume model, execution settings, mode or case-set differs")
    parts = []
    event_ids: set[str] = set()
    abstained = 0
    for case in cases:
        cid = case["case_id"]
        record = records[cid]
        for folder, suffix, kind in [("inputs", "json", "input"),
                                      ("outputs", "json", "output"),
                                      ("evidence", "json", "evidence"),
                                      ("traces/cases", "jsonl", "trace")]:
            path = run_dir / folder / f"{cid}.{suffix}"
            expected = record.get(f"{kind}_sha256")
            required = kind in {"input", "trace"} or record["state"] == "completed"
            if required or expected is not None:
                _check_hash(path, expected)
            elif path.exists():
                raise ValueError(f"Unrecorded checkpoint hash: {path.name}")
        if hashlib.sha256(json_bytes(case)).hexdigest() != record["input_sha256"]:
            raise ValueError(f"Resume input differs: {cid}")
        trace_bytes = (run_dir / "traces/cases" / f"{cid}.jsonl").read_bytes()
        parts.append(trace_bytes)
        events = [json.loads(line) for line in trace_bytes.decode("utf-8").splitlines()
                  if line.strip()]
        for event in events:
            solver.contracts.validate_trace(event, cid)
            if event["case_id"] != cid or event["event_id"] in event_ids:
                raise ValueError("Resume trace case mismatch or duplicate event ID")
            event_ids.add(event["event_id"])
        if record["state"] == "completed":
            explicit_abstention = any(event.get("event_type") == "handoff"
                                      and event.get("decision_code") == "AGENT_ABSTAINED"
                                      for event in events)
            if record.get("abstained") is not explicit_abstention:
                raise ValueError("Resume abstention record differs from trace")
            abstained += int(explicit_abstention)
            output = _object(run_dir / "outputs" / f"{cid}.json")
            ledger = _object(run_dir / "evidence" / f"{cid}.json")
            solver.contracts.validate_output(output, cid)
            for ref, evidence in ledger.items():
                solver.contracts.validate_evidence(evidence, cid)
                if evidence["evidence_ref"] != ref:
                    raise ValueError("Resume evidence reference mismatch")
            verify_output(cid, output, ledger)
            _trace_check(cid, events, ledger, output)
    if receipt.get("abstained") != abstained:
        raise ValueError("Resume abstention count differs from trace")
    for folder, suffix, kind in [("inputs", "json", "input"), ("outputs", "json", "output"),
                                  ("evidence", "json", "evidence"),
                                  ("traces/cases", "jsonl", "trace")]:
        expected_ids = {cid for cid in ids if records[cid].get(f"{kind}_sha256")}
        if {p.stem for p in (run_dir / folder).glob(f"*.{suffix}")} != expected_ids:
            raise ValueError("Resume checkpoint inventory differs")
    merged = run_dir / "traces/trace.jsonl"
    _check_hash(merged, receipt.get("trace_sha256"))
    if merged.read_bytes() != b"".join(parts):
        raise ValueError("Resume merged trace differs from checkpoint traces")
    for attempt in receipt.get("attempt_history", []):
        directory = Path(attempt["directory"])
        if (directory.is_absolute() or len(directory.parts) != 2
                or directory.parts[0] != "attempts" or ".." in directory.parts):
            raise ValueError("Invalid attempt archive directory")
        for relative, expected in attempt["files"].items():
            path = run_dir / directory / relative
            if not path.resolve().is_relative_to(run_dir / directory):
                raise ValueError("Invalid attempt archive path")
            _check_hash(path, expected)
    if receipt.get("attempt_history"):
        _check_hash(run_dir / "traces/audit_trace.jsonl", receipt.get("audit_trace_sha256"))
    return receipt


async def _resume_batch(cases: list[dict[str, Any]], gateway: Any, solver: Any,
                        run_dir: Path, mode: str, concurrency: int,
                        case_set_version: str) -> dict[str, Any]:
    if not run_dir.is_dir():
        raise ValueError("Resume requires an existing incomplete run directory")
    lock = run_dir / ".resume-lock"
    owner = uuid.uuid4().hex
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(owner)
    try:
        receipt = validate_resume(cases, solver, run_dir, mode=mode,
                                  case_set_version=case_set_version)
        failed = [case for case in cases if receipt["cases"][case["case_id"]]["state"] == "failed"]
        archive = run_dir / "attempts" / uuid.uuid4().hex
        archive.mkdir(parents=True)
        paths = [run_dir / "receipt.json", run_dir / "traces/trace.jsonl"]
        for case in failed:
            cid = case["case_id"]
            paths.extend(path for path in [run_dir / "evidence" / f"{cid}.json",
                                           run_dir / "traces/cases" / f"{cid}.jsonl",
                                           run_dir / "outputs" / f"{cid}.json"] if path.is_file())
        files = {}
        for path in paths:
            relative = path.relative_to(run_dir)
            destination = archive / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            files[relative.as_posix()] = sha256(destination)
            if sha256(path) != files[relative.as_posix()]:
                raise ValueError("Checkpoint changed during archive")
        receipt.setdefault("attempt_history", []).append({
            "directory": archive.relative_to(run_dir).as_posix(), "files": files,
            "failed_case_ids": [case["case_id"] for case in failed],
        })
        receipt.update(state="running", failed=0)
        write_json(run_dir / "receipt.json", receipt)
        # Copies above preserve all material before starting a new trace/evidence ledger.
        for case in failed:
            cid = case["case_id"]
            for path in [run_dir / "traces/cases" / f"{cid}.jsonl",
                         run_dir / "evidence" / f"{cid}.json",
                         run_dir / "outputs" / f"{cid}.json"]:
                if path.exists():
                    path.unlink()
        return await _execute_cases(failed, gateway, solver, run_dir, receipt, concurrency,
                                    resuming=True)
    finally:
        if lock.is_file() and lock.read_text(encoding="utf-8") == owner:
            lock.unlink()


async def execute_batch(
    cases: list[dict[str, Any]], gateway: Any, solver: Any, run_dir: Path, *,
    mode: str = "demo", concurrency: int = 4, case_set_version: str = "demo-v1",
    resume_failed: bool = False,
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
    if resume_failed:
        return await _resume_batch(cases, gateway, solver, run_dir, mode, concurrency,
                                   case_set_version)
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
        "execution": _execution_metadata(solver, mode),
    }
    write_json(run_dir / "receipt.json", receipt)
    return await _execute_cases(cases, gateway, solver, run_dir, receipt, concurrency)


async def _execute_cases(cases: list[dict[str, Any]], gateway: Any, solver: Any,
                         run_dir: Path, receipt: dict[str, Any], concurrency: int, *,
                         resuming: bool = False) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def execute(case: dict[str, Any]) -> None:
        async with semaphore:
            case_id = case["case_id"]
            input_path = run_dir / "inputs" / f"{case_id}.json"
            output_path = run_dir / "outputs" / f"{case_id}.json"
            evidence_path = run_dir / "evidence" / f"{case_id}.json"
            trace_path = run_dir / "traces" / "cases" / f"{case_id}.jsonl"
            if not resuming:
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
        for case_id in receipt["case_ids"]:
            handle.write((run_dir / "traces" / "cases" / f"{case_id}.jsonl").read_bytes())
    if receipt.get("attempt_history"):
        audit = run_dir / "traces/audit_trace.jsonl"
        with audit.open("wb") as handle:
            for attempt in receipt["attempt_history"]:
                for case_id in attempt["failed_case_ids"]:
                    handle.write((run_dir / attempt["directory"] / "traces/cases"
                                  / f"{case_id}.jsonl").read_bytes())
            handle.write(merged.read_bytes())
        receipt["audit_trace_sha256"] = sha256(audit)
    receipt.update(state="complete" if not receipt["failed"] else "incomplete",
                   trace_sha256=sha256(merged), finished_at=datetime.now(UTC).isoformat())
    write_json(run_dir / "receipt.json", receipt)
    return receipt
