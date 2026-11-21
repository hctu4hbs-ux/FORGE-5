from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from durableflow import Job, Store, Worker, Workflow


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state.sqlite")

    def submit(self, *jobs):
        return self.store.submit(Workflow(tuple(jobs)), key="worker")

    def test_real_parallelism_is_bounded_and_dependencies_are_satisfied(self):
        active = peak = 0
        lock = threading.Lock()
        barrier = threading.Barrier(3)
        def slow(payload, deps):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait(timeout=5)
            time.sleep(0.02)
            with lock:
                active -= 1
            return payload["value"]
        jobs = [Job(str(i), "slow", {"value": i}) for i in range(6)]
        jobs.append(Job("total", "sum", depends_on=tuple(str(i) for i in range(6))))
        id = self.submit(*jobs)
        result = Worker(self.store, {"slow": slow, "sum": lambda payload, deps: sum(deps.values())}, concurrency=3).run(id)
        self.assertEqual(3, peak)
        self.assertEqual(7, result["succeeded"])
        self.assertEqual(15, self.store.snapshot(id)["jobs"][-1]["result"])

    def test_heartbeat_prevents_reclaim_of_long_handler(self):
        id = self.submit(Job("long", "slow"))
        started = threading.Event()
        def slow(payload, deps):
            started.set()
            time.sleep(0.35)
            return 12
        worker = Worker(self.store, {"slow": slow}, lease_seconds=0.15, poll_interval=0.015)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker.run, id)
            self.assertTrue(started.wait(3))
            deadline = time.monotonic() + 0.25
            while time.monotonic() < deadline:
                self.assertIsNone(self.store.claim(workflow_id=id))
                time.sleep(0.02)
            self.assertEqual(1, future.result(timeout=5)["succeeded"])
        self.assertEqual(1, self.store.snapshot(id)["jobs"][0]["attempts"])

    def test_retry_exception_then_success(self):
        calls = 0
        def flaky(payload, deps):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError("try again")
            return "ok"
        id = self.submit(Job("a", "flaky", backoff=0.01))
        counts = Worker(self.store, {"flaky": flaky}).run(id)
        self.assertEqual({"succeeded": 1, "failed_attempts": 2, "lease_lost": 0}, counts)
        self.assertEqual("succeeded", self.store.snapshot(id)["state"])

    def test_unknown_handler_and_invalid_json_result_fail_and_block(self):
        id = self.submit(Job("a", "missing", max_attempts=1),
                         Job("b", "bad", max_attempts=1), Job("c", "bad", depends_on=("a",)))
        counts = Worker(self.store, {"bad": lambda p, d: object()}).run(id)
        self.assertEqual(2, counts["failed_attempts"])
        self.assertEqual(["failed", "failed", "blocked"], [j["state"] for j in self.store.snapshot(id)["jobs"]])

    def test_two_workers_drain_same_workflow_without_duplicate_delivery(self):
        seen = []
        lock = threading.Lock()
        def record(payload, deps):
            with lock:
                seen.append(payload["i"])
            time.sleep(0.005)
            return payload["i"]
        id = self.submit(*(Job(str(i), "record", {"i": i}) for i in range(30)))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(Worker(Store(self.store.path), {"record": record}, concurrency=2).run, id) for _ in range(2)]
            counts = [future.result(timeout=15) for future in futures]
        self.assertEqual(list(range(30)), sorted(seen))
        self.assertEqual(30, sum(count["succeeded"] for count in counts))

    def test_empty_queue_and_invalid_configuration(self):
        self.assertEqual(0, Worker(self.store, {}).run()["succeeded"])
        for options in ({"concurrency": 0}, {"concurrency": True}, {"lease_seconds": 0},
                        {"poll_interval": float("nan")}, {"lease_seconds": 10 ** 1000}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                Worker(self.store, {}, **options)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(__file__).resolve().parents[1]
        self.db = Path(self.temp.name) / "cli.sqlite"

    def cli(self, *args, code=0):
        env = dict(os.environ, PYTHONPATH=str(self.root))
        result = subprocess.run([sys.executable, "-m", "durableflow", "--db", str(self.db), *args],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(code, result.returncode, result.stderr + result.stdout)
        return result

    def test_documented_json_workflow_submit_run_status_and_dedup(self):
        path = Path(self.temp.name) / "flow.json"
        path.write_text(json.dumps({"jobs": [{"id": "a", "handler": "add", "payload": {"values": [2, 3]}},
            {"id": "b", "handler": "add", "payload": {"values": [7]}, "depends_on": ["a"]}]}))
        id = json.loads(self.cli("submit", str(path), "--key", "example").stdout)["workflow_id"]
        self.assertEqual(id, json.loads(self.cli("submit", str(path), "--key", "example").stdout)["workflow_id"])
        self.cli("run", "--workflow", id, "--concurrency", "2")
        status = json.loads(self.cli("status", id).stdout)
        self.assertEqual("succeeded", status["state"])
        self.assertEqual(12, status["jobs"][1]["result"])
        self.cli("recover")

    def test_cli_failures_have_exit_status(self):
        self.cli("status", "missing", code=2)
        path = Path(self.temp.name) / "bad.json"
        path.write_text("not JSON")
        self.cli("submit", str(path), "--key", "bad", code=2)
        path.write_text(json.dumps({"jobs": [{"id": "a", "handler": "unregistered", "max_attempts": 1}]}))
        id = json.loads(self.cli("submit", str(path), "--key", "failure").stdout)["workflow_id"]
        self.cli("run", "--workflow", id, code=1)

    def test_database_errors_are_concise_input_failures(self):
        self.db.write_text("invalid database content")
        result = self.cli("recover", code=2)
        self.assertIn("durableflow:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
