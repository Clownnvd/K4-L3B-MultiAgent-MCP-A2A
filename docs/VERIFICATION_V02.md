# v0.2 offline integration receipt — 2026-09-25

Outcome: offline model-ready source, not a completed live submission.

Fresh checks after implementation and receipt-metadata changes:

- PASS: `python -m pytest -q` — 54 passed in 68.84 seconds.
- PASS: `python -m ruff check . --output-format concise` — all checks passed.
- PASS: `day09 demo --out .local/runs/demo-integration-v01 --concurrency 4`
  — completed 100, failed 0 (synthetic evidence, no live model).
- PASS: official input validation — exactly 100 cases.
- PASS: authenticated workspace access and real MCP evidence retrieval for nine
  tools during inspection; this is not a guarantee of every case/tool.
- FAIL: `python tools/smoke_model.py` — connection failure; GPU VM network setup
  has not yet provided usable SSH access or a running model service.
- BLOCKED: small live run, 100-case live run, submission ZIP and actual scoring.

Tool failure tests explicitly prove missing refund data is not replaced with
invented zero-refund history. Packaging tests prove demo, incomplete, stale,
wrong-case and unconsumed-evidence artifacts are rejected.

The source release-safety test checks tracked/addable files instead of rejecting
ignored local inputs. A regression test proves forcibly tracked inputs are still
detected. No test was skipped to make the suite pass.

The model service deployment scripts are prepared but were not executed on the
new FPT VM at this checkpoint. The owner requested leaving the rented GPU running.
