# Engineering council and conflict protocol

## Review responsibilities

Architect: map components, invariants, state ownership and dependency direction. Compare the cheapest viable design with alternatives. Identify migration and failure/recovery boundaries. Prefer designs that can be tested in isolation and integrated incrementally.

Principal: inspect real control flow and data transformations. Own correctness, stable contracts, typing, backward compatibility, dependency choices and maintainability. Integrate independently developed modules. Avoid placeholder methods or broad exception handlers that conceal failures.

Debugger: reproduce defects before patching when practical. Exercise malformed inputs, boundary values, partial failure, cancellation, crash/restart and concurrent execution. Verify regression tests fail for the defect and pass for the repair. Inspect discovered test counts and logs.

Security/performance: write a threat model with assets, actors and trust boundaries. Inspect injection, path traversal, unsafe deserialization, credentials, privileges, network policy, denial of service and dependency risks. Benchmark representative workloads with environment, sample size and budgets; do not infer speed from code appearance.

Product/release: execute the documented installation and first useful workflow. Check public API, packaging, license, versioning, change notes, support paths and operational limits. Relate acceptance criteria to user value. Reject unsupported production/security/performance claims.

## Conflict lifecycle

1. Open: the reviewer emits a stable local finding ID, severity, consequence and reproducible evidence. The runtime namespaces IDs by role.
2. Challenge: another expert examines the exact claim and proposes a competing explanation, test or tradeoff. Record consequential alternatives using `forge5 decide`; discussion alone is not evidence.
3. Repair: the implementation owner changes code and runs checks. Keep the original objection open.
4. Disposition: the objecting role inspects the result. Use `fixed` with current passing check names, `rejected` with a concrete counterexample/rationale, or `accepted_risk` with a mitigation and consequence. High/critical risk cannot be accepted.
5. Re-review: obtain a fresh approval. Closing an objection invalidates that author's previous approval. Reappearance of the stable finding reopens it and retains resolution history.

All five current approvals, all tasks done, no open findings, a valid audit trail and current passing required checks are necessary for release. A role quorum cannot overrule an open finding. Separate agents can improve independence but cannot guarantee it; declare the execution mode accurately.

## Scope and iteration discipline

Use one council per integrated deliverable; divide large projects into bounded tasks with stable contracts. Keep mutable implementation ownership explicit. Runtime state persists across sessions. Limit rounds and repair attempts; if the same source and blockers recur, escalate the unresolved design/tooling constraint with evidence. Never spend remaining budget on cosmetic changes while core behavior is incomplete.
