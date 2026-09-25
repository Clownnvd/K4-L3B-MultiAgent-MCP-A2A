# KINGPRO implementation contract

Outcome: produce 100 L3B outputs, observable multi-agent trace and a valid submission ZIP from the official input release and authenticated MCP evidence.

Scope: entity resolution; customer, item/product, shipment, payment/refund and policy specialists; conflict resolution; independent verifier; resumable bounded batch execution. Do not use local Olist data as a substitute for audited MCP evidence.

Invariants:

- Every referenced evidence object was obtained through the team's authenticated MCP session for the same case. Never fabricate an evidence reference.
- Claims and input hints are allegations, not ground truth. Resolve/reject candidates from evidence.
- Monetary operations use Decimal; no double refund; null is preserved when data is unavailable.
- All outputs conform to the immutable public L3B schema; traces contain observable events only.
- Coordinator assigns specialist tasks, receives actual handoffs and finalizes only after verifier success.
- No LLM is used by the deterministic baseline. Any future model must have at most 10,000,000,000 total parameters; quantization is not a parameter-count exemption.
- All tool calls have bounded timeouts and retries, and no cross-case cache. Failed attempts are logged without credentials.

Acceptance:

1. Offline unit tests exercise policy precedence, false claims, candidate ambiguity, conflicting sources, refunds, scope isolation and model rejection.
2. `day09 validate-inputs` confirms the official set of exactly 100 cases.
3. Live tool discovery and schema-validated evidence retrieval succeed.
4. `day09 validate` and independent semantic checks pass on all generated cases.
5. ZIP contains only manifest.json, trace.jsonl and the exact 100 outputs, without credentials.
6. Source lint and regression tests pass. Starter release-safety test applies to distributable source, not ignored local input/output artifacts.

Submission to the external scoreboard is a separate action after this artifact is inspectable. No hidden oracle or reference output is accessed.
