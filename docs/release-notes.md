# FORGE-5 0.1.0 — AI Engineering Council

This first alpha delivers a portable engineering skill and a runnable,
zero-dependency Python coordination platform. It adds persistent task contracts,
evidence-backed objections, five-role review, bounded execution and source/plan
freshness to an existing coding-agent host.

The release includes a substantial durable workflow executor example with real
concurrency and recovery tests. See `docs/validation.md` and `docs/evidence/` for
observed results, independent review summaries and implementation limits.

Install the runtime from a trusted checkout with `python -m pip install .`, then
copy the portable skill using `python scripts/install_skill.py --dest YOUR_SKILL_DIR`.
Python 3.11+ is required; Linux/POSIX is the locally validated platform.

Known limits: alpha API stability, trusted local commands, unauthenticated role
labels, partial AST triage, no hosted sandbox, no distributed council service,
and no claim of general AI benchmark superiority or production readiness.
