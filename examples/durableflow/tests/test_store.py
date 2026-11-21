from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest

from durableflow import IdempotencyConflict, Job, Store, Workflow


def claim_and_die(path, workflow_id):
    claim = Store(path).claim(workflow_id=workflow_id, lease_seconds=0.1)
    os._exit(17 if claim else 18)


def process_claim(path, workflow_id, results):
    store = Store(path)
    claimed = []
    while claim := store.claim(workflow_id=workflow_id):
        claimed.append(claim.job_id)
        store.succeed(claim, claim.job_id)
    results.put(claimed)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite"
        self.now = 1000.0
        self.store = Store(self.path, clock=lambda: self.now)

    def submit(self, *jobs):
        return self.store.submit(Workflow(tuple(jobs)), key="test")

    def test_idempotency_is_canonical_and_conflicts_are_atomic(self):
        original = Workflow((Job("b", "echo", depends_on=("a",)), Job("a", "echo", {"b": 2, "a": 1})))
        id = self.store.submit(original, key="once")
        reordered = Workflow((Job("a", "echo", {"a": 1, "b": 2}), Job("b", "echo", depends_on=("a",))))
        self.assertEqual(id, self.store.submit(reordered, key="once"))
        with self.assertRaises(IdempotencyConflict):
            self.store.submit(Workflow((Job("other", "echo"),)), key="once")
        self.assertEqual(2, len(self.store.snapshot(id)["jobs"]))

    def test_concurrent_submission_returns_one_id(self):
        workflow = Workflow((Job("a", "echo"),))
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _: Store(self.path).submit(workflow, key="same"), range(24)))
        self.assertEqual(1, len(set(ids)))

    def test_atomic_claims_across_threads(self):
        id = self.submit(*(Job(str(i), "echo") for i in range(40)))
        def claim(_):
            result = Store(self.path, clock=lambda: self.now).claim(workflow_id=id)
            return result.job_id if result else None
        with ThreadPoolExecutor(max_workers=12) as pool:
            ids = list(pool.map(claim, range(55)))
        self.assertEqual(40, len([value for value in ids if value is not None]))
        self.assertEqual(40, len(set(ids) - {None}))

    def test_atomic_claims_across_processes(self):
        id = Store(self.path).submit(Workflow(tuple(Job(str(i), "echo") for i in range(40))), key="process")
        ctx = multiprocessing.get_context("spawn")
        results = ctx.Queue()
        processes = [ctx.Process(target=process_claim, args=(str(self.path), id, results)) for _ in range(4)]
        for process in processes:
            process.start()
        all_ids = [value for _ in processes for value in results.get(timeout=15)]
        for process in processes:
            process.join(15)
            self.assertEqual(0, process.exitcode)
        self.assertEqual(40, len(all_ids))
        self.assertEqual(40, len(set(all_ids)))
        self.assertEqual("succeeded", self.store.snapshot(id)["state"])
        results.close()

    def test_retry_backoff_and_dependency_results_survive_reopen(self):
        id = self.submit(Job("a", "echo", max_attempts=3, backoff=2), Job("b", "echo", depends_on=("a",)))
        first = self.store.claim()
        self.assertEqual("a", first.job_id)
        self.assertTrue(self.store.fail(first, "first error"))
        self.assertIsNone(self.store.claim())
        self.now += 2
        second = Store(self.path, clock=lambda: self.now).claim()
        self.assertEqual(2, second.attempt)
        self.store.fail(second, "second error")
        self.now += 3.99
        self.assertIsNone(self.store.claim())
        self.now += 0.01
        third = self.store.claim()
        self.assertEqual(3, third.attempt)
        self.store.succeed(third, {"answer": 42})
        child = self.store.claim()
        self.assertEqual({"a": {"answer": 42}}, child.dependencies)
        self.store.succeed(child, None)
        self.assertEqual("succeeded", self.store.snapshot(id)["state"])

    def test_expired_tokens_cannot_renew_fail_or_complete(self):
        id = self.submit(Job("a", "echo", backoff=0.5))
        stale = self.store.claim(lease_seconds=2)
        self.now += 2
        self.assertFalse(self.store.renew(stale))
        self.assertFalse(self.store.succeed(stale, "late"))
        self.assertFalse(self.store.fail(stale, "late"))
        self.assertIsNone(self.store.claim())  # recovery schedules backoff
        self.now += 0.5
        live = self.store.claim()
        self.assertNotEqual(stale.token, live.token)
        self.assertFalse(self.store.succeed(stale, "overwrites live"))
        self.assertTrue(self.store.succeed(live, "correct"))
        self.assertFalse(self.store.succeed(live, "duplicate"))
        self.assertEqual("correct", self.store.snapshot(id)["jobs"][0]["result"])

    def test_renewal_extends_ownership(self):
        self.submit(Job("a", "echo"))
        claim = self.store.claim(lease_seconds=2)
        self.now += 1
        self.assertTrue(self.store.renew(claim, lease_seconds=3))
        self.now += 2
        self.assertIsNone(self.store.claim())
        self.assertTrue(self.store.succeed(claim, 9))

    def test_failure_blocks_transitive_dependents_but_not_independent_work(self):
        id = self.submit(Job("a", "echo", max_attempts=1), Job("b", "echo", depends_on=("a",)),
                         Job("c", "echo", depends_on=("b",)), Job("z", "echo"))
        self.store.fail(self.store.claim(), "fatal")
        self.assertEqual("z", self.store.claim().job_id)
        states = {job["id"]: job["state"] for job in self.store.snapshot(id)["jobs"]}
        self.assertEqual({"a": "failed", "b": "blocked", "c": "blocked", "z": "running"}, states)

    def test_final_expiration_exhausts_attempt_and_blocks_descendant(self):
        id = self.submit(Job("a", "echo", max_attempts=1), Job("b", "echo", depends_on=("a",)))
        self.store.claim(lease_seconds=1)
        self.now += 1
        self.store.recover()
        self.assertEqual(["failed", "blocked"], [job["state"] for job in self.store.snapshot(id)["jobs"]])
        self.assertEqual(0, self.store.unfinished(id))

    def test_actual_worker_death_and_restart_recovery(self):
        store = Store(self.path)
        id = store.submit(Workflow((Job("a", "echo", backoff=0.01),)), key="death")
        ctx = multiprocessing.get_context("spawn")
        process = ctx.Process(target=claim_and_die, args=(str(self.path), id))
        process.start()
        process.join(10)
        self.assertEqual(17, process.exitcode)
        self.assertEqual("running", store.snapshot(id)["jobs"][0]["state"])
        reopened = Store(self.path)
        deadline = time.monotonic() + 3
        recovered = None
        while recovered is None and time.monotonic() < deadline:
            recovered = reopened.claim(workflow_id=id)
            time.sleep(0.02)
        self.assertIsNotNone(recovered)
        self.assertEqual(2, recovered.attempt)
        self.assertTrue(reopened.succeed(recovered, "restarted"))
        self.assertEqual("succeeded", Store(self.path).snapshot(id)["state"])

    def test_invalid_result_leaves_claim_available_for_failure(self):
        self.submit(Job("a", "echo"))
        claim = self.store.claim()
        with self.assertRaises(ValueError):
            self.store.succeed(claim, float("nan"))
        self.assertTrue(self.store.fail(claim, "invalid result"))

    def test_parameterized_sql_preserves_hostile_strings(self):
        name = "'; DROP TABLE jobs; --"
        id = self.store.submit(Workflow((Job(name, "echo", {"value": name}),)), key=name)
        claim = self.store.claim()
        self.assertEqual(name, claim.payload["value"])
        self.store.succeed(claim, name)
        self.assertEqual(name, self.store.snapshot(id)["jobs"][0]["result"])

    def test_claim_scope_and_missing_workflow(self):
        a = self.store.submit(Workflow((Job("a", "echo"),)), key="a")
        b = self.store.submit(Workflow((Job("b", "echo"),)), key="b")
        self.assertEqual(b, self.store.claim(workflow_id=b).workflow_id)
        self.assertEqual(a, self.store.claim(workflow_id=a).workflow_id)
        with self.assertRaises(KeyError):
            self.store.unfinished("missing")

    def test_ephemeral_databases_are_rejected(self):
        for path in ("", ":memory:"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                Store(path)


class ModelTests(unittest.TestCase):
    def test_invalid_dags_and_json_rejected_before_database_changes(self):
        cases = [(), (Job("a", "echo"), Job("a", "echo")),
                 (Job("a", "echo", depends_on=("missing",)),),
                 (Job("a", "echo", depends_on=("a",)),),
                 (Job("a", "echo", depends_on=("b",)), Job("b", "echo", depends_on=("a",))),
                 (Job("a", "echo", {"x": float("nan")}),),
                 (Job("a", "echo", {1: "bad"}),),
                 (Job("a", "echo", max_attempts=True),),
                 (Job("a", "echo", backoff=float("inf")),),
                 (Job("a", "echo", depends_on=("b", "b")), Job("b", "echo"))]
        for jobs in cases:
            with self.subTest(jobs=jobs), self.assertRaises(ValueError):
                Workflow(jobs).document()

    def test_deep_dag_avoids_recursion_limit(self):
        jobs = tuple(Job(str(i), "echo", depends_on=(str(i - 1),) if i else ()) for i in range(1500))
        self.assertEqual(1500, len(Workflow(jobs).document()["jobs"]))

    def test_document_parser_rejects_malformed_and_unknown_fields(self):
        for value in ({}, {"jobs": {}}, {"jobs": [{"id": "a", "handler": "echo", "executable": "x"}]},
                      {"jobs": [None]}, {"jobs": [], "other": True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Workflow.from_dict(value)


if __name__ == "__main__":
    unittest.main()
