# Model-ready orchestration contract

Requested outcome: a complete, locally executable multi-agent scaffold with a swappable model boundary. Offline demonstration must be clearly separated from authenticated competition output.

## Boundaries

1. Entity agent receives input hints and actual candidate evidence, returning a structured resolution.
2. Coordinator dispatches order/product, shipment, payment/refund and policy specialists. Permissions and evidence caches are case scoped.
3. Conflict/policy stage receives raw evidence and structured specialist handoffs. It proposes the official output and numeric source bindings, not arbitrary executable code.
4. Arithmetic executor evaluates a small allowlist of operations over numeric values located by JSON pointers in actual evidence. No positive literal constants are allowed as evidence operands.
5. Independent verifier validates JSON Schema, entity scope, evidence references, claim coverage and cross-field financial invariants. Optional critic can reject a result but cannot silently overwrite it.
6. Batch runner writes separate case checkpoints, then a complete trace and run receipt. A failed case cannot be replaced with fabricated output. Re-running creates a new directory instead of deleting earlier results.
7. Packaging allows live runs only and checks evidence linkage and lifecycle order. Demo artifacts cannot be packaged as submissions.

## Model interface

`DecisionModel.complete(task, payload) -> JSON object`, used for entity resolution and the final policy proposal. Default live adapter is disabled. A future OpenAI-compatible endpoint must serve an explicitly approved checkpoint with known total parameters at most 10B. No OpenAI or Gemini closed model is silently substituted. A model with unknown size is refused.

## Checks before handoff

- Red/green offline tests for the full agent handoff, invalid candidates, forged evidence, arithmetic tampering, critic rejection and model limit.
- Synthetic 100-case demo completes through the real orchestrator, source-bound arithmetic, schema validation and verifier. Demo refs have a `demo` marker and never go to the competition server.
- Packaging rejects demo runs, incomplete runs, stale/tampered checkpoints, unconsumed evidence and invalid lifecycle order.
- `ruff check` and the complete source suite pass. Release-safety checks inspect tracked distributable files; ignored official input files are permitted in a learner's workspace.
- Live readiness stays unverified until the valid Team API Key, a model endpoint and representative real evidence responses are available. No claim that model attachment alone guarantees competition correctness.
