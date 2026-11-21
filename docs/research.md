# Research and positioning

Reviewed on 2026-11-21. This is a qualitative reading of primary project material,
not a comparative benchmark. No star counts or performance superiority claims
are used. FORGE-5's implementation is original; no competitor code is vendored.

| Project | Relevant approach in its own documentation | FORGE-5 design response |
| --- | --- | --- |
| [Superpowers](https://github.com/obra/superpowers) | Composable development skills, planning, implementation and staged review | Keep a portable skill; make task/review/evidence invariants executable and persistent |
| [MetaGPT](https://github.com/FoundationAgents/MetaGPT) | Software-company role specialization and structured engineering workflow | Use five engineering perspectives while avoiding a mandatory model-provider framework |
| [ChatDev](https://github.com/OpenBMB/ChatDev) | Multi-agent collaboration and configurable development workflows | Keep conflicts as explicit durable findings/dispositions and expose a small host-neutral protocol |
| [Agent Skills specification](https://agentskills.io/specification) | Portable skill directories with YAML-frontmatter `SKILL.md` and optional resources | Ship one concise `forge-5` skill with references and an executable helper |

The concrete differentiator is the combination of an objection lifecycle, atomic
fail-closed council rounds, source/plan freshness and executable release gates.
These are features, not proof that five roles outperform one agent. Role-playing
in one context is explicitly labeled sequential self-review; fresh host agents
can provide stronger independence, with no guarantee of unbiased judgments.

The release demonstrates functionality on a durable workflow engine and tests
runtime invariants. To establish broader effectiveness, future evaluations need
multiple unseen tasks, pinned source versions, budgets, baseline agent runs,
independent acceptance tests, repeated trials and disclosed failures. Protocol
fixtures or one successful example are insufficient for general claims.

CI action references were checked against the official
[checkout](https://github.com/actions/checkout/releases),
[setup-python](https://github.com/actions/setup-python/releases) and
[upload-artifact](https://github.com/actions/upload-artifact/releases) projects.
Local CLI bridge flags were inspected through the installed `codex exec --help`.
