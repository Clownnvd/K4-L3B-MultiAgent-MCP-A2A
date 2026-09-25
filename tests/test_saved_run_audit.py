"""Offline saved-artifact audit acceptance tests; no agent or live config imports."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "audit_saved_run.py"


def put(root, path, data):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data), encoding="utf-8")


def fixture(root, case_id="CASE_1", *, foreign=False):
    put(root, f"inputs/{case_id}.json", {
        "case_id": case_id, "opened_at": "2020-01-10T00:00:00Z"})
    put(root, f"evidence/{case_id}.json", {
        "items": {"domain": "item", "data": {
            "order_id": "order-a", "items": [{"seller_id": "seller-a"}],
            "other": {"order_id": "order-b", "seller_id": "seller-b"}}},
        "policy": {"domain": "policy", "data": {"seller_id": "seller-b"}},
        "refund": {"domain": "refund", "data": {
            "order_id": "order-a", "events": [
                {"event_type": "refund_completed", "status": "completed",
                 "event_at": "2020-01-11T00:00:00Z"},
                {"event_type": "refund_requested", "status": "pending",
                 "event_at": "2020-01-11T00:00:00Z"},
                {"order_id": "order-b", "event_type": "refund_completed",
                 "status": "completed", "event_at": "2020-01-11T00:00:00Z"},
                {"event_type": "refund_completed", "status": "completed",
                 "event_at": "2020-01-09T00:00:00Z"}]}}})
    put(root, f"outputs/{case_id}.json", {
        "case_id": case_id,
        "entity_resolution": {"resolved_order_ids": ["order-a"]},
        "root_cause_analysis": {"responsible_parties": [
            {"party_type": "seller", "party_id": "seller-b" if foreign else "seller-a"}]},
        "shipment_analysis": {"verdict": "seller_delay" if foreign else "on_time",
                              "late_seller_ids": []}})
    trace = root / f"traces/cases/{case_id}.jsonl"
    trace.parent.mkdir(parents=True, exist_ok=True)
    events = [{"case_id": case_id, "event_type": "verification_completed",
               "attributes": {"tool_calls": count}} for count in (3, 10)]
    trace.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")


def run(root):
    result = subprocess.run([sys.executable, str(SCRIPT), "--run-dir", str(root)],
                            capture_output=True, text=True, check=False)
    assert not result.stderr
    return result.returncode, json.loads(result.stdout)


def test_flags_scope_and_real_trace_counts_without_modifying_artifacts(tmp_path):
    fixture(tmp_path, foreign=True)
    fixture(tmp_path, "CASE_2")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    code, report = run(tmp_path)
    assert code == 0
    assert report["counts"] == {
        "output_files": 2, "audited_cases": 2, "foreign_seller_responsible_parties": 1,
        "seller_delay_missing_late_seller_ids": 1,
        "completed_refund_events_after_opened_at": 2,
        "cases_with_completed_refund_after_opened_at": 2}
    assert report["tool_calls"]["total"] == 20
    assert report["tool_calls"]["distribution"] == {"10": 2}
    assert report["tool_calls"]["cases_with_count"] == 2
    assert report["diagnostics"]["foreign_seller_case_ids"] == ["CASE_1"]
    assert report["diagnostics"]["seller_delay_missing_late_seller_ids_case_ids"] == ["CASE_1"]
    assert "seller-a" not in json.dumps(report)
    assert "seller-b" not in json.dumps(report)
    assert before == {p.relative_to(tmp_path): p.read_bytes()
                      for p in tmp_path.rglob("*") if p.is_file()}


def test_absent_trace_count_is_unknown_not_zero(tmp_path):
    fixture(tmp_path)
    put(tmp_path, "traces/cases/CASE_1.jsonl", {
        "case_id": "CASE_1", "event_type": "verification_completed", "attributes": {}})
    code, report = run(tmp_path)
    assert code == 0
    assert report["tool_calls"]["total"] is None
    assert report["tool_calls"]["cases_with_count"] == 0
    assert report["tool_calls"]["missing_case_ids"] == ["CASE_1"]


def test_missing_and_corrupt_files_fail_without_exposing_contents(tmp_path):
    fixture(tmp_path)
    (tmp_path / "inputs/CASE_1.json").write_text('SECRET never echo {', encoding="utf-8")
    (tmp_path / "evidence/CASE_1.json").unlink()
    code, report = run(tmp_path)
    assert code == 2
    assert report["status"] == "incomplete"
    assert {e["code"] for e in report["errors"]} == {"INVALID_JSON", "MISSING_FILE"}
    assert "SECRET" not in json.dumps(report)


def test_missing_output_and_trace_are_not_silent(tmp_path):
    fixture(tmp_path)
    (tmp_path / "outputs/CASE_1.json").unlink()
    (tmp_path / "traces/cases/CASE_1.jsonl").unlink()
    code, report = run(tmp_path)
    assert code == 2
    assert report["counts"]["output_files"] == 0
    assert len(report["errors"]) == 2


def test_wrong_shapes_and_mismatched_case_are_reported(tmp_path):
    fixture(tmp_path)
    put(tmp_path, "inputs/CASE_1.json", {"case_id": "OTHER_CASE"})
    put(tmp_path, "outputs/CASE_1.json", [])
    (tmp_path / "traces/cases/CASE_1.jsonl").write_text('{broken', encoding="utf-8")
    code, report = run(tmp_path)
    assert code == 2
    assert {e["code"] for e in report["errors"]} == {
        "CASE_ID_MISMATCH", "INVALID_STRUCTURE", "INVALID_JSON"}


def test_empty_run_cannot_report_success(tmp_path):
    code, report = run(tmp_path)
    assert code == 2
    assert report["status"] == "incomplete"


def test_invalid_tool_count_is_not_added(tmp_path):
    fixture(tmp_path)
    put(tmp_path, "traces/cases/CASE_1.jsonl", {
        "case_id": "CASE_1", "event_type": "verification_completed",
        "attributes": {"tool_calls": True}})
    code, report = run(tmp_path)
    assert code == 2
    assert report["errors"][0]["code"] == "INVALID_TOOL_CALL_COUNT"
    assert report["tool_calls"]["total"] is None


def test_refund_boundary_unknown_time_and_supported_seller_are_not_false_flags(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "evidence/CASE_1.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence["refund"]["data"]["events"][0]["event_at"] = "2020-01-09T21:00:00-03:00"
    evidence["refund"]["data"]["events"][3]["event_at"] = "2020-01-11T00:00:00"
    put(tmp_path, "evidence/CASE_1.json", evidence)
    path = tmp_path / "outputs/CASE_1.json"
    output = json.loads(path.read_text(encoding="utf-8"))
    output["shipment_analysis"] = {"verdict": "seller_delay", "late_seller_ids": ["seller-a"]}
    put(tmp_path, "outputs/CASE_1.json", output)
    code, report = run(tmp_path)
    assert code == 0
    assert report["counts"]["foreign_seller_responsible_parties"] == 0
    assert report["counts"]["seller_delay_missing_late_seller_ids"] == 0
    assert report["counts"]["completed_refund_events_after_opened_at"] == 0
    assert report["diagnostics"]["refund_timestamp_unknown_case_ids"] == ["CASE_1"]


def test_malformed_nested_evidence_fails_without_traceback(tmp_path):
    fixture(tmp_path)
    put(tmp_path, "evidence/CASE_1.json", {
        "refund": {"domain": "refund", "data": {"order_id": "order-a", "events": [
            {"event_type": ["PRIVATE bad shape"], "status": "completed"}]}}})
    code, report = run(tmp_path)
    assert code == 2
    assert report["errors"][0]["code"] == "INVALID_STRUCTURE"
    assert "PRIVATE" not in json.dumps(report)


def test_archived_attempts_make_historical_call_count_unknown(tmp_path):
    fixture(tmp_path)
    put(tmp_path, "receipt.json", {"attempt_history": [
        {"directory": "attempts/001", "failed_case_ids": ["CASE_1"]},
        {"directory": "attempts/002", "failed_case_ids": ["CASE_1"]}]})
    code, report = run(tmp_path)
    assert code == 0
    assert report["tool_calls"]["total"] == 10
    assert report["tool_calls"]["archived_failed_attempts"] == 2
    assert report["tool_calls"]["historical_call_count_unknown"] is True
    assert report["tool_calls"]["call_count_scope"] == "latest_finalized_attempt_lower_bound"


def test_scoped_plural_seller_ids_witness_responsibility(tmp_path):
    fixture(tmp_path)
    put(tmp_path, "evidence/CASE_1.json", {
        "items": {"domain": "item", "data": {
            "order_id": "order-a", "items": [{"seller_ids": ["seller-a"]}],
            "other": {"order_id": "order-b", "seller_ids": ["seller-b"]}}}})
    code, report = run(tmp_path)
    assert code == 0
    assert report["counts"]["foreign_seller_responsible_parties"] == 0
    assert report["diagnostics"]["foreign_seller_case_ids"] == []
    output_path = tmp_path / "outputs/CASE_1.json"
    output = json.loads(output_path.read_text(encoding="utf-8"))
    output["root_cause_analysis"]["responsible_parties"][0]["party_id"] = "seller-b"
    put(tmp_path, "outputs/CASE_1.json", output)
    code, report = run(tmp_path)
    assert code == 0
    assert report["counts"]["foreign_seller_responsible_parties"] == 1
