# Paired repair evaluation

The evaluation harness executes repairs on five separate, generated Python projects. Each project contains a source module, a service facade and protected acceptance/regression tests. It compares a direct implementation provider with the actual FORGE-5 development orchestrator, including planning, bounded implementation, staged checks, five review requests and release eligibility.

## Reproduce the offline integration evaluation

After installing the project into your active Python environment:

```bash
forge5 evaluate --mode fixture --execute --allow-local \
  --run-id offline-five --format markdown
forge5 evaluate --mode fixture --execute --allow-local \
  --run-id offline-five --resume --format json
```

Use `--case logic` (repeatable) to select a subset, `--rounds 3` for the repair iteration budget and `--timeout 30` for the per-process wall clock limit. Global `--root PATH` selects the parent project before the subcommand. The evaluator writes `report.json`, `report.md`, per-case checkpoints and actual workspaces below `.forge5/evaluations/RUN_ID/`. A nonzero CLI exit means that at least one arm failed.

**Fixture mode is a protocol integration test, not an AI benchmark.** Both arms receive the same explicitly programmed repair oracle. The five review identities use the same fixture executable; they do not represent five independent AI models. A passing fixture run demonstrates that actual checks and orchestration execute together. It establishes neither model superiority nor production readiness.

## Cases

| Project | Defect | Protected acceptance check | Regression check |
| --- | --- | --- | --- |
| logic | Discount treated as an absolute amount | Fractional and full discounts before tax | No-discount invoice |
| concurrency | Read/sleep/write counter race | 160 increments across eight workers | Serial increments |
| security | Resource path escapes its root | Traversal and symlink escapes rejected | Local resource resolution |
| migration | Non-idempotent SQLite migration | Repeated migration preserves legacy rows | First migration adds the column |
| performance | Repeated linear membership scans | Equality-operation budget on hashable values | Query order and duplicates preserved |

The performance case counts operations rather than asserting a fragile timing threshold. Measured elapsed seconds are also reported. Tests import through the service facade, so repairs run as multi-file projects. These intentionally small regression fixtures do not replace evaluation on a representative application.

## Configured providers

```bash
forge5 evaluate --mode providers --agents /absolute/path/agents.json \
  --baseline-json '["/absolute/path/python","/absolute/path/direct-agent.py"]' \
  --execute --allow-local --run-id provider-trial --format markdown
```

The agents configuration follows [the platform guide](platform.md). The baseline executable receives one JSON `forge5.implement.v1` request on stdin per round and returns the same declarative `changes` format as the implementation agent. Its request includes the goal, scoped task, round, workspace root and previous check results. Both arms begin with fresh identical buggy files and identical protected tests; both receive the same maximum iteration budget and per-process timeout. FORGE-5 makes additional planning and review calls: equal iteration budgets do **not** imply equal tokens, elapsed time or cost. Provider/model identities are configuration declarations, not authenticated evidence of model independence.

The baseline may edit only the declared defective source files through its returned changes. Preimage hashes prevent stale writes. Existing tests are protected; changed tests invalidate success. The FORGE-5 arm also requires current release eligibility, rather than merely green acceptance tests.

## Evidence, resumption and limitations

Reports retain before/after unittest output, exit codes, failure/error/test counts, wall time, process-limit information, provider calls, actual development rounds, new regressions, source fingerprints and the full orchestrator outcome. Cost is `null`: this command does not infer billing from elapsed time or claim access to authenticated token usage. Missing provider usage is not zero cost.

A completed successful arm is reused only when its source fingerprint matches. Failed arms can be retried with `--resume`; their original tests and initial measurements remain protected. Case definitions, fixture provider, engine source code, configured agent file, command arguments and budgets form the resume contract. A changed contract or completed workspace requires a new run ID. An interruption before the initial trusted checkpoint is written also requires a new run ID. Provider executables or remote services can change independently of their command arguments: maintain immutable executables and provider versions for rigorous comparisons.

Evaluation currently requires explicit **unisolated local execution**. Local command providers are trusted programs: this is not an OS sandbox and does not prevent direct malicious filesystem access or networking. Process limits are recorded by the process runner, but isolation support in the separate agent runtime does not make this entire evaluation isolated. Run untrusted providers only inside a separately secured environment. No live provider quality or cost comparison is shipped as a claimed result.
