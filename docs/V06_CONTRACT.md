# Grounding and call-efficiency contract

Scope: improve source-bound decisions and avoid redundant MCP calls. Work locally;
no new GPU, live MCP batch, submission, or claim of an improved official score.
The saved v05 run is diagnostic input, not an answer oracle or a new submission.

Ownership: coordinator owns decision compilation, episode grounding, offline audit
and integration. Efficiency worker owns workflow/entity/evidence and efficiency
tests. Read-only audit worker proposes defects; it does not edit source.

Acceptance:

1. Responsible seller IDs must be witnessed in the resolved order, not copied
   from generic policy examples. Ambiguous responsibility stays unknown.
2. Seller-delay attribution must use shipping deadlines from the selected
   purchase episode; do not mix same-ID records from different purchases.
3. Refund issue states and settled refund balances must use the selected episode.
   An older failed request alone cannot prove a current failure. Missing evidence
   remains unknown; no invented zero or refund authorization.
   Contract correction: v04's case-opening accounting cutoff was an unverified
   assumption. A completed refund after opening must not disappear into zero.
   Retain later events and bound their episode by the next purchase, not opening.
   Two old cutoff tests are updated to this corrected behavior, not simply removed.
4. Efficiency tests measure real gateway invocation counts, preserve case isolation,
   and cover ambiguous/failed customer history. No candidate-ID regex shortcuts.
5. Full unit suite, lint, source safety, and a read-only saved-run audit pass.
6. Explicit failed-case recovery retries only failed checkpoints. Successful files
   remain unchanged; source, model, inputs and hashes must match. Prior failed
   attempts remain archived and auditable. Never reset or hide their tool cost.

Added recovery scope after inspection: batch.py, cli.py, test_batch_resume.py and
RESUME_FAILED_CASES.md are owned by a separate recovery worker. The old implementation
refused any reuse, so one failed case caused a second full live batch. The new flag
is opt-in, rejects incompatible/legacy receipts, and does not contact the provider
until local preflight passes. Server-run continuity still requires operator verification.

Invariants: official JSON contract unchanged; source refs remain authentic and
case-scoped; no hard-coded official case answers, hidden oracle, rewritten traces,
or cross-team/run evidence cache. Monetary calculations remain source-bound.

Proof: observed failing regression tests, then passing targeted and full suites.
Saved-run findings are consistency checks, not official semantic accuracy.
Baseline: official score 70.0197; semantic 53.7684; efficiency 0. The exact cause
of the efficiency score is unconfirmed because the private call budget is hidden.

Rollback: v05 remains intact in its original worktree and commit 56f7842.
