---
name: forge-5
description: Build, debug, review, harden and release complex multi-module Python software through a five-expert engineering council with executable quality gates, evidence-backed conflict resolution and bounded improvement loops. Use for substantial Python tools, services, libraries, migrations, difficult bugs, performance work and release readiness.
---

# FORGE-5 — AI Engineering Council

Turn the user's objective into working software and current, inspectable evidence. Use the runtime to enforce workflow invariants. Supply engineering judgment and implementation through the host agent; the runtime does not generate code by itself.

## Establish the workspace

1. Read the project's applicable instructions and inspect existing code before editing. Preserve user changes. Identify available execution, delegation, network and model capabilities. Respect host approval rules.
2. Define acceptance criteria, compatibility, data sensitivity, failure modes and measurable performance budgets. Resolve routine choices autonomously. Ask only for decisions that materially change the objective or authorization.
3. Use Python 3.11+ and the `forge5` CLI. If it is not installed, use the repository checkout: `python3 -m pip install /absolute/path/to/FORGE-5`. Do not install from an invented registry package or URL. The repository-local `skills/forge-5/scripts/forge.py` helper can run the checkout without installation. See [runtime.md](references/runtime.md).
4. In the target project, run `forge5 init --goal "..."`. Inspect and configure `forge5.toml` before executing commands. Require real test discovery and security review; add lint, types, dependency audit and measurable performance checks when relevant. Commands inherit host permissions; use the host's sandbox/container for untrusted code.

## Assign the five expert roles

Read [council.md](references/council.md) for responsibilities and objection rules.

| Role | Required contribution |
| --- | --- |
| Architect | Boundaries, contracts, failure isolation, alternatives and a dependency-aware task plan |
| Principal Engineer | Integrated implementations with clear APIs, typing and acceptance behavior |
| Debugging Specialist | Reproductions, regression tests, error paths and root-cause verification |
| Security & Performance Engineer | Threat model, trust boundaries, resource limits and measured performance |
| Product & Release Director | Usable installation, end-to-end behavior, docs, licensing and honest release evidence |

When delegation exists, dispatch fresh reviewers with role packets from `forge5 review packet ROLE`. Give reviewers the source and task artifacts, without a suggested verdict or suspected fix. Keep reviewer work read-only and isolate implementation edits. Use worktrees or explicit nonoverlapping ownership if parallel implementation is warranted. Never imply independence when one context performs all roles. Without delegation, perform five sequential passes and submit with `--mode sequential`; disclose that limit.

## Build in verifiable slices

1. Record consequential alternatives with `forge5 decide`. Create a task DAG using `forge5 task add`, including explicit acceptance evidence. Add dependencies before dependents.
2. Implement a coherent vertical slice. Run the narrowest useful regression check, then integrate with the surrounding system. Record completion evidence using `forge5 task set ID done --evidence "..."`.
3. Add tests that can detect plausible wrong implementations: boundary cases, fault injection, malformed inputs, concurrency, restart/recovery and compatibility as appropriate. Confirm that tests are discovered; a zero-test success is not proof.
4. Run `forge5 check --execute`. Read failed evidence and the actual code. Fix causes; rerun affected checks and then required integration checks. Do not weaken policy, delete regression tests, or alter acceptance criteria to manufacture a pass.
5. Export fresh packets and obtain all five reviews. Read actual diffs and artifacts. Import each JSON review with its packet's source and context digests; use `--revision` for additional optimistic concurrency when coordinating writers.

## Operate the Expert Conflict Engine

Every objection must state a stable ID, severity, concrete defect or tradeoff, and file:line or reproducible evidence. Request changes when a blocker exists; abstain when tools or evidence are missing. See [council.md](references/council.md).

- Keep objections open until their author evaluates the response. A later review that omits a finding does not close it.
- Resolve disagreements with a test, benchmark, interface contract or explicit recorded tradeoff. Do not use majority voting to waive a correctness/security blocker.
- For a fix, run a regression check on the current source and resolve with `--disposition fixed --evidence CHECK`. Require the objecting reviewer to examine the patch and renew approval.
- Record evidence and rationale when rejecting a false positive. Accept risk only for low/medium findings, with an explicit mitigation and product consequence. High/critical findings cannot be waived by the runtime.
- Reopen stable findings if the defect reappears. Never manufacture role identities, test results, independent reviews or consensus.

## Iterate and deliver

Use bounded rounds: checks → five reviews → objections → implementation/repair → fresh checks. Use `forge5 council` only when a real external reviewer command is available; follow [adapters.md](references/adapters.md). Do not configure an adapter that simply approves. Cross-review packets include previous reviews and open objections for subsequent rounds.

Stop repeated identical failures; report the exact blocker and missing capability. Do not claim a fixed iteration count makes an arbitrary project production ready. For large projects, validate incremental slices and the final integrated system; keep acceptance contracts and state outside context-window memory.

Run `forge5 release`. Source changes invalidate checks and approvals. Task or decision changes invalidate approvals. Save final reports under `.forge5/` so writing evidence does not change source inputs. A passing local gate is a prerequisite, not authorization to deploy or publish. Follow the user's publication authorization and host rules.

Deliver working code, reproducible commands, observed test/benchmark results, resolved and remaining findings, compatibility limits, and the next concrete step if blocked. Distinguish measured behavior from expectations. Never promise unlimited capability, commercial success or superiority over untested competitors.
