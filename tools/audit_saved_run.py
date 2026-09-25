"""Read-only, stdlib-only audit of saved run artifacts; not an official score.

Exit 0 means all required files were readable and structurally auditable, even
when findings exist. Exit 2 means incomplete/corrupt artifacts. No source values
are emitted except file paths and case IDs. No live configuration is imported.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


def records(value, order_id=None):
    """Inherit order scope downward; explicit child scope replaces its parent."""
    if isinstance(value, dict):
        scope = value.get("order_id", order_id)
        yield value, scope
        for child in value.values():
            yield from records(child, scope)
    elif isinstance(value, list):
        for child in value:
            yield from records(child, order_id)


def aware_date(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def audit(run_dir: Path) -> dict:
    errors = []

    def error(code, path, case_id=None):
        entry = {"code": code, "path": str(path)}
        if case_id is not None:
            entry["case_id"] = case_id
        errors.append(entry)

    def read(path, case_id, *, trace=False):
        try:
            raw = path.read_text(encoding="utf-8-sig")
            result = ([json.loads(line) for line in raw.splitlines() if line.strip()]
                      if trace else json.loads(raw))
        except FileNotFoundError:
            error("MISSING_FILE", path, case_id)
            return None
        except (ValueError, UnicodeError, RecursionError):
            error("INVALID_JSON", path, case_id)
            return None
        except OSError:
            error("UNREADABLE_FILE", path, case_id)
            return None
        if (trace and (not result or not all(isinstance(e, dict) for e in result))
                or not trace and not isinstance(result, dict)):
            error("INVALID_STRUCTURE", path, case_id)
            return None
        if trace:
            if any(e.get("case_id") != case_id for e in result):
                error("CASE_ID_MISMATCH", path, case_id)
                return None
        elif path.parent.name in {"inputs", "outputs"} and result.get("case_id") != case_id:
            error("CASE_ID_MISMATCH", path, case_id)
            return None
        return result

    # Receipt history lists archived failed run attempts, not provider call counts.
    archived_failed_attempts = None
    receipt_path = run_dir / "receipt.json"
    if receipt_path.exists():
        receipt = read(receipt_path, None)
        if receipt is not None:
            history = receipt.get("attempt_history", [])
            if not isinstance(history, list) or not all(isinstance(x, dict) for x in history):
                error("INVALID_STRUCTURE", receipt_path)
            else:
                archived_failed_attempts = len(history)
    directories = {"inputs": "*.json", "evidence": "*.json",
                   "outputs": "*.json", "traces/cases": "*.jsonl"}
    files = {}
    for directory, pattern in directories.items():
        path = run_dir / directory
        try:
            if not path.is_dir():
                error("MISSING_DIRECTORY", path)
                files[directory] = []
            else:
                files[directory] = sorted(path.glob(pattern))
        except OSError:
            error("UNREADABLE_DIRECTORY", path)
            files[directory] = []
    case_ids = sorted({p.stem for group in files.values() for p in group})
    if not case_ids:
        error("NO_CASES", run_dir)
    counts = {"output_files": len(files["outputs"]), "audited_cases": 0,
              "foreign_seller_responsible_parties": 0,
              "seller_delay_missing_late_seller_ids": 0,
              "completed_refund_events_after_opened_at": 0,
              "cases_with_completed_refund_after_opened_at": 0}
    diagnostics = {"foreign_seller_case_ids": [],
                   "seller_delay_missing_late_seller_ids_case_ids": [],
                   "completed_refund_after_opened_at_case_ids": [],
                   "refund_timestamp_unknown_case_ids": []}
    calls = []
    missing_calls = []
    for case_id in case_ids:
        paths = {d: run_dir / d / (case_id + (".jsonl" if d == "traces/cases" else ".json"))
                 for d in directories}
        data = {d: read(p, case_id, trace=d == "traces/cases") for d, p in paths.items()}
        trace = data["traces/cases"]
        last_count = None
        if trace is not None:
            for event in trace:
                if event.get("event_type") != "verification_completed":
                    continue
                attrs = event.get("attributes", {})
                if not isinstance(attrs, dict):
                    error("INVALID_STRUCTURE", paths["traces/cases"], case_id)
                    last_count = None
                    break
                count = attrs.get("tool_calls")
                if count is not None and (type(count) is not int or count < 0):
                    error("INVALID_TOOL_CALL_COUNT", paths["traces/cases"], case_id)
                    last_count = None
                    break
                # Counts are cumulative within a case: use the final verification.
                last_count = count
        if last_count is None:
            missing_calls.append(case_id)
        else:
            calls.append(last_count)
        case, ledger, output = (data[d] for d in ("inputs", "evidence", "outputs"))
        if case is None or ledger is None or output is None:
            continue
        if any(not isinstance(e, dict) or not isinstance(e.get("domain"), str)
               or "data" not in e or not isinstance(e["data"], (dict, list))
               for e in ledger.values()):
            error("INVALID_STRUCTURE", paths["evidence"], case_id)
            continue
        if any(key in row and row[key] is not None and not isinstance(row[key], str)
               for e in ledger.values() for row, _ in records(e["data"])
               for key in ("event_type", "status", "order_id", "seller_id")):
            error("INVALID_STRUCTURE", paths["evidence"], case_id)
            continue
        resolution = output.get("entity_resolution")
        cause = output.get("root_cause_analysis")
        shipment = output.get("shipment_analysis")
        if not all(isinstance(x, dict) for x in (resolution, cause, shipment)):
            error("INVALID_STRUCTURE", paths["outputs"], case_id)
            continue
        resolved = resolution.get("resolved_order_ids")
        parties = cause.get("responsible_parties")
        late_sellers = shipment.get("late_seller_ids", [])
        if (not isinstance(resolved, list) or not all(isinstance(x, str) for x in resolved)
                or not isinstance(parties, list) or not all(isinstance(x, dict) for x in parties)
                or not isinstance(late_sellers, list)
                or not all(isinstance(x, str) for x in late_sellers)):
            error("INVALID_STRUCTURE", paths["outputs"], case_id)
            continue
        resolved = set(resolved)
        scoped = [(e["domain"], row) for e in ledger.values() if e["domain"] != "policy"
                  for row, scope in records(e["data"])
                  if isinstance(scope, str) and scope in resolved]
        sellers = {row["seller_id"] for _, row in scoped
                   if isinstance(row.get("seller_id"), str) and row["seller_id"]}
        sellers.update(seller for _, row in scoped
                       if isinstance(row.get("seller_ids"), list)
                       for seller in row["seller_ids"] if isinstance(seller, str) and seller)
        foreign = sum(p.get("party_type") == "seller"
                      and isinstance(p.get("party_id"), str)
                      and p["party_id"] not in sellers for p in parties)
        if foreign:
            counts["foreign_seller_responsible_parties"] += foreign
            diagnostics["foreign_seller_case_ids"].append(case_id)
        if shipment.get("verdict") == "seller_delay" and not late_sellers:
            counts["seller_delay_missing_late_seller_ids"] += 1
            diagnostics["seller_delay_missing_late_seller_ids_case_ids"].append(case_id)
        opened = aware_date(case.get("opened_at"))
        completed = [row for domain, row in scoped if domain == "refund"
                     and row.get("event_type") in {"refund_completed", "refunded", "refund_settled"}
                     and row.get("status") in {"confirmed", "completed", "settled",
                                                "succeeded", "success"}]
        future = sum(opened is not None and aware_date(row.get("event_at")) is not None
                     and aware_date(row["event_at"]) > opened for row in completed)
        if future:
            counts["completed_refund_events_after_opened_at"] += future
            counts["cases_with_completed_refund_after_opened_at"] += 1
            diagnostics["completed_refund_after_opened_at_case_ids"].append(case_id)
        if completed and (opened is None or any(aware_date(r.get("event_at")) is None
                                               for r in completed)):
            diagnostics["refund_timestamp_unknown_case_ids"].append(case_id)
        counts["audited_cases"] += 1
    return {"audit_version": 1, "status": "incomplete" if errors else "complete",
            "run_dir": str(run_dir), "counts": counts, "diagnostics": diagnostics,
            "tool_calls": {"basis": "last_verification_completed_per_case",
                           "call_count_scope": "latest_finalized_attempt_lower_bound",
                           "archived_failed_attempts": archived_failed_attempts,
                           "historical_call_count_unknown": (
                               archived_failed_attempts is None or archived_failed_attempts > 0
                               or bool(missing_calls)),
                           "total": sum(calls) if calls else None,
                           "cases_with_count": len(calls),
                           "distribution": {str(k): v for k, v in sorted(Counter(calls).items())},
                           "missing_case_ids": missing_calls},
            "notes": ["Diagnostic audit only; not an official score.",
                      "After-opening completed refunds are chronology diagnostics, not errors.",
                      "Refund counts are observations; repeated evidence is not deduplicated.",
                      "Tool-call totals cover only cases with a recorded valid final count.",
                      "Recorded calls are a lower bound, never total provider usage.",
                      "Archived attempts count receipt history entries; their calls are unknown."],
            "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.run_dir.resolve())
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 2 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
