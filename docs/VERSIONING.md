# KINGPRO version and submission policy

The original course repository is `upstream`. The personal GitHub fork is
`origin`: https://github.com/Clownnvd/K4-L3B-MultiAgent-MCP-A2A.

## First checkpoint

`work/v0.1.0-scaffold` holds the initial in-progress implementation. It is not a
release tag, has known incomplete tests, and must not be submitted. `main` stays
at the course starter until a verified implementation is ready.

## Subsequent versions

1. Implement and inspect changes on a work branch.
2. Run full tests, lint, output validation and secret checks.
3. Commit source before submission, then push that exact commit to the fork.
4. Tag a verified source revision (`v0.1.0`, `v0.2.0`, and so on). Never move or
   overwrite a published version tag.
5. Build the submission from a clean source revision. Keep the commit ID,
   artifact SHA-256, model checkpoint/settings and validation receipt together
   in ignored local run metadata, not as extra files inside the competition ZIP.
6. Submit only after the artifact is verified and submission is authorized.
7. Record the returned submission ID and result against the exact commit and
   artifact hash. Do not report a score before the server returns one.

Never publish `.env`, credentials, local evidence dumps, official case payloads,
generated outputs, traces or competition ZIP files. A public source checkpoint
is not proof of an accepted competition submission.
