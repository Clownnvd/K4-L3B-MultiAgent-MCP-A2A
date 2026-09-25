# Implementation status

## Current: offline integration complete; live deployment not ready

As of 2026-09-25, the real orchestrator completes 100 synthetic demo cases with
zero failures. These outputs cannot be packaged or submitted as live evidence.
Batch execution, isolated checkpoints, guarded packaging and CLI integration
are implemented. Full regression and lint results are recorded in the version
receipt; synthetic correctness does not establish live semantic accuracy.

Qwen3.5-9B is now approved using its full tensor count (9,653,104,368), with a
JSON-only non-thinking request adapter. The local configuration targets a private
SSH tunnel. No successful model inference has happened yet.

The team credential is valid. Nine MCP tools returned actual evidence during
inspection; get_refund_timeline returned a server tool-execution error. Typed
tool failures are recorded separately from evidence and cannot become invented
empty results or zero-refund history. Missing financial evidence forces a
conservative investigation result or abort.

A single FPT H100 VM was provisioned. At the latest inspection, the VM was
RUNNING but its security group remained PROCESSING; SSH timed out and the model
smoke test failed to connect. Deployment files are prepared, not yet executed
on that VM. The owner requested keeping the VM running between trials.

Remaining: resolve VM network provisioning, install/start and identify the model,
pass the JSON smoke test, pass a small live run, investigate failures, complete
100 live cases, validate ZIP, commit/push and then submit. Submission is now
explicitly authorized only after these checks. No score or live completion is claimed.

## Foundational work

- Official L3B input release downloaded; CLI confirmed exactly 100 inputs.
- Isolated Python environment installed with the starter dependencies.
- MCP SDK 2 snake_case compatibility fixed; success and error parsing tested.
- Case-scoped evidence ledger, agent tool permissions, bounded transport retries and handoffs implemented.
- Independent checks for evidence references, candidate separation, money totals, refund balance and conflict-source consistency implemented.
- Model policy rejects unknown model sizes and models over 10 billion total parameters.
- R2AI transfer mapping documented in `R2AI_TRANSFER.md`.

## Historical snapshot: v0.1.0-scaffold (2026-09-25)

This is a work-in-progress source checkpoint, NOT a competition-ready release.

- The corrected credential was accepted by `/api/v2/me/submissions` (HTTP 200,
  JSON array), and the L3B workspace opened successfully. It remains only in
  the ignored local `.env`. No submission was made.
- `solve_case()` and the orchestrator now have implementations: entity resolution,
  specialist handoffs, model proposals, source-bound arithmetic and verification.
  They have not yet passed an end-to-end run.
- The model adapter currently approves only Qwen3-8B. Qwen3.5-9B was selected as
  the next candidate, but is NOT yet wired into the allowlist or a live endpoint.
- Latest foundational check: `pytest tests/test_gateway.py tests/test_safety.py
  tests/test_starter.py -q` passed all 10 tests.
- `day09 validate-inputs` validated exactly 100 official inputs locally.
- `test_orchestration.py` cannot collect because `student_agent.batch` does not
  exist yet. The planned `demo` and `run_package` modules are also absent.
- Full tests and lint are not green. The original release-safety tests expect a
  clean starter checkout rather than this workspace with ignored runtime inputs.

Remaining: batch/demo/run packaging, CLI integration, model configuration and
tests, authenticated MCP evidence validation, full regression checks, then a
verified submission artifact. No output, score or completion is claimed.

See [VERSIONING.md](VERSIONING.md) for publication and submission ordering.
