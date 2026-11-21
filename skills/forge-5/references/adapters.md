# Host and command adapters

Preferred integration: let the existing coding agent implement and execute tools, then give fresh role packets to its reviewer/subagent capability. Submit genuine reviews through the CLI. No model API key is required by FORGE-5 itself.

For automated dispatch, provide a trusted executable that accepts one `forge5.review.v1` JSON packet on stdin and writes exactly one review JSON object on stdout. The command runs with the target project as cwd. It must use a real model/agent or human-authored review process to inspect the workspace and produce evidence. It must not modify source. Runtime errors, bad JSON, a wrong role, timeouts and oversized output produce an abstention. All five roles are dispatched, with configurable concurrency from one to five.

```bash
forge5 council --adapter-json '["/absolute/path/to/my-agent-reviewer"]' --execute
```

The optional repair adapter receives `{"protocol":"forge5.repair.v1","status":...}`. It may edit the trusted workspace using its host's tools, run checks, and ask original reviewers to resolve findings through the CLI. It must output `{"summary":"what changed and what was checked"}`. It must preserve tests and acceptance policy. The runtime never interprets model output as Python or shell code.

```bash
forge5 council --adapter-json '["/path/to/reviewer"]' --repair-json '["/path/to/repair-agent"]' --rounds 3 --execute
```

Use `--reviews-only` to dispatch reviewers without configured quality commands. This cannot bypass release gates. Review packets in later rounds contain previous reviews and open findings for evidence-based cross-review. A clean approval does not silently close an old objection. SDK users can implement the `Reviewer` protocol and `Repair` callback in `forge5.council`; see repository tests for deterministic protocol tests. Fake adapters in tests validate orchestration only, not AI engineering ability.
