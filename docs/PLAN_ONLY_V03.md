# v0.3: bounded decisions, deterministic output

The first submitted source revision `decd1ad` received public score **58.9428**.
It had 65 explicit fallback abstentions. This version changes the design; it does
not claim a higher score before live evaluation.

Set `MODEL_DECISION_MODE=plan` to enable the new path. The model can choose only
listed issue categories, dated order-version indices, capture/refund event indices,
claim verdicts and a confidence tier. It cannot emit literal amounts, arbitrary
entity IDs, source references, action strings or the final submission JSON.

Python performs:

1. Identity resolution when one real candidate is linked by received customer
   history; conflicting dates remain a downstream version problem.
2. Record-local date comparisons and explicit version-conflict observations.
3. Plan validation and binding to actual received evidence.
4. Decimal sums, completed-refund subtraction and policy amount limits.
5. Exact entity IDs, compatible policy action codes and official JSON construction.
6. Independent arithmetic, schema, claim coverage, source scope and refund checks.

The `net` arithmetic operator has a structural `positive_count` identifying the
capture portion of its operand list; the rest are settled refunds. It never accepts
literal monetary operands. The independent verifier recomputes the same source math.

Important limits:

- Model choice of a valid source row can still be semantically wrong. Dated
  transaction versions are not silently merged or declared authoritative.
- Pending/failed refund requests are not completed refunds.
- A technical tool error is not a business refund failure.
- Missing refund evidence remains null and cannot authorize a refund.
- Confidence tiers are documented heuristics, not empirically calibrated probabilities.
- Local tests and valid JSON do not establish hidden-case correctness.

Inspired by the local LASTDANCE grounding study: choose from real source candidates,
compile a typed plan, compute deterministically, then verify independently. No old
competition answers, 14B models or mismatching-plan acceptance paths were copied.

The v0.2 submitted run is preserved. This branch must be committed/pushed and
evaluated in a fresh isolated run before another submission.
