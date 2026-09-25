"""Offline failure recovery: completed cases must never contact the gateway again."""
import asyncio
import json

import pytest

from student_agent import batch
from test_batch_package import FixtureSolver, prepare


@pytest.fixture(autouse=True)
def clean_source(monkeypatch):
    monkeypatch.setattr(batch, "source_metadata", lambda root: {"head": "a" * 40, "dirty": False})


class SentinelSolver(FixtureSolver):
    async def solve(self, case, gateway, trace, evidence_path=None):
        gateway.append(case["case_id"])
        assert case["case_id"] == "CASE_003", "A completed case was executed again"
        return await super().solve(case, gateway, trace, evidence_path=evidence_path)


def retry(run, contracts, cases=None, **kwargs):
    receipt = json.loads((run / "receipt.json").read_text())
    if cases is None:
        cases = [json.loads((run / "inputs" / f"{case_id}.json").read_text())
                 for case_id in receipt["case_ids"]]
    calls = []
    result = asyncio.run(batch.execute_batch(
        cases, calls, SentinelSolver(contracts), run, mode="live",
        case_set_version="test-v1", resume_failed=True, **kwargs))
    return result, calls


def test_resume_one_failure_preserves_99_successes_and_actual_history(tmp_path):
    run, contracts, _, before = prepare(tmp_path, fail="CASE_003")
    original = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                for folder in ["inputs", "outputs", "evidence", "traces/cases"]
                for path in (run / folder).iterdir() if path.stem != "CASE_003"}
    failed_trace = (run / "traces/cases/CASE_003.jsonl").read_bytes()
    receipt_bytes = (run / "receipt.json").read_bytes()
    receipt, calls = retry(run, contracts)
    assert calls == ["CASE_003"]
    assert (receipt["state"], receipt["completed"], receipt["failed"]) == ("complete", 100, 0)
    assert receipt["run_id"] == before["run_id"]
    assert receipt["started_at"] == before["started_at"]
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == value
               for path, value in original.items())
    archive = run / receipt["attempt_history"][0]["directory"]
    assert (archive / "receipt.json").read_bytes() == receipt_bytes
    assert (archive / "traces/cases/CASE_003.jsonl").read_bytes() == failed_trace
    latest = (run / "traces/trace.jsonl").read_bytes()
    assert latest == b"".join((run / f"traces/cases/{cid}.jsonl").read_bytes()
                              for cid in receipt["case_ids"])
    assert (run / "traces/audit_trace.jsonl").read_bytes() == failed_trace + latest
    assert not (run / ".resume-lock").exists()
    from student_agent.run_package import validate_run

    assert len(validate_run(run, contracts)) == 102


@pytest.mark.parametrize("relative", ["inputs/CASE_000.json", "outputs/CASE_000.json",
                                     "evidence/CASE_000.json", "traces/cases/CASE_000.jsonl",
                                     "traces/cases/CASE_003.jsonl", "traces/trace.jsonl"])
def test_resume_tampering_refused_before_any_call_or_write(tmp_path, relative):
    run, contracts, _, _ = prepare(tmp_path, count=5, fail="CASE_003")
    path = run / relative
    path.write_bytes(path.read_bytes() + b" ")
    before = (run / "receipt.json").read_bytes()
    with pytest.raises(ValueError, match="hash|input|tamper"):
        retry(run, contracts)
    assert (run / "receipt.json").read_bytes() == before
    assert not (run / "attempts").exists()


@pytest.mark.parametrize("state", ["running", "complete"])
def test_resume_refuses_running_or_successful_run(tmp_path, state):
    run, contracts, _, receipt = prepare(tmp_path, count=5, fail="CASE_003")
    receipt["state"] = state
    batch.write_json(run / "receipt.json", receipt)
    with pytest.raises(ValueError, match="incomplete"):
        retry(run, contracts)


def test_resume_refuses_concurrent_lock_and_preserves_its_owner(tmp_path):
    run, contracts, _, _ = prepare(tmp_path, count=5, fail="CASE_003")
    lock = run / ".resume-lock"
    lock.write_text("other-owner")
    with pytest.raises((ValueError, FileExistsError)):
        retry(run, contracts)
    assert lock.read_text() == "other-owner"


@pytest.mark.parametrize("change", ["dirty", "head", "model", "input", "ids"])
def test_resume_refuses_changed_execution_contract(tmp_path, monkeypatch, change):
    run, contracts, _, receipt = prepare(tmp_path, count=5, fail="CASE_003")
    cases = [json.loads((run / f"inputs/{cid}.json").read_text()) for cid in receipt["case_ids"]]
    if change in {"dirty", "head"}:
        source = {"head": "b" * 40 if change == "head" else "a" * 40,
                  "dirty": change == "dirty"}
        monkeypatch.setattr(batch, "source_metadata", lambda root: source)
    elif change == "model":
        receipt["model"] = {"mode": "changed"}
        batch.write_json(run / "receipt.json", receipt)
    elif change == "input":
        cases[0]["new_field"] = "changed"
    else:
        cases.append({"case_id": "CASE_NEW"})
    with pytest.raises(ValueError):
        retry(run, contracts, cases)
    assert not (run / "attempts").exists()


def test_cli_resume_is_explicit_and_requires_an_existing_out_directory():
    from pathlib import Path

    from student_agent.cli import _run, parser

    assert not parser().parse_args(["run"]).resume_failed
    assert parser().parse_args(["run", "--out", "runs/existing", "--resume-failed"]).resume_failed
    with pytest.raises(ValueError, match="requires --out"):
        asyncio.run(_run(Path("."), resume_failed=True))


def test_repeated_failure_keeps_both_attempts_and_refuses_tampered_archive(tmp_path):
    run, contracts, _, receipt = prepare(tmp_path, count=5, fail="CASE_003")
    cases = [json.loads((run / f"inputs/{cid}.json").read_text()) for cid in receipt["case_ids"]]
    original_trace = (run / "traces/cases/CASE_003.jsonl").read_bytes()
    second = asyncio.run(batch.execute_batch(
        cases, [], SentinelSolver(contracts, fail="CASE_003"), run, mode="live",
        case_set_version="test-v1", resume_failed=True))
    assert (second["completed"], second["failed"]) == (4, 1)
    second_trace = (run / "traces/cases/CASE_003.jsonl").read_bytes()
    archive = run / second["attempt_history"][0]["directory"] / "traces/cases/CASE_003.jsonl"
    archive.write_bytes(original_trace + b" ")
    with pytest.raises(ValueError, match="hash|tamper"):
        retry(run, contracts)
    archive.write_bytes(original_trace)
    third, calls = retry(run, contracts)
    assert calls == ["CASE_003"] and len(third["attempt_history"]) == 2
    assert (run / "traces/audit_trace.jsonl").read_bytes() == (
        original_trace + second_trace + (run / "traces/trace.jsonl").read_bytes())
