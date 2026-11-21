# Host adapters and Python SDK

The host is responsible for implementation tools, model access, permissions and
review independence. FORGE-5 never converts generated prose into executable code.

## Native host integration

Export each role's packet using `forge5 review packet ROLE`. Give that packet and
the actual source to the host's reviewer capability. Save the returned review JSON
under `.forge5/`, then import it using both packet digests:

```bash
forge5 review submit .forge5/review.json \
  --digest SOURCE_DIGEST_FROM_PACKET --context CONTEXT_DIGEST_FROM_PACKET --mode host
```

`--revision` adds strict optimistic revision matching for a single submission.
Parallel host reviews may share the source digest without requiring identical
mutable council revisions. Each submission must use **its own role packet's**
context digest, which also binds that author's finding/disposition history.
Source, plan and authored-objection changes reject stale approvals. Without
separate agents, use `--mode sequential` and disclose self-review.

Review contract (unknown fields are rejected):

```json
{
  "role": "security",
  "verdict": "request_changes",
  "summary": "The parser can allocate unbounded memory from one input.",
  "findings": [{
    "id": "payload-limit",
    "severity": "high",
    "title": "Unbounded payload",
    "detail": "Reject oversized input before decoding or buffering it.",
    "evidence": "src/parser.py:42; tests/test_limits.py reproduces the allocation"
  }]
}
```

Roles: `architect`, `principal`, `debugger`, `security`, `product`. Verdicts:
`approve`, `request_changes`, `abstain`. Severities: `critical`, `high`, `medium`,
`low`. A high/critical finding cannot accompany an approval. At most 100 findings
are accepted per review. Finding IDs must be stable and unique within that role's
response. Every finding needs concrete evidence text; the host/reviewers must
assess its truth, which JSON validation cannot prove.

## Command reviewer protocol

One process handles one review. FORGE-5 invokes the same trusted argv up to five
times concurrently, with the target project as cwd. No shell is used. Stdin
contains one JSON packet with `protocol = "forge5.review.v1"`, role, root, source
and context digests, revision, instructions, goal, tasks, decisions, open findings,
previous reviews and check metadata. Raw check output remains in local state and
can be inspected separately.

Stdout must be exactly one review JSON object. The generic runner merges stderr
into captured output, so progress logs also invalidate a generic adapter's response.
Use a separate file if you need logging. Wrong roles, invalid JSON, nonzero exits,
timeouts and oversized responses result in an abstention, with an error in the
round's result. Output capture defaults to 256 KiB for reviewers.

```bash
forge5 council --adapter-json '["/path/to/real-reviewer", "--read-only"]' \
  --workers 5 --timeout 300 --execute
```

The adapter must inspect actual source through its host's tools. An executable
that blindly returns approvals only tests wiring and must never be presented as
engineering review. If tooling cannot inspect enough context, return `abstain`.

## Optional Codex CLI bridge

Authenticate your Codex CLI through its normal workflow first. The bridge uses
the CLI's `exec`, `--sandbox read-only`, `--ephemeral`, `--output-schema` and
`--output-last-message` options. It validates the final response and does not read
or handle credentials. It writes temporary protocol files outside project inputs.
The CLI may use the model/provider and billing configured in that installation.

```bash
forge5 council --adapter-json \
  '["python3", "/absolute/path/to/FORGE-5/adapters/codex_reviewer.py", "--timeout", "300"]' \
  --timeout 330 --execute
```

The outer timeout should exceed the bridge's inner timeout. The local validation
used an executable Codex protocol fixture, not a live model call: the available
CLI was unauthenticated. Native host subagents were used for actual independent
example reviews. No hosted-provider integration benchmark is claimed.

## Repair protocol

An optional trusted repair command receives
`{"protocol":"forge5.repair.v1","status":...}` on stdin and returns
`{"summary":"what changed and what was checked"}` on stdout. It may edit source,
run checks and coordinate finding dispositions through the CLI. It must preserve
acceptance contracts and regression tests. It must not impersonate an objecting
reviewer to close a finding. The runtime cannot authenticate role identity.

```bash
forge5 council --adapter-json '["/path/to/reviewer"]' \
  --repair-json '["/path/to/repair-agent"]' --rounds 3 --execute
```

Each round reruns configured checks and obtains new reviews. Source and unchanged
blocker repetition stops the loop. `--rounds` accepts 1–20; default is one at the
CLI. No repair command means the runtime reports blockers for the host to address.
An interrupted round fails closed. Retry it, or cancel using
`forge5 round-cancel --reason "provider connection failed"`; old approvals stay revoked.

## Python SDK

```python
from pathlib import Path
from forge5.engine import Council
from forge5.council import CommandReviewer, improve

council = Council(Path("/path/to/project"))
reviewer = CommandReviewer(council, ("/path/to/reviewer",), timeout=300)
result = improve(council, reviewer, max_rounds=2, workers=5)
print(result["outcome"], result["status"]["blockers"])
```

Implement `Reviewer.__call__(role, context) -> Review` and optional
`Repair(council, status) -> None` for an in-process host. In-process callbacks must
supply their own deadlines/cancellation; the runtime cannot safely kill Python
threads. Command adapters receive subprocess deadlines. All source edits should
finish before checks/review; use isolated worktrees for parallel implementation.
