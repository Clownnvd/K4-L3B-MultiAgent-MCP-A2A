# v06 local verification

This version has not been run with a live model or submitted to the competition.
The last verified official score remains 70.0197 (v05). No GPU was started.

## Changes and evidence

- Seller roles from policy are bound to case evidence, not copied example IDs.
  Read-only examination of 100 saved v05 outputs found 18 foreign seller IDs.
  Applying the new binding helper to the same evidence removed all 18; this was
  a helper replay, not a full model rerun or an official score prediction.
- The saved 10 seller-delay decisions lacked late seller IDs. The new episode
  helper found source-backed late seller IDs for all 10. Synthetic tests reject
  seller blame when only customer delivery, rather than seller handoff, was late.
- Case opening no longer silently erases later completed refunds. Regression
  tests cover a full refund after opening, omitted completed refunds, old episodes,
  the next-purchase boundary, identical refund IDs and conflicting amounts.
- History-first entity lookup produced the same resolutions on 100 saved cases:
  300 to 200 identity calls in offline gateway replay. A full synthetic case uses
  9 rather than 10 gateway calls while retaining all nine evidence domains.
- Explicit `--resume-failed` keeps 99 successful checkpoints unchanged and retries
  one failed case in the 100-case test. Prior attempts remain locally archived.
  The resulting artifact passes the existing 102-entry package validation.

Observed regression tests failed before fixes. An independent local review exposed
duplicate-refund counting, missing-deadline seller filtering and plural seller-ID
handling; all received additional regression tests and fixes.

## Reproduce

Final integration receipt: `python -m pytest -q --durations=5` passed 263 tests
in 287.43 seconds. The final read-only audit test file passed 11 tests separately.
`ruff check src tests tools` and `git diff --check` passed. The staged-source safety
check scanned 90 files and found no credential or forbidden-runtime-path issues.

Use the repository Python environment with this worktree's `src` on `PYTHONPATH`:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
python -m pytest -q
python -m ruff check src tests tools
python tools/audit_saved_run.py --run-dir ../v05/.local/runs/live-final-r2
git diff --check
```

After staging the intended source files, run `python tools/check_source_safety.py`.
The audit only reads existing artifacts. Its call counts cover the latest finalized
attempts and are a lower bound, never total provider usage or the official efficiency
score. The original v05 run contains 1000 locally recorded calls, 10 per case.

## Limits

- No hidden answers or private case scores were consulted. Claim verdicts still
  require model interpretation; local tests are not a semantic-accuracy benchmark.
- Unknown/conflicting dates, identities and responsibility remain conservative.
  Corrected episode binding can reject old decisions; it does not manufacture answers.
- The exact provider call budget and the cause of the previous efficiency score of
  zero are not established. Recovery cannot undo calls already made.
- Resume requires the same clean source and active provider run. It cannot resume
  v05 artifacts after changing to v06, and it does not prove remote run continuity.
- A new authorized live evaluation is required to measure official score changes.
