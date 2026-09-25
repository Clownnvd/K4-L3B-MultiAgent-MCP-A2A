import asyncio
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from student_agent.batch import execute_batch
from student_agent.contracts import Contracts
from student_agent.run_package import package_run

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class FixtureSolver:
    """Synthetic schema-valid checkpoints; never contacts a service."""

    def __init__(self, contracts, fail=None):
        self.contracts = contracts
        self.fail = fail
        self.active = self.peak = 0

    async def solve(self, case, gateway, trace, evidence_path=None):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.001)
            case_id = case["case_id"]
            ref = "ev_" + hashlib.sha256(case_id.encode()).hexdigest()
            evidence = {"schema_version": "day09-mcp-evidence-v1", "evidence_ref": ref,
                        "result_hash": "sha256:" + "a" * 64, "domain": "order",
                        "data": {"order_id": "order-1"}}
            write_json(evidence_path, {ref: evidence})
            trace.emit(case_id=case_id, event_type="case_received", actor="coordinator")
            if case_id == self.fail:
                raise ValueError("private transport details must not enter receipt")
            trace.emit(case_id=case_id, event_type="tool_result_consumed", actor="entity-agent",
                       tool_name="get_order", evidence_refs=[ref],
                       attributes={"result_hash": evidence["result_hash"], "domain": "order"})
            trace.emit(case_id=case_id, event_type="policy_decided", actor="policy-agent",
                       evidence_refs=[ref])
            trace.emit(case_id=case_id, event_type="verification_completed", actor="verifier",
                       evidence_refs=[ref])
            trace.emit(case_id=case_id, event_type="case_finalized", actor="coordinator")
            return {
                "schema_version": "day09-l3b-output-v2", "case_id": case_id,
                "assessment": {"primary_issue": "insufficient_evidence", "secondary_issues": [],
                               "case_status": "needs_investigation", "confidence": 0.5},
                "affected_entities": {"order_ids": ["order-1"], "item_ids": [], "seller_ids": [],
                                      "payment_references": [], "shipment_ids": []},
                "entity_resolution": {"status": "resolved", "resolved_order_ids": ["order-1"],
                                      "rejected_candidates": [], "confidence": 0.5},
                "customer_context": {"customer_unique_id": None, "related_order_ids": []},
                "shipment_analysis": {"verdict": "insufficient_evidence", "late_seller_ids": [],
                                      "timeline_complete": False},
                "payment_analysis": {"verdict": "insufficient_evidence", "captured_total_brl": None,
                                     "refunded_total_brl": None, "refundable_total_brl": None},
                "root_cause_analysis": {"ranked_causes": [], "responsible_parties": []},
                "evidence_refs": [ref], "claim_assessments": [], "data_conflicts": [],
                "financial_resolution": {"currency": "BRL", "recommended_refund_brl": 0,
                                         "refund_lines": []}, "resolution_actions": [],
            }
        finally:
            self.active -= 1


def prepare(tmp_path, count=100, mode="live", fail=None):
    official = tmp_path / "official"
    shutil.copytree(ROOT / "contracts/schemas", official / "contracts/schemas")
    cases = [{"case_id": f"CASE_{i:03}", "candidate_order_ids": ["order-1"],
              "customer_request": {"claims": []}} for i in range(count)]
    write_json(official / "case-set.json", {"case_set_version": "test-v1", "variant_id": "l3b",
                                           "case_ids": [c["case_id"] for c in cases]})
    for case in cases:
        write_json(official / "inputs" / (case["case_id"] + ".json"), case)
    contracts = Contracts(official / "contracts/schemas")
    solver = FixtureSolver(contracts, fail)
    run = tmp_path / "run"
    receipt = asyncio.run(execute_batch(cases, object(), solver, run, mode=mode,
                                       concurrency=3, case_set_version="test-v1"))
    return run, contracts, solver, receipt


def test_bounded_batch_preserves_failure_and_deterministic_case_order(tmp_path):
    run, _, solver, receipt = prepare(tmp_path, count=7, fail="CASE_003")
    assert 1 < solver.peak <= 3
    assert (receipt["completed"], receipt["failed"]) == (6, 1)
    assert not (run / "outputs/CASE_003.json").exists()
    assert (run / "evidence/CASE_003.json").is_file()
    assert "private transport" not in (run / "receipt.json").read_text()
    events = [json.loads(line) for line in (run / "traces/trace.jsonl").read_text().splitlines()]
    assert [e["case_id"] for e in events if e["event_type"] == "case_received"] == [
        f"CASE_{i:03}" for i in range(7)]


def test_run_directory_cannot_be_reused_or_stale_files_deleted(tmp_path):
    run, _, solver, _ = prepare(tmp_path, count=1)
    original = (run / "receipt.json").read_bytes()
    with pytest.raises(ValueError, match="empty|fresh|exist"):
        asyncio.run(execute_batch([{"case_id": "CASE_000"}], object(), solver, run))
    assert (run / "receipt.json").read_bytes() == original


def test_complete_live_fixture_packages_exact_official_layout(tmp_path):
    run, contracts, _, _ = prepare(tmp_path)
    destination = package_run(run, tmp_path / "submission.zip", contracts)
    with zipfile.ZipFile(destination) as archive:
        assert len(archive.namelist()) == 102
        assert {"manifest.json", "trace.jsonl"} <= set(archive.namelist())
        assert json.loads(archive.read("manifest.json"))["case_set_version"] == "test-v1"
    with pytest.raises((ValueError, FileExistsError)):
        package_run(run, destination, contracts)


