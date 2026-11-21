# FORGE-5 — AI Engineering Council

**Turn ambitious Python ideas into software you can inspect, test and improve.**

[![CI](https://github.com/hctu4hbs-ux/FORGE-5/actions/workflows/ci.yml/badge.svg)](https://github.com/hctu4hbs-ux/FORGE-5/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/Status-Alpha-orange.svg)](CHANGELOG.md)

Five engineering perspectives. Persistent objections. Executable gates. **Evidence before release.**

[Quick start](#start-in-five-minutes) · [How it works](#meet-your-engineering-council) · [Real results](#tested-on-working-software) · [Documentation](#documentation) · [Contribute](CONTRIBUTING.md)

FORGE-5 gives a coding agent a practical engineering workflow for complex Python
software. A portable Agent Skill supplies the process; a zero-dependency Python
runtime keeps tasks, objections, decisions, checks and release evidence consistent
across sessions.

The council has five roles: **Architect**, **Principal Engineer**, **Debugging
Specialist**, **Security & Performance Engineer**, and **Product & Release Director**.
Your existing agent supplies reasoning, code edits and tools. FORGE-5 supplies the
coordination and verification machinery. It works with delegated reviewers or
explicitly disclosed sequential role passes.

> Status: 0.1.0 alpha. The runtime and example have real behavioral tests. This is
> not a claim of production readiness, autonomous correctness or superiority on
> software engineering benchmarks. Read the [observed validation](docs/validation.md).

## Meet your engineering council

| Expert | The question they must answer |
| --- | --- |
| **Architect** | Are the boundaries, contracts and failure modes sound? |
| **Principal Engineer** | Does the integrated implementation meet the acceptance criteria? |
| **Debugging Specialist** | Can we reproduce failures and prove the fixes with regression tests? |
| **Security & Performance Engineer** | Are trust boundaries, resource limits and measured budgets acceptable? |
| **Product & Release Director** | Can a user install it, use it and understand its real limits? |

Use FORGE-5 for multi-module tools, Python services and libraries, difficult
regressions, persistence/concurrency work, and releases that need inspectable
engineering evidence. The agent keeps building; the council keeps challenging
unsupported conclusions.

## Tested on working software

| Local validation | Observed result |
| --- | --- |
| Council runtime | **63 behavioral tests passed** |
| Statement + branch coverage | **89.10% combined** |
| DurableFlow example | **26 tests passed**, including real process death and recovery |
| Example workload | **200 jobs in 1.70 seconds**, concurrency 4, on the recorded machine |
| Expert review | **Five independent host role reviews**, renewed after repairs |

These are recorded local results, not a comparative AI benchmark or a promise
about your machine. Inspect the [validation report](docs/validation.md) and
[raw evidence](docs/evidence).

## What actually runs

- **Expert Conflict Engine:** structured objections, stable finding IDs, author-owned
  dispositions, regression evidence for fixes, and no high/critical risk waiver.
  Dropping a finding from a later review does not close it.
- **Persistent coordination:** a dependency-aware task DAG, recorded design
  alternatives, SQLite transactions, optimistic concurrency and a hash-linked audit log.
- **Freshness enforcement:** reviews bind both source contents/executable bits and
  the goal/task/decision context and the author's objection history. Edits invalidate prior evidence.
- **Atomic council rounds:** old approvals are revoked before dispatch; five results
  commit together. Reviewer errors become abstentions. Interrupted rounds block release.
- **Executable gates:** trusted argv commands, deadlines, process-group cleanup on
  POSIX, capped captured output, required test/security checks, optional lint/type/
  performance budgets, and a default test runner that rejects empty/skipped suites.
- **Bounded improvement:** run checks → review → host-supplied repair → fresh checks.
  Stop on repeated unchanged blockers instead of unbounded agent conversation.
- **Host-neutral interfaces:** JSON CLI, typed Python SDK, role packets and a
  subprocess adapter protocol. No mandatory model vendor, API key or paid service.

## Start in five minutes

Requirements: Python 3.11+, a trusted project workspace, and a coding agent that can
read skills and execute local commands. POSIX is the validated execution platform.

Clone the repository and install the runtime:

```bash
git clone https://github.com/hctu4hbs-ux/FORGE-5.git
cd FORGE-5
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .

# Copy the skill into a directory your agent discovers.
python scripts/install_skill.py --dest /absolute/path/to/project/.agents/skills
```

The installer creates `forge-5/` under the destination and refuses overwrites. Use
your host's documented discovery directory (for example `.claude/skills` for a
Claude Code project). The skill follows the open Agent Skills format; each host's
discovery, delegation and command permissions still apply. See
[installation and compatibility](docs/installation.md).

In your target Python project:

```bash
forge5 init --goal "Build a durable, dependency-aware workflow engine"
forge5 task add storage --title "Atomic persistence" --owner architect \
  --acceptance "Restart, rollback and duplicate-submission tests pass"
forge5 task add executor --title "Concurrent DAG execution" --depends storage \
  --acceptance "Retries, lease recovery and failed dependencies pass"
```

Ask your agent:

> Use FORGE-5 to implement this project. Build and integrate working slices, run
> the checks, ask the five experts for evidence-backed review, resolve objections,
> and give me the final report with measured results and remaining limits.

The runtime does not silently invoke a model or execute code on initialization.
Inspect `forge5.toml`, implement the project and its tests, then:

```bash
forge5 check --execute
forge5 review packet architect > .forge5/architect-packet.json
# Have a real reviewer inspect the packet and source; save their JSON response.
forge5 review submit .forge5/architect-review.json \
  --digest SOURCE_DIGEST_FROM_PACKET --context CONTEXT_DIGEST_FROM_PACKET
# Repeat for principal, debugger, security and product.
forge5 report --output .forge5/report.md
forge5 release
```

Tasks must also be completed with evidence. A successful `release` exits 0 only
when every local policy condition passes. This command reports readiness; it does
not publish, deploy or grant approvals on the user's behalf.

## Why a conflict engine?

A reviewer can object to a design without that objection disappearing into a chat
transcript. Each finding has an author, stable ID, severity, evidence and a recorded
resolution. For example, the Debugging Specialist identifies a duplicate-execution
race. The Principal Engineer repairs the transaction, a check reproduces the old
failure and verifies the patch, and the original reviewer closes the finding:

```bash
forge5 resolve debugger.claim-race --actor debugger --disposition fixed \
  --rationale "Atomic claim transaction now fences stale workers; regression passed" \
  --evidence tests
```

The author must then renew approval. All open findings block release. False
positives can be rejected with an explanation; low/medium risks can be accepted
explicitly. Local role labels are a workflow contract, not authenticated identities.

## Use a real reviewer adapter

Let your agent host dispatch packets directly, or connect a trusted reviewer
executable that accepts one packet on stdin and returns one review JSON object:

```bash
forge5 council --adapter-json '["/absolute/path/to/reviewer"]' --execute
forge5 council --adapter-json '["/path/to/reviewer"]' \
  --repair-json '["/path/to/repair-agent"]' --rounds 3 --workers 5 --execute
```

An optional [Codex CLI bridge](adapters/codex_reviewer.py) integrates a locally
authenticated Codex installation in read-only mode. Its protocol is covered by
isolated executable-fixture tests; a live paid/model run is not claimed. Custom
agents can use the [adapter contract and SDK](docs/adapters.md).

## A substantial example

[DurableFlow](examples/durableflow) is a multi-module Python workflow executor with
SQLite persistence, DAG dependencies, atomic claims, token-fenced leases, crash
recovery, retry/backoff, idempotent submission and bounded parallel execution.
It includes real concurrency/recovery tests, a CLI and a measured workload.

See [validation](docs/validation.md) for actual test counts, environment, discovered
defects and limits. Protocol fixtures validate orchestration; they are not evidence
that an LLM can solve arbitrary engineering tasks.

## Try the durable workflow example

After installing FORGE-5, run a real multi-module project:

```bash
cd examples/durableflow
python -m unittest discover -s tests -v
python bench.py
```

For the submit/run/status CLI journey, follow the
[DurableFlow guide](examples/durableflow/README.md).

## Documentation

| Guide | What you will find |
| --- | --- |
| [Installation](docs/installation.md) | Runtime, portable skill and host compatibility |
| [Architecture](docs/architecture.md) | State invariants, atomic rounds and release policy |
| [Adapters & SDK](docs/adapters.md) | Native host integration, command reviewers and repair loops |
| [Validation](docs/validation.md) | Measurements, discovered defects, repairs and limits |
| [Research](docs/research.md) | Design positioning and primary project references |
| [Security](SECURITY.md) | Trust model and vulnerability reporting |
| [Changelog](CHANGELOG.md) | Version history |

## Development

```bash
python -m pip install -e '.[dev]'
python -m coverage run -m unittest discover -s tests -v
python -m coverage report
python -m ruff check src tests scripts adapters
python -m mypy src/forge5
python -m bandit -r src/forge5 -c pyproject.toml
python -m build
```

CI exercises supported Python versions, checks formatting/types/security, builds
distributions and runs the example. [Contributions](CONTRIBUTING.md) should include
reproducible behavioral evidence. The project uses the [MIT license](LICENSE).

## Boundaries

FORGE-5 depends on the capability of its host model and tools. It cannot guarantee
arbitrary project completion, independent judgment, full security coverage or
commercial success. Commands execute with host permissions; this runtime is not
a sandbox. The built-in AST scanner is a small triage tool, not a dependency audit
or formal security assessment. Source freshness and audit hashes are local
consistency checks, not signed attestations. Read the
[security model](SECURITY.md) and [architecture](docs/architecture.md).

## Help shape FORGE-5

If this workflow makes your engineering work more reliable, **star the repository**
so others can find it. The most valuable contributions are difficult reproducible
cases: a missed regression, an unsupported approval, a better adapter, or a real
project evaluation with an equal-budget baseline.

[Report a bug](https://github.com/hctu4hbs-ux/FORGE-5/issues) · [Contribute a fix](CONTRIBUTING.md) · [Read the evidence](docs/validation.md)
