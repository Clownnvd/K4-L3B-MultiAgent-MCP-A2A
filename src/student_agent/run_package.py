"""Fresh verification gate for isolated runs; only live full runs are submittable."""
from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .batch import json_bytes, sha256
from .cases import CASE_ID_PATTERN, load_case_set
from .contracts import Contracts
from .submission import MAX_FILE_BYTES, MAX_SUBMISSION_BYTES, SECRET_PATTERN, build_manifest
from .verifier import verify_output


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Missing or invalid checkpoint: {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Checkpoint must be an object: {path.name}")
    return value


def _check_hash(path: Path, expected: Any) -> None:
    if not path.is_file() or path.is_symlink() or not expected or sha256(path) != expected:
        raise ValueError(f"Missing or tampered checkpoint hash: {path.name}")


def _trace_check(case_id: str, events: list[dict], ledger: dict, output: dict) -> None:
    types = [event["event_type"] for event in events]
    required = ["case_received", "policy_decided", "verification_completed", "case_finalized"]
    if (not types or any(types.count(kind) != 1 for kind in required)
            or types[0] != required[0] or types[-1] != required[-1]
            or [types.index(kind) for kind in required]
            != sorted(types.index(kind) for kind in required)):
        raise ValueError(f"Invalid lifecycle for {case_id}")
    timestamps = [datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
                  for event in events]
    if timestamps != sorted(timestamps):
        raise ValueError(f"Invalid lifecycle timestamp order for {case_id}")
    consumed: set[str] = set()
    for event in events:
        refs = event.get("evidence_refs") or []
        if any(ref not in ledger for ref in refs):
            raise ValueError(f"Trace references unknown evidence for {case_id}")
        if event["event_type"] == "tool_result_consumed":
            if not refs:
                raise ValueError("Evidence consumption event has no refs")
            for ref in refs:
                attributes = event.get("attributes") or {}
                if (attributes.get("result_hash") != ledger[ref]["result_hash"]
                        or attributes.get("domain") != ledger[ref]["domain"]):
                    raise ValueError("Consumed evidence differs from saved ledger")
            consumed.update(refs)
        elif not set(refs) <= consumed:
            raise ValueError(f"Evidence used before it was consumed for {case_id}")
    if not set(output["evidence_refs"]) <= consumed or set(ledger) != consumed:
        raise ValueError(f"Missing consumed evidence for {case_id}")


def validate_run(run_dir: Path, contracts: Contracts) -> dict[str, bytes]:
    """Return a freshly verified ZIP payload; never trusts receipt counts alone."""
    run_dir = Path(run_dir).resolve()
    receipt = _object(run_dir / "receipt.json")
    if receipt.get("mode") != "live":
        raise ValueError("Only live runs may be packaged; demo runs are not submissions")
    ids = receipt.get("case_ids")
    if (not isinstance(ids, list) or len(ids) != 100
            or any(not isinstance(i, str) or not CASE_ID_PATTERN.fullmatch(i) for i in ids)
            or len(set(ids)) != 100):
        raise ValueError("A live submission requires exactly 100 unique case IDs")
    records = receipt.get("cases")
    if (receipt.get("schema_version") != "student-agent-run-v1"
            or receipt.get("state") != "complete" or receipt.get("completed") != 100
            or receipt.get("failed") != 0 or not isinstance(records, dict)
            or set(records) != set(ids)
            or any(not isinstance(record, dict) for record in records.values())):
        raise ValueError("Run is incomplete or contains failed cases")
    official = load_case_set(contracts.root.parent.parent)
    if set(ids) != set(official.case_ids) or receipt.get("case_set_version") != official.version:
        raise ValueError("Run does not match the official case-set")
    for folder, extension in [("inputs", "json"), ("outputs", "json"),
                              ("evidence", "json"), ("traces/cases", "jsonl")]:
        actual = {path.stem for path in (run_dir / folder).glob(f"*.{extension}")}
        if actual != set(ids):
            raise ValueError(f"Checkpoint inventory differs from case-set: {folder}")
    merged = run_dir / "traces/trace.jsonl"
    _check_hash(merged, receipt.get("trace_sha256"))
    event_ids: set[str] = set()
    trace_parts = []
    payloads: dict[str, bytes] = {}
    for case_id in ids:
        record = records[case_id]
        if record.get("state") != "completed":
            raise ValueError(f"Run contains an incomplete case: {case_id}")
        paths = {"input": run_dir / "inputs" / f"{case_id}.json",
                 "output": run_dir / "outputs" / f"{case_id}.json",
                 "evidence": run_dir / "evidence" / f"{case_id}.json",
                 "trace": run_dir / "traces/cases" / f"{case_id}.jsonl"}
        for kind, path in paths.items():
            _check_hash(path, record.get(f"{kind}_sha256"))
        case = _object(paths["input"])
        if case != official.cases[case_id]:
            raise ValueError(f"Input checkpoint differs from official case: {case_id}")
        output, ledger = _object(paths["output"]), _object(paths["evidence"])
        contracts.validate_output(output, case_id)
        for ref, evidence in ledger.items():
            contracts.validate_evidence(evidence, case_id)
            if ref != evidence["evidence_ref"] or "demo" in ref.lower():
                raise ValueError("Mismatched or demo evidence in live run")
        verify_output(case_id, output, ledger)
        expected_claims = {
            c["claim_id"] for c in case.get("customer_request", {}).get("claims", [])
        }
        claims = [c["claim_id"] for c in output.get("claim_assessments", [])]
        if set(claims) != expected_claims or len(set(claims)) != len(claims):
            raise ValueError("Output claims differ from case input")
        candidates = set(case.get("candidate_order_ids", []))
        claimed = case.get("customer_request", {}).get("claimed_order_id")
        if claimed:
            candidates.add(claimed)
        resolution = output["entity_resolution"]
        assigned = set(resolution["resolved_order_ids"] + resolution["rejected_candidates"])
        if not assigned <= candidates:
            raise ValueError("Output entity escapes input case scope")
        if resolution["status"] != "resolved" and (
            output["financial_resolution"]["recommended_refund_brl"] != 0
            or output["assessment"]["case_status"] != "needs_investigation"
        ):
            raise ValueError("Unresolved entity requires investigation without refund")
        trace_bytes = paths["trace"].read_bytes()
        trace_parts.append(trace_bytes)
        events = []
        for line in trace_bytes.decode("utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            contracts.validate_trace(event, case_id)
            if event["case_id"] != case_id or event["event_id"] in event_ids:
                raise ValueError("Trace has wrong case or duplicate event ID")
            event_ids.add(event["event_id"])
            events.append(event)
        _trace_check(case_id, events, ledger, output)
        payloads[f"outputs/{case_id}.json"] = json_bytes(output)
    if b"".join(trace_parts) != merged.read_bytes():
        raise ValueError("Merged trace differs from case checkpoints")
    manifest = build_manifest(official)
    contracts.validate_manifest(manifest)
    payloads.update({"manifest.json": json_bytes(manifest), "trace.jsonl": merged.read_bytes()})
    if any(SECRET_PATTERN.search(data.decode("utf-8")) for data in payloads.values()):
        raise ValueError("A Team API Key appears in submission artifacts")
    if any(len(data) > MAX_FILE_BYTES for data in payloads.values()):
        raise ValueError("Submission file exceeds 1 MB")
    if sum(map(len, payloads.values())) > MAX_SUBMISSION_BYTES:
        raise ValueError("Submission exceeds the 12 MB uncompressed limit")
    return payloads


def package_run(run_dir: Path, destination: Path, contracts: Contracts) -> Path:
    payloads = validate_run(run_dir, contracts)
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(payloads.items()):
            archive.writestr(name, payload)
    return destination