@pytest.mark.parametrize("mode,count,fail,match", [
    ("demo", 100, None, "demo"), ("live", 2, None, "100|case-set|incomplete"),
    ("live", 100, "CASE_005", "incomplete|failed"),
])
def test_unsubmittable_runs_are_refused(tmp_path, mode, count, fail, match):
    run, contracts, _, _ = prepare(tmp_path, count, mode, fail)
    with pytest.raises(ValueError, match=match):
        package_run(run, tmp_path / "bad.zip", contracts)
    assert not (tmp_path / "bad.zip").exists()


@pytest.mark.parametrize("relative", ["outputs/CASE_000.json", "evidence/CASE_000.json",
                                     "traces/cases/CASE_000.jsonl", "inputs/CASE_000.json"])
def test_checkpoint_tamper_refused(tmp_path, relative):
    run, contracts, _, _ = prepare(tmp_path)
    with (run / relative).open("a", encoding="utf-8") as handle:
        handle.write(" ")
    with pytest.raises(ValueError, match="hash|tamper"):
        package_run(run, tmp_path / "bad.zip", contracts)


def test_wrong_official_case_content_refused(tmp_path):
    run, contracts, _, _ = prepare(tmp_path)
    write_json(contracts.root.parent.parent / "inputs/CASE_000.json", {"case_id": "CASE_000"})
    with pytest.raises(ValueError, match="input|case"):
        package_run(run, tmp_path / "bad.zip", contracts)


@pytest.mark.parametrize("mutation,match", [("lifecycle", "lifecycle"),
                                           ("missingrefs", "consumed|evidence")])
def test_fresh_validation_checks_beyond_hashes(tmp_path, mutation, match):
    run, contracts, _, receipt = prepare(tmp_path)
    path = run / "traces/cases/CASE_000.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    if mutation == "lifecycle":
        events[-2], events[-1] = events[-1], events[-2]
    else:
        events = [e for e in events if e["event_type"] != "tool_result_consumed"]
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    receipt["cases"]["CASE_000"]["trace_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    merged = run / "traces/trace.jsonl"
    merged.write_bytes(b"".join((run / f"traces/cases/{case_id}.jsonl").read_bytes()
                                for case_id in receipt["case_ids"]))
    receipt["trace_sha256"] = hashlib.sha256(merged.read_bytes()).hexdigest()
    write_json(run / "receipt.json", receipt)
    with pytest.raises(ValueError, match=match):
        package_run(run, tmp_path / "bad.zip", contracts)


def test_cli_demo_single_lifecycle_and_preserves_directory(tmp_path, monkeypatch, capsys):
    from student_agent.cli import main

    run = tmp_path / "cli-demo"
    argv = ["day09", "--root", str(ROOT), "demo", "--count", "2", "--out", str(run)]
    monkeypatch.setattr("sys.argv", argv)
    main()
    assert json.loads(capsys.readouterr().out)["completed"] == 2
    events = [json.loads(line) for line in (run / "traces/trace.jsonl").read_text().splitlines()]
    for case_id in {event["case_id"] for event in events}:
        types = [event["event_type"] for event in events if event["case_id"] == case_id]
        assert types.count("case_received") == types.count("case_finalized") == 1
    before = (run / "receipt.json").read_bytes()
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert before == (run / "receipt.json").read_bytes()


def test_live_cli_parser_supports_explicit_limit_and_output():
    from student_agent.cli import parser

    args = parser().parse_args(["live", "--limit", "3", "--out", "runs/first"])
    assert args.limit == 3 and args.out == "runs/first"


def test_invalid_batch_parameters_do_not_create_directory(tmp_path):
    contracts = Contracts(ROOT / "contracts/schemas")
    solver = FixtureSolver(contracts)
    for cases, concurrency in [([{"case_id": "CASE_A"}] * 2, 1),
                               ([{"case_id": "../escape"}], 1),
                               ([{"case_id": "CASE_A"}], 0)]:
        with pytest.raises(ValueError):
            asyncio.run(execute_batch(cases, object(), solver, tmp_path / "run",
                                       concurrency=concurrency))
    assert not (tmp_path / "run").exists()


def test_receipt_reproducibility_uses_explicit_safe_model_fields(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from student_agent import batch
    from student_agent.model_adapter import ModelSettings

    monkeypatch.setattr(batch, "source_metadata", lambda root: {"head": "a" * 40, "dirty": True})
    solver = FixtureSolver(Contracts(ROOT / "contracts/schemas"))
    solver.model = SimpleNamespace(settings=ModelSettings(
        "Qwen/Qwen3-8B", "local-qwen", "https://private-auth.example/secret",
        "fake-api-key-never-serialize", max_tokens=1234,
    ))
    case = {"case_id": "CASE_META", "candidate_order_ids": ["order-1"]}
    receipt = asyncio.run(execute_batch([case], object(), solver, tmp_path / "live", mode="live"))
    assert receipt["source"] == {"head": "a" * 40, "dirty": True}
    assert receipt["model"] == {
        "checkpoint": "Qwen/Qwen3-8B", "served_name": "local-qwen", "max_tokens": 1234,
        "temperature": 0.7, "mode": "nonthinking",
    }
    serialized = (tmp_path / "live/receipt.json").read_text()
    assert "fake-api-key-never-serialize" not in serialized
    assert "private-auth" not in serialized
    demo = asyncio.run(execute_batch([case], object(), solver, tmp_path / "demo", mode="demo"))
    assert demo["model"] == {"mode": "deterministic_fixture"}
