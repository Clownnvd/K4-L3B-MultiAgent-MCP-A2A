# Run and validate

Run commands from the repository root after installing `pip install -e ".[dev]"`.

## Offline proof

```shell
pytest -q
ruff check .
day09 demo --count 100 --out .local/runs/demo-check
```

Every output directory must be fresh. Demo runs are synthetic fixtures, not
actual model evaluations, and the submission packager rejects them.

## Live model and evidence

Store the competition key only in ignored `.env`. Model configuration and
deployment instructions are in [deploy/README.md](../deploy/README.md).

```shell
day09 validate-inputs
day09 mcp-tools
python tools/smoke_model.py
day09 run --limit 3 --concurrency 1 --out .local/runs/live-smoke
```

Tool discovery alone does not prove successful evidence retrieval. Inspect each
case receipt, evidence and trace; server execution errors are not empty evidence.
Do not start 100 cases if the model smoke test or limited live run is failing.

## Reproducible submission

Commit and push a verified source revision before the final run/submission.
Each run receipt records source revision/dirty state and non-secret model fields.

```shell
day09 run --concurrency 2 --out .local/runs/live-v1
day09 validate --run-dir .local/runs/live-v1
day09 package --run-dir .local/runs/live-v1 --output dist/submission-v1.zip
```

Record the ZIP SHA-256 and the submitted commit ID in ignored local metadata.
The ZIP must contain only manifest.json, trace.jsonl and the exact 100 outputs.
Upload via the authenticated L3B workspace only after validation and secret checks.
Read the actual server receipt before reporting any successful submission or score.

## Failure handling

- Never reuse another team's evidence or demo evidence in live output.
- Never replace an unreadable candidate with an invented order.
- Payment/refund tool failures cannot justify zero historical refunds.
- Local hashes detect checkpoint changes but are not signed external provenance.
- The provider must serve the exact approved model; arbitrary aliases are not
  proof of compliance with the 10B total-parameter limit.
