# durableflow

A small, useful Python 3.11+ durable DAG executor. SQLite owns workflow state;
workers execute registered local Python handlers. There are no external runtime
dependencies. This is a local example, version 0.1.0, rather than an assessed
production service.

From this directory, run without installing:

```bash
python3 -m durableflow --db demo.sqlite submit example.json --key demo-1
python3 -m durableflow --db demo.sqlite run --concurrency 4
python3 -m durableflow --db demo.sqlite status WORKFLOW_ID
python3 -m unittest discover -s tests -v
python3 bench.py
```

`submit` prints the workflow ID. `example.json` computes 5 and then 12 before
collecting both results. Repeating the same key and canonical definition returns
the original ID; changing its content raises an idempotency conflict. Keys do not
expire. Job and dependency ordering does not affect identity. A new key creates
a separate workflow even if its definition is identical.

Optional installation: `python3 -m pip install .` builds with setuptools and adds
the `durableflow` entry point. A build frontend may need to obtain setuptools;
direct execution needs only the standard library.

## Python API

```python
from durableflow import Job, Store, Worker, Workflow

store = Store("work.sqlite")
workflow_id = store.submit(
    Workflow((Job("source", "add", {"values": [2, 3]}),
              Job("total", "add", {"values": [7]}, depends_on=("source",)))),
    key="invoice-42",
)

def add(payload, dependencies):
    return sum(payload.get("values", [])) + sum(dependencies.values())

counts = Worker(store, {"add": add}, concurrency=2).run(workflow_id)
assert store.snapshot(workflow_id)["state"] == "succeeded"
```

Handlers receive a fresh payload object and a mapping of direct dependency IDs
to their committed JSON results. They return finite JSON values. A handler
exception or invalid result consumes an attempt. Missing handlers fail normally.
The CLI registers only `echo` (returns its payload), `add` (sums numeric
`payload.values` and direct dependency results), and `collect` (returns payload
and dependency results). JSON never imports modules, executes commands, or
deserializes Python objects. Trusted Python callers can register additional
handlers explicitly.

## Durability contract

Submission validates the whole DAG before one transaction inserts it. Claims
use `BEGIN IMMEDIATE`, so multiple threads and processes cannot acquire the
same live lease. Every attempt gets a random token; renewal, success and failure
check both token and unexpired ownership. Expired owners cannot overwrite a
replacement attempt. Job attempts and dependency results survive process exit.

Workers heartbeat while handlers run. After worker death, the next `claim`,
`recover`, or CLI `recover` turns expired jobs into pending retries or exhausted
failures. Reading status does not mutate state. Recovery is demand-driven;
there is no background daemon. Retries wait
`min(86400, backoff * 2 ** (attempt - 1))` seconds after failure or expiration
is observed. Expiration consumes an attempt. After final failure, all pending
transitive descendants become `blocked`; unrelated branches can finish.

Execution is **at least once**. A process can perform an external side effect
and die before persisting its result. Fencing protects database results, not
external systems. Handlers should use the workflow/job identity in their own
idempotency protocol; supply these values in the payload when needed. No
exactly-once external side effect guarantee is possible here.

`Worker.run(id)` waits until that workflow is terminal, including future retries
and jobs held by other workers. Without an ID it drains all currently visible
workflows until no pending/running jobs remain. A later submission needs another
run. Concurrency is bounded per Worker, with at most that many in-flight handlers;
several Worker instances add their capacities. Counts reflect accepted commits
by the current worker, including failed attempts rather than failed workflows.
CLI `run --workflow ID` exits 1 if that workflow fails, 2 for input errors, and
0 on success. An unscoped drain returns 0 when it finishes; inspect workflow
status to assess individual outcomes.

## Operational and security limits

- Use a local filesystem supported by SQLite WAL, shared by the worker processes
  on one host. Network filesystem correctness and multi-host operation are not
  supported. SQLite serializes writers; this implementation favors clarity over
  high-throughput scheduling and opens a connection per operation.
- Use synchronized, stable wall clocks. Leases tolerate normal scheduling delay;
  a long scheduler pause, SQLite lock contention, or clock jump can cause lease
  loss and duplicate handler execution. Backoff has no jitter and scheduling does
  not guarantee fairness. A stale handler keeps occupying its worker slot until
  it returns; its result is discarded.
- Handlers must terminate. Threads have no hard timeout or safe cancellation,
  and CPU-bound Python tasks do not gain multicore throughput in a single worker.
  Stop a stuck process externally and let another process recover its leases.
- Database files contain plaintext payloads, results and error messages. Protect
  filesystem permissions and backups. There is no authentication, encryption,
  tenant isolation, retention, deletion, migration framework or quota management.
  Treat the DB and handler registry as trusted; no sandbox isolates handlers.
- Definitions are capped at 10,000 jobs, 100 attempts per job, and 256 worker
  threads. Payload/result size and nesting are not bounded. Do not expose this
  API directly to untrusted high-volume input. Inputs must use string object
  keys and finite JSON values; cyclic Python objects are unsupported.

The SQLite schema is initial-version only. Future incompatible schema changes
need an explicit migration before reusing existing databases. Report defects
with the smallest workflow, Python/SQLite versions and redacted persisted state.
The included validation report records actual checks and review limitations.

## Change notes

0.1.0: transactional submissions and claims; renewable fenced leases; retry and
dependency handling; bounded workers; built-in CLI handlers; recovery and
concurrency regression tests.
