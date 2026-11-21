# Engineering platform: configuration and operation

The expanded platform connects executable providers to persisted engineering
state. It does not include model credentials, a hosted LLM service or an automatic
choice of a paid provider. Use it in a trusted execution environment and inspect
provider commands, project policy and proposed changes before authorizing them.

The 0.1.0 evidence in `docs/evidence/` describes the historical implementation.
Do not treat those results as validation of later platform changes. Current
validation commands and evaluation results must be rerun on the revision in use.

## Components and ownership

| Component | Responsibility |
| --- | --- |
| `agents.py` | Six separately configured command providers and distinct role contracts |
| `development.py` | Planning, bounded task implementation, checks, review/repair and resume state |
| `tools.py` | Role-scoped, audited operations against the project workspace |
| `isolation.py` | Explicit local execution or resource-limited Docker/Podman invocation |
| `engine.py`, `models.py` | Task transitions, findings, dispositions and freshness policy |
| `architecture.py`, `memory.py` | Import contracts and searchable decisions, constraints and lessons |
| `quality.py` | Additional quality inventory/triage and optional dependency-audit integration |
| `evaluation.py` | Matched baseline/council experiments on distinct Python repair cases |

The implementation provider owns code and test proposals for an assigned task.
Reviewers inspect those artifacts through their own contracts. An implementation
response is not permission to close the objections raised against it.

## Install

```bash
git clone https://github.com/hctu4hbs-ux/FORGE-5.git
cd FORGE-5
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python scripts/install_skill.py --dest /absolute/path/to/project/.agents/skills
```

The core runtime has no mandatory third-party dependencies. The development extra
installs the repository's lint, type, security, coverage and build tools. A target
project can require additional packages and checks. The portable skill remains
compatible with hosts that discover standard `SKILL.md` directories; skill
installation does not grant tool permissions or provision model access.

## Configure six providers

Copy `examples/agents.example.json` to a target-controlled `forge5-agents.json` and
replace its placeholders with real executables. It is an example configuration,
not a working model provider or a fixture that approves every review.

```json
{
  "schema": 1,
  "agents": {
    "architect": {
      "argv": ["your-architect-command"],
      "provider": "your-provider",
      "identity": "architect-session",
      "model": "your-architect-model",
      "timeout": 180
    }
  }
}
```

The shortened example shows one entry; the actual registry requires all six keys:
`architect`, `principal`, `debugger`, `security`, `product`, `implementation`.
Each entry requires `argv`, `provider` and a distinct `identity`; `model` and
`timeout` are optional. Commands are argv arrays, never shell strings. Keep model
credentials outside this JSON and outside the repository, using the provider's
normal credential mechanism.

Each invocation starts a fresh command process and receives a role-specific
mission, permitted tool description, required deliverables and evaluation
criteria. Models/providers/hosts may differ per entry. Distinct declared
identities prevent an accidentally duplicated configuration, but do not
cryptographically authenticate a reviewer or establish that different model
backends were used. Reports must disclose fixture, sequential or hosted execution
accurately.

The legacy read-only `forge5.review.v1` bridge in `adapters/codex_reviewer.py` remains
available for its documented review protocol. It is not automatically a planner
or implementation provider: a provider must implement the development request
and response contracts it receives. Never reuse a review-only adapter as an
implementation agent and assume it can write code.

## Run a development cycle

Create the target directory, configure its real provider commands and checks,
then run:

```bash
forge5 --root /path/to/project plan --goal "Build the requested Python tool" \
  --agents /path/to/forge5-agents.json --execute
forge5 --root /path/to/project build \
  --agents /path/to/forge5-agents.json --rounds 3 --execute --format markdown
```

`plan` obtains a structured dependency-aware plan. Tasks carry acceptance
contracts and explicit file ownership. The orchestrator persists its phase and
progress under `.forge5/` rather than relying on chat context. Implementation
work is limited to an assigned ready task. Proposed contents and pre-change
hashes form a reviewable change set; source changes and evidence are inspected
before integration and subsequent review.

