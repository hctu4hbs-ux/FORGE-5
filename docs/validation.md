# Observed validation — 0.1.0 alpha

Observed on **2026-11-21 (Asia/Riyadh)**, Linux x86_64, Python 3.12.14,
SQLite 3.53.1. Results below are local measurements. Remote GitHub Actions,
other Python versions and other operating systems are not claimed as executed.

## Runtime and installation

| Check | Observed result |
| --- | --- |
| Runtime behavioral suite | 63 tests passed; exact log in `evidence/runtime-tests.txt` |
| Coverage (statements + branches) | 89.10% combined; 91.85% statements, 81.09% branches |
| Ruff | Formatting and lint checks passed for source, tests, scripts and adapters |
| mypy | Strict check passed for 13 runtime source files |
| Bandit | Zero unhandled findings; B404/B603 explicitly reviewed for the trusted argv runner |
| AST security triage | Zero findings on runtime source; known unsafe API fixtures detected in tests |
| Packaging | Wheel and source distribution built; wheel installed without dependencies in an isolated environment; installed `forge5 --version` returned 0.1.0 |
| Skill | Frontmatter/name/structure validation passed; installer copied references and rejected overwrite |
| Codex bridge | Read-only argv/schema/final-JSON protocol exercised through a real executable fixture; malformed packets failed closed |
| Live Codex/provider call | Not run: available CLI was unauthenticated; no model-provider benchmark is implied |

The suite exercises concurrent writers, transactional rollback, DAG transitions,
acceptance-context replay rejection, atomic review rounds, stale source/evidence,
conflict disposition rules, reviewer failures, timeouts, output overflow, literal
shell metacharacters, malformed contracts, real subprocess adapters and CLI
release behavior. Test adapters are explicitly fixtures; approval fixtures are
not independent engineering judgments.

## Defects discovered and repaired

Independent read-only security/debugging reviewers found concrete defects during
implementation. Their reproductions informed fixes and regression tests:

| Defect | Repair and verification |
| --- | --- |
| Old host review could restore approval after task/decision changes | Required source **and context** digests; old plan packet regression |
| Pre-objection approval packet could be replayed after its author's disposition | Per-role authored finding/history context; old packet rejected and a fresh atomic round succeeds |
| Partial review commits could retain a previous approval after a new reviewer failed | Revoke approvals at round start, active-round blocker, one atomic five-role completion; deterministic concurrent-write regression |
| chmod could change execution behavior without invalidating evidence | Hash executable bits and compare mode during snapshot stability check; chmod regression |
| Audit verification loaded all historical command output into memory | Stream hash verification within one read transaction; 8 MiB-history memory regression and measured 50 MiB-history workload |
| Standalone scanner blocked on a `.py` FIFO | Require regular files and cap Python file size; FIFO regression |
| Default zero-test success could look like valid test evidence | Default `forge5 test` rejects empty/all-skipped discovery |
| Fixture-level unittest skips could incorrectly cancel a real executed test count | Track started cases and direct case skips, excluding fixture/subtest skip markers; mixed fixture/real-test regression |
| Checkout-only helper's subprocess checks could not import an uninstalled runtime | Propagate its verified absolute source path; fresh package-free virtual-environment journey regression |

These are observed repairs, not a claim that all defects have been found.
All five runtime role reviewers renewed approval after the relevant repairs.
Their actual summaries are preserved in `evidence/runtime-reviews.json`; they
are independent native agent reviews within the documented scope, not human audits.

## Runtime workload sample

Run `python scripts/benchmark.py` to reproduce the workload. One measured sample:

| Workload | Observed |
| --- | --- |
| Record 250 independent task contracts | 1.154 seconds |
| Fingerprint 1000 small Python source files | 0.061 seconds |
| Verify history with 400 × 128 KiB output payloads | 0.261 seconds |
| Peak Python allocation during streamed verification | 430892 bytes (about 0.411 MiB) |
| SQLite database after workload | 52801536 bytes (about 50.36 MiB) |

See `evidence/runtime-benchmark.json`. This is a single local sample with no model
invocation. It does not establish distributed scalability or a latency SLA.

## DurableFlow forward test

A fresh implementation agent received the portable skill and a request to build
a durable DAG executor. It developed the example in a separate subdirectory,
then obtained fresh, read-only reviews from the five roles. The reviewers were
not given the parent's suspected bugs or expected verdicts.

The resulting multi-module Python project provides SQLite persistence, atomic
claims, token-fenced leases, recovery after process death, idempotent submission,
retry/backoff, dependency failure propagation, bounded parallel workers and a CLI.

| Check | Observed result in final example gate |
| --- | --- |
| Behavioral tests | 26 passed in 3.338 seconds; process wall time 3.497 seconds |
| Workload | 200 jobs, concurrency 4, 1.699600 seconds against a 10-second budget |
| AST security triage | Zero findings |
| Independent host role reviews | All five approved, findings empty |
| Local `forge5 release` | Exit 0, no blockers; revision 30 |
| Product install check | Isolated installation and installed example CLI exercised |

Tests include four separate claim processes, 12 claiming threads, 24 concurrent
idempotent submissions, actual worker process death and recovery, token expiry,
heartbeats, retry deadlines, transitive dependency blocking and CLI subprocesses.
The debugging review additionally injected an SQLite trigger failure to verify
submission rollback. The full status, report and 30-event audit are checked in
under `docs/evidence/durableflow-*`; they refer to the example snapshot, not to
every future checkout. Review timestamps are UTC in runtime records.

This is one successful example and useful workflow evidence. It is not a blinded
comparative benchmark of AI capability. Native host subagents supplied actual
reviews; deterministic protocol fixtures were used only in runtime tests.

## Limits and next evaluation work

The council does not authenticate reviewer identity or guarantee independence.
Source fingerprints do not cover external services, interpreter dependencies,
environment secrets or race-proof confinement. The scanner is limited AST triage;
there are no third-party runtime dependencies in this release. Trusted commands
still need host isolation. In-process callbacks need host cancellation.

DurableFlow is a single-host SQLite example with trusted handlers, at-least-once
external side effects, stable-wall-clock assumptions, no hard thread cancellation
and no payload quotas. Its README documents those limits. It is not a distributed
workflow service or a production certification.

Future effectiveness evaluation should use unseen projects, pinned inputs,
independent acceptance tests, equal budgets, baseline runs, repeated trials and
published failures. No repository popularity, commercial outcome or general
engineering superiority is claimed.
