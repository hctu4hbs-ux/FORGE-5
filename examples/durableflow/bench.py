"""Repeatable small-workflow workload; emits measured wall time, not a claim."""
import json
from pathlib import Path
import tempfile
import time

from durableflow import Job, Store, Worker, Workflow


def main():
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "bench.sqlite")
        id = store.submit(Workflow(tuple(Job(str(i), "echo", {"i": i}) for i in range(200))), key="bench")
        counts = Worker(store, {"echo": lambda p, d: p}, concurrency=4).run(id)
        assert counts["succeeded"] == 200
        assert store.snapshot(id)["state"] == "succeeded"
    elapsed = time.perf_counter() - started
    print(json.dumps({"jobs": 200, "concurrency": 4, "seconds": elapsed, "budget_seconds": 10}))
    if elapsed > 10:
        raise SystemExit("workload exceeded ten-second budget")


if __name__ == "__main__":
    main()
