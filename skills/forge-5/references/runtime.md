# Runtime workflow

Use `forge5 --help` for the complete interface. All commands accept `--root PATH` before the subcommand. The default root is the current directory.

```bash
forge5 init --goal 'Build a durable workflow engine'
forge5 task add storage --title 'Transactional persistence' --owner architect --acceptance 'Rollback, restart and deduplication tests pass'
forge5 task add scheduler --title 'Dependency-aware execution' --depends storage --acceptance 'Retries, failed dependencies and concurrent workers behave correctly'
forge5 task set storage running
forge5 check --execute
forge5 task set storage done --evidence 'tests/test_store.py: restart and rollback cases passed'
forge5 review packet architect > .forge5/architect-packet.json
forge5 review submit .forge5/architect-review.json --digest DIGEST_FROM_PACKET --context CONTEXT_DIGEST_FROM_PACKET --mode host
forge5 resolve debugger.retry-loss --actor debugger --disposition fixed --rationale 'Atomic lease acquisition verified' --evidence tests
forge5 report --output .forge5/report.md
forge5 release
```

Review JSON shape:

```json
{
  "role": "debugger",
  "verdict": "request_changes",
  "summary": "A restart can execute an already committed job twice.",
  "findings": [{
    "id": "retry-loss",
    "severity": "high",
    "title": "Non-atomic completion",
    "detail": "The queue commit and result commit occur in different transactions.",
    "evidence": "src/queue.py:81; tests/test_store.py::test_restart reproduces the failure"
  }]
}
```

Use role values `architect`, `principal`, `debugger`, `security`, `product`. Verdicts are `approve`, `request_changes`, `abstain`. Severity values are `critical`, `high`, `medium`, `low`. Every unresolved finding blocks release, including low severity; either fix or explicitly dispose of it. A role can only close its own findings. This is a logical role constraint, not identity authentication.

Configure checks in `forge5.toml`. `{python}` resolves to the runtime's interpreter. Arguments are an array, never a shell string. Check names must be unique. Always require at least one `test` and one `security` check. Add `kind = "performance"` with a `max_seconds` budget for measured workloads. Include a real dependency scanner for third-party packages when available; built-in AST triage is incomplete.

Project input paths, bytes and executable bits are content hashed. Reviews also require the packet context digest over goals, tasks, decisions and that author's finding/disposition history. Always use each role's own packet context digest. Excluded generated directories: `.git`, `.forge5`, `.venv`, `venv`, `__pycache__`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `build`, `dist`, `htmlcov`, `*.egg-info`, `.coverage*`. Do not put source or tests in excluded paths. Source symlinks, more than 100000 files or files over 16 MiB are rejected. Run with writers stopped. The hash is a local freshness check, not an authenticated attestation or a guarantee against filesystem races.

A council round revokes prior approvals before dispatch and commits all five results atomically. Interrupted or stale rounds block release. Retry the round, or use `forge5 round-cancel --reason "why it was interrupted"`; cancellation keeps approvals revoked. The default `forge5 test` command rejects empty and entirely skipped unittest suites. Custom test commands remain responsible for meaningful assertions.
