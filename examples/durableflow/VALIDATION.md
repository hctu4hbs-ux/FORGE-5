# Observed validation

Observed validation on 2026-11-21 (Asia/Riyadh), Python 3.12.14 / SQLite 3.53.1,
Linux x86_64. Python 3.11+ is the intended compatibility range; other Python
versions and operating systems were not exercised.

- `python3 -m unittest discover -s tests -v`: 26 tests passed in 3.281 seconds
  in the recorded gate before this documentation refresh (initial 24-test slice
  passed in 3.456 seconds).
- `python3 bench.py`: 200 independent jobs, concurrency 4, 1.906648 seconds in
  that same recorded gate;
  configured budget 10 seconds on this environment only.
- Tests exercised four separate claim processes, 12 claim threads, two concurrent
  workers, 24 concurrent idempotent submissions, a process exiting after claim
  (`os._exit(17)`) and subsequent database reopen/recovery, expired token fencing,
  heartbeat extension, exponential retry deadlines, transitive dependency
  blocking, bounded actual concurrency and CLI subprocesses.

FORGE-5 `check --execute` passed required tests, security and workload gates.
Security AST triage returned zero findings. The current checks, their precise
timings and review votes are recorded under `.forge5/`; this report records a
specific observed run rather than a rolling latest timing. Report edits intentionally
require a fresh gate before final reviews. Reviews use host mode with separate
read-only role agents, not five independent human auditors. The implementation
agent retains integration ownership.
Security triage is incomplete AST scanning, accompanied by source review and
hostile-string/JSON tests. It is not a penetration test or dependency audit.
There are no third-party runtime dependencies.

No deployment or publication was performed. See README for at-least-once side
effects, clock/lease assumptions, trusted handlers, single-host SQLite and
resource/timeout limits. No production-readiness claim is made.
