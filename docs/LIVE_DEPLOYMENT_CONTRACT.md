# Live deployment acceptance contract

Authorized on 2026-09-25: rent an appropriate FPT GPU, deploy a model below 10B,
complete the system, run the competition workflow, and commit before submission.

## Boundaries

- Use the existing FPT workspace. One GPU/model instance, no autoscaling.
- Check actual portal pricing and runtime compatibility before provisioning.
- User explicitly requested keeping the GPU/model running for several hours and
  ready for repeated runs. Do not enable idle shutdown or stop it after tests;
  report the active resource and hourly charges at handoff.
- Keep model access and competition credentials local and excluded from Git.
- Model candidate: Qwen/Qwen3.5-9B, verified full tensor count 9,653,104,368.
- Do not use another team's outputs, private answers or case-specific overrides.
- Never fabricate evidence when an MCP tool fails. A valid team key does not
  imply every tool or case works.

## Work ownership

- Coordinator: deployment, model adapter, live evidence, workflow integration,
  regression checks, commits and submission decision.
- Demo worker: synthetic fixtures and focused tests only.
- Batch worker: isolated runs, packaging guards, CLI and focused tests only.
- Research worker: public repositories, read-only; no solution output imports.

## Acceptance

1. Model identity and actual service runtime verified, then JSON smoke test.
2. Offline demo completes 100 cases through real orchestrator and verifier.
3. Full pytest and ruff checks pass; deployment evidence is separately reported.
4. A small live run passes before attempting all 100 official cases.
5. Complete live outputs, source linkage, trace and package validate.
6. Secret scan, clean source commit and pushed revision precede submission.
7. Submission receipt and actual score, if submitted, are recorded against the
   immutable commit and artifact hash. No score is claimed without server evidence.

Current service limitation observed: get_refund_timeline returned a tool error on
the first inspected official case, while nine other tools returned evidence.
This requires investigation, not an invented empty refund response.
