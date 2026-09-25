# R2AI lessons applied to the L3B competition

Reviewed local sources:

- KINGPRO: `docs/SILENT_ERROR_ASSURANCE.md`, `docs/V297_SUBMISSION_HANDOFF.md`, `tests/test_compiler_metamorphic.py`, `scripts/private_ready_doctor.py`.
- ARCANE: `ARCHITECTURE.md`, `SUBMISSION_DOCUMENTATION.md` from the local Stage 2 archive.
- LASTDANCE: `Docs/ARCHITECTURE.md`, `Docs/MODELS.md` from the local reference repository.

| Source | Transferable method | Day09 application |
|---|---|---|
| KINGPRO | Independent verification and adversarial perturbations | Verify money, evidence scope and conflicting fields; test candidate reordering and unrelated distractors. |
| KINGPRO | Atomic checkpoints, manifests and explicit failure states | Preserve each completed case; do not treat missing evidence as a successful answer. |
| ARCANE | Metadata gate before ranking | Restrict investigation to supplied candidates and customer identity before deeper tool calls. |
| ARCANE | Separate retrieval, evidence package and execution | Specialists return facts and evidence refs; a separate policy stage computes decisions; verifier checks output. |
| ARCANE | Bounded repair; malformed output is an error | Retry transport only within budget; do not fill failed fields with invented values. |
| LASTDANCE | Select only existing source references | Agent cannot invent an evidence ref or select a candidate outside the scoped evidence. |
| LASTDANCE | Deterministic arithmetic after semantic interpretation | Use Decimal and explicit chronology for refund decisions. |

Not transferred: benchmark-specific answer overrides, question-ID routing, leaderboard-driven per-case patches, 14B model configurations, synthetic evidence IDs, or financial-table-specific heuristics. ARCANE's named 4B/8B/9B checkpoints are historical context, not automatically approved or enabled model dependencies here.

Day09 differs materially: authoritative evidence comes from the authenticated MCP gateway, and its audit owns team/run/case provenance. Local JSON Schema success alone does not prove valid credentials or a scoreable submission.
