# Resume failed cases

Run directories remain fresh-only by default. An explicitly requested recovery retries
only cases whose checkpoint state is `failed`:

```powershell
day09 run --out runs/original-run --resume-failed
```

Repeat the original `--limit`, `--critic`, and `--abstain-on-failure` settings, if used.
Concurrency may change. The local receipt must be closed (`incomplete`) with at least
one failed case. Running, interrupted, complete, legacy receipts without execution
metadata, and inconsistent receipts are refused. A fresh run must have recorded a
clean Git revision; resume requires the same clean revision, model metadata, execution
settings, exact ordered case IDs, case-set version, and serialized input hashes.

Preflight verifies all saved inputs, outputs, evidence, case traces, merged trace, and
prior attempt archives before any model or case tool call. Successful evidence and
outputs are also revalidated. An exclusive `.resume-lock` prevents overlapping local
resumes; a process removes only the lock it created. An interrupted recovery is not
automatically restarted or converted to a new full run. A stale lock needs operator
inspection; the command never guesses that another process has stopped.

Successful checkpoints remain byte-for-byte unchanged. Before retrying failed cases,
the original receipt, merged trace, and failed evidence/output/trace files are copied
into a unique `attempts/` directory, with hashes recorded in `attempt_history`. The
original `run_id` and `started_at` remain unchanged. `traces/trace.jsonl` contains one
genuine latest trace per case, in original case order. No synthetic lifecycle events
are inserted. `traces/audit_trace.jsonl` additionally retains all earlier failed case
traces before those latest traces. Local efficiency/cost audits must include this audit
trace or the archived attempts; failed work must not be treated as zero cost. Calls
that fail before emitting an event cannot be reconstructed from local traces; provider
accounting remains authoritative.

This is local recovery, not a provider-side run reset or new submission authorization.
Use it only while the original provider run is still active. Local metadata cannot
prove that the remote MCP provider run has not changed. A provider reset requires a
new run under the provider's rules. Resume does not submit, export credentials, start
GPU services, or grant permission to submit. Normal package validation remains required.
The submission trace contains latest case attempts; the full attempt audit is kept
locally, not silently represented as the official submission trace.
