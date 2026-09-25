# First submission workflow

The owner authorized a first submission after committing/pushing source and
validating the full 100-case artifact. Model/service availability is verified;
semantic correctness is not claimed from schema success.

## Full run with explicit abstention

```shell
day09 run --concurrency 4 --abstain-on-failure --out .local/runs/live-first
```

This option is off by default. If model decisions cannot be verified after the
bounded repair, the case receives an explicit zero-confidence insufficient-evidence
output with unknown financial totals, no refund recommendation and actual consumed
MCP references only. The trace records `AGENT_ABSTAINED` and the receipt reports
the abstained count separately. Empty evidence remains a failed case. Such outputs
may receive low or zero scores; 100 packaged outputs does not mean 100 solved cases.

## Submit from the logged-in workstation

Install browser support using `pip install -e ".[browser]"`. Copy the full run
directory back to the same source revision on the workstation. Keep runtime
artifacts in ignored `.local/` or `runs/`; do not commit them.

```shell
python tools/submit_competition.py --run-dir .local/runs/live-first --output .local/submission-first.zip
python tools/submit_competition.py --run-dir .local/runs/live-first --output .local/submission-first.zip --confirm
python tools/submit_competition.py --run-dir .local/runs/live-first --check
```

The helper refuses dirty/unpushed/mismatched source, invalid/incomplete/demo runs,
wrong inventories, tampered checkpoints and mismatched ZIPs. It records an attempt
before clicking the official upload button exactly once. A connection failure or
uncertain response never triggers automatic resubmission. `--check` reads only
an acknowledged receipt, through same-origin browser fetch without inspecting
browser cookies or storage. Actual server status/score, not button clicks, are
the completion evidence.

Local attempt records are kept under `.local/submissions/`. Treat server receipts
as private operational data. Do not publish them in Git.