The cycle runs configured checks, obtains the five reviews, retains objections,
requests implementation responses/repairs and repeats within the round budget.
A blocked result is a useful outcome: preserve its exact checks, finding IDs and
provider error rather than weakening policy. Re-run `build` with the same project
state to resume; inspect an interrupted change set before retrying. External
providers and side effects are not guaranteed exactly-once across a host crash.

Use JSON for machine consumption and Markdown for human review. Keep generated
reports under `.forge5/` so saving them does not modify source inputs.

## Isolation and permissions

New development execution defaults to fail closed when the requested isolation
cannot be supplied. Container execution requires an operational Docker or Podman
runtime and an explicitly configured, already-installed image pinned as
`IMAGE@sha256:DIGEST`. Automatic image pulls are disabled. The image must contain
Python, the runtime, project dependencies and provider commands needed by that
invocation; a host executable path is not automatically available in the image.

Pass an isolation-policy JSON file with `--isolation /path/to/isolation.json` to
`plan`, `build`, `review run` or `benchmark`. For example, replace the image digest below with
the digest of your trusted, locally installed provider image:

```json
{
  "mode": "container",
  "image": "your-registry/forge5-provider@sha256:REPLACE_WITH_64_HEX_DIGEST",
  "timeout": 180,
  "memory_mb": 512,
  "cpus": 1,
  "pids_limit": 128,
  "output_limit": 131072,
  "writable": false
}
```

This is an explanatory placeholder, not an executable image reference. Container
mode refuses local fallback even if `--allow-local` is present. `auto` can fall
back only with the explicit local opt-in. In the implementation environment,
Docker and Podman executables were unavailable; container command construction
can be fixture-tested there, but a real container run must not be claimed.

The execution policy can limit wall time, captured output, memory, CPU and process
count. Container commands disable network, drop capabilities, enable
`no-new-privileges`, run without root privileges, use a read-only root filesystem
and mount the workspace read-only unless explicitly configured otherwise.
Container cleanup is attempted by its unique name even when the client times out.
These controls depend on the engine/kernel and are not a formal isolation proof.

For providers and code you trust, `--allow-local` explicitly permits fallback:

```bash
forge5 --root /path/to/project build --agents /path/to/forge5-agents.json \
  --rounds 3 --execute --allow-local --format json
```

Local execution inherits host permissions and environment. It does **not** enforce
filesystem, network, CPU or memory isolation. Process-group cleanup on POSIX does
not contain detached descendants. Reports record the actual backend and missing
protections; the presence of a `docker` binary alone is not proof of isolation.

The broker restricts operations by role, validates workspace paths and records
successful and denied calls. Its audit covers broker-mediated operations and
provider invocation metadata; it cannot observe arbitrary out-of-band tools used
inside a trusted external process. A local provider can bypass a broker by using
its own host capabilities. Configure a host sandbox/container if that threat is
relevant. Do not represent declared tool lists as enforced OS permissions.

## Conflicts, memory and release

Findings carry stable role-qualified IDs, severity, claim, evidence, affected
locations, a required test, implementer responses and a lifecycle. An implementer
can respond or dispute a claim but cannot approve its own repair. A fix requires
current passing evidence and review by the objecting role or the supported
independent-review path. High/critical risk cannot be waived. Explicitly track
`open`, `disputed`, `needs_evidence`, `fixed`, `rejected`, `accepted_risk` and
`reopened` rather than deleting discussion. Legacy open/closed fields remain for
backward compatibility.

Architecture checks inspect static Python imports for forbidden dependencies,
layer-direction violations and strongly connected components. Dynamic imports
and runtime plugin loading need additional review. Record constraints and lessons
through the engineering-memory API so they remain searchable and participate in
review freshness. Do not edit hidden policy files to manufacture a pass.

`forge5 release` derives readiness from current state: required tasks completed,
current check evidence, five current approvals, no unresolved finding and no
incomplete review round. Source bytes/paths/executable bits, plan/decision context
and each author's finding history invalidate stale approvals. Release reporting
is distinct from authorization to publish or deploy.

## CLI reference

