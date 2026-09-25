# Implementation status

## Completed

- Official L3B input release downloaded; CLI confirmed exactly 100 inputs.
- Isolated Python environment installed with the starter dependencies.
- MCP SDK 2 snake_case compatibility fixed; success and error parsing tested.
- Case-scoped evidence ledger, agent tool permissions, bounded transport retries and handoffs implemented.
- Independent checks for evidence references, candidate separation, money totals, refund balance and conflict-source consistency implemented.
- Model policy rejects unknown model sizes and models over 10 billion total parameters.
- R2AI transfer mapping documented in `R2AI_TRANSFER.md`.

## Development snapshot: v0.1.0-scaffold (2026-09-25)

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
