#!/usr/bin/env python3
"""Measure local metadata, fingerprint and streamed-audit workloads."""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

checkout = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(checkout / "src"))

from forge5.engine import Council  # noqa: E402
from forge5.workspace import fingerprint  # noqa: E402


def benchmark(tasks: int, files: int, events: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="forge5-bench-") as directory:
        root = Path(directory)
        council = Council.initialize(root, "Measure bounded local workloads")
        start = time.perf_counter()
        for index in range(tasks):
            council.add_task(f"t{index}", "Benchmark task", "principal", [], "Observable contract")
        task_seconds = time.perf_counter() - start
        source = root / "src"
        source.mkdir()
        for index in range(files):
            (source / f"m{index}.py").write_text(f"VALUE = {index}\n")
        start = time.perf_counter()
        fingerprint(root)
        fingerprint_seconds = time.perf_counter() - start
        for _ in range(events):
            council.store.mutate("benchmark.output", {"output": "x" * 131072}, lambda state: None)
        tracemalloc.start()
        try:
            start = time.perf_counter()
            council.store.verify()
            verify_seconds = time.perf_counter() - start
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
            "tasks": tasks,
            "task_add_seconds": task_seconds,
            "source_files": files,
            "fingerprint_seconds": fingerprint_seconds,
            "historical_output_events": events,
            "payload_bytes_each": 131072,
            "audit_verify_seconds": verify_seconds,
            "audit_verify_peak_python_bytes": peak,
            "database_bytes": council.store.path.stat().st_size,
            "limits": "One local sample, no model invocation, not a comparative benchmark",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=250)
    parser.add_argument("--files", type=int, default=1000)
    parser.add_argument("--events", type=int, default=400)
    args = parser.parse_args()
    if not all(1 <= value <= 5000 for value in (args.tasks, args.files, args.events)):
        parser.error("workload sizes must be between 1 and 5000")
    print(json.dumps(benchmark(args.tasks, args.files, args.events), indent=2))


if __name__ == "__main__":
    main()