Use `forge5 --help` and the relevant subcommand's help for the complete interface.
`--root` precedes the subcommand. New platform commands expose `--format json` or
`--format markdown` and `--output PATH`.

| Command | Operation |
| --- | --- |
| `forge5 plan` | Invoke the configured architect and persist the task DAG |
| `forge5 build` | Execute/resume bounded implementation and review/repair rounds |
| `forge5 review run` | Invoke the five configured reviewers; legacy packet/submit remain available |
| `forge5 conflict` | List/show findings, respond/dispute/request evidence, dispose or reopen |
| `forge5 security` | AST/secret triage, package inventories and optional dependency audit |
| `forge5 benchmark` | Execute named configured performance checks and budgets |
| `forge5 architecture` | Check static project imports against selected contracts |
| `forge5 memory` | Add/search persistent decisions, constraints and lessons |
| `forge5 evaluate` | Run or resume matched fixture/provider evaluations |
| `forge5 release` | Report the current local release gate |

Examples of discussion and memory operations:

```bash
forge5 conflict list --format markdown
forge5 conflict show debugger.claim-race
forge5 conflict respond debugger.claim-race --actor implementation \
  --message "The patch and regression test address the atomicity failure."
forge5 conflict needs_evidence debugger.claim-race --actor debugger \
  --message "Run the restart regression on the current source."
forge5 conflict fixed debugger.claim-race --actor debugger \
  --message "Verified the repaired transaction and restart behavior." --evidence tests
forge5 memory add "Use token-fenced leases for durable claims" --kind constraint --tag storage
forge5 memory search "token leases" --format markdown
forge5 security --format json --output .forge5/security.json
forge5 security --require-dependency-audit --format markdown
forge5 benchmark --execute --allow-local --name workload --format markdown
forge5 architecture --contracts /path/to/architecture.json --format json
forge5 review run --agents /path/to/forge5-agents.json --execute --allow-local
forge5 release
```

The conflict IDs and check names are illustrative and must already exist. A
reviewer must inspect evidence; naming a check is not a substitute for its real
passing current-source result. `--independent` is a trusted host declaration,
not an authenticated identity grant. The implementation role cannot use it to
close its own objections.

Persist architecture policy through the SDK so it participates in engineering
memory and invalidates approvals:

```python
from pathlib import Path
from forge5.engine import Council
from forge5.memory import set_contracts

council = Council(Path("/path/to/project"))
set_contracts(council, {
    "forbidden_imports": [{"from": "domain", "to": "transport"}],
    "layers": [["transport"], ["application"], ["domain"]],
})
```

Layer order is outer to inner: inner layers cannot import earlier outer layers.
The CLI's explicit `--contracts` performs a check with that JSON; it is not a
persistent policy edit. Preserve decisions/constraints through the memory API.

## Quality and evaluation

Configure meaningful test, lint, type, security and performance commands in
`forge5.toml`. The default test discovery rejects empty or entirely skipped
suites. Coverage, property-based tests and mutation tests are appropriate when
they can detect plausible incorrect implementations; configure their actual
commands and dependencies instead of claiming that a named check exists.

The quality extension reports bounded secret triage, dependency-audit availability,
and package SBOM/license inventory with their scope. A required unavailable audit
must block rather than silently pass. Installed-distribution metadata is an
inventory of that environment, not proof that a target's transitive lockfile is
complete. Pattern-based secret scanning and AST rules can miss vulnerabilities.

See [the evaluation guide](evaluation-v2.md) for logic, concurrency, security,
migration and performance cases, matched baselines, measured metrics and declared
fixture/provider modes. Deterministic offline adapters test orchestration and
repair mechanics; they are not evidence of independent LLM reasoning or general
engineering superiority. Costs remain unknown unless the provider supplies
credible usage/cost evidence.

## Operational limits

This remains an alpha API. Local role labels are not authenticated identities;
external commands are trusted unless isolated by the host; sandbox capabilities
are environment-dependent; static analysis is incomplete; and configured command
success is only as meaningful as its assertions. Review the current source,
reports and tests before using it with sensitive projects. No production or
unbounded autonomous capability is claimed.
