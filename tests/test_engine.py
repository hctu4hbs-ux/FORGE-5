from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from forge5.config import load_checks
from forge5.engine import Council, context_digest
from forge5.models import ForgeError, Review, Role
from forge5.workspace import fingerprint


def policy(root: Path, *, failing: bool = False) -> None:
    command = [sys.executable, "-c", "raise SystemExit(1)" if failing else "print('checked')"]
    (root / "forge5.toml").write_text(
        "schema = 1\n"
        + "\n".join(
            f'[[checks]]\nname = "{kind}"\nkind = "{kind}"\n'
            f"argv = {json.dumps(command)}\ntimeout = 5\nrequired = true\n"
            for kind in ("test", "security")
        )
    )


def approval(role: Role) -> Review:
    return Review(role, "approve", "Inspected acceptance behavior", ())


class CouncilTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        policy(self.root)
        self.c = Council.initialize(self.root, "Build a durable tool")

    def add_done(self) -> None:
        self.c.add_task("core", "Implement", "principal", [], "Behavior demonstrated")
        self.c.task_status("core", "done", "Tests demonstrate behavior")

    def approvals(self) -> None:
        source = fingerprint(self.root)
        for role in Role:
            self.c.review(approval(role), source, context_digest(self.c.store.read()[1], role))

    def finding(self, severity: str = "high") -> None:
        review = Review.parse(
            {
                "role": "debugger",
                "verdict": "request_changes",
                "summary": "Found a regression",
                "findings": [
                    {
                        "id": "race",
                        "severity": severity,
                        "title": "Race",
                        "detail": "Atomicity violated",
                        "evidence": "test_claim reproduces",
                    }
                ],
            }
        )
        self.c.review(
            review, fingerprint(self.root), context_digest(self.c.store.read()[1], review.role)
        )

    def test_release_requires_all_invariants(self) -> None:
        status = self.c.status()
        self.assertFalse(status["release_ready"])
        self.assertEqual(len(status["blockers"]), 8)
        self.add_done()
        self.approvals()
        self.assertFalse(self.c.status()["release_ready"])
        results = self.c.check()
        self.assertTrue(all(r["passed"] for r in results))
        self.assertTrue(self.c.status()["release_ready"])

    def test_source_edit_invalidates_reviews_and_checks(self) -> None:
        self.add_done()
        self.c.check()
        self.approvals()
        (self.root / "app.py").write_text("new = True")
        status = self.c.status()
        self.assertFalse(status["release_ready"])
        self.assertEqual(len(status["blockers"]), 7)

    def test_source_digest_not_mtime(self) -> None:
        path = self.root / "source.py"
        path.write_text("x=1")
        before = fingerprint(self.root)
        path.write_text("x=2")
        self.assertNotEqual(before, fingerprint(self.root))

    def test_generated_evidence_does_not_change_source(self) -> None:
        before = fingerprint(self.root)
        (self.root / ".forge5" / "report.md").write_text("evidence")
        self.assertEqual(before, fingerprint(self.root))

    def test_policy_edit_invalidates_checks(self) -> None:
        self.c.check()
        with (self.root / "forge5.toml").open("a") as handle:
            handle.write("# policy revision\n")
        self.assertTrue(any("Check test" in b for b in self.c.status()["blockers"]))

    def test_failed_check_blocks_release(self) -> None:
        policy(self.root, failing=True)
        self.add_done()
        self.approvals()
        self.assertTrue(all(not r["passed"] for r in self.c.check()))
        self.assertFalse(self.c.status()["release_ready"])

    def test_dag_prevents_early_start_and_unsafe_reopen(self) -> None:
        self.c.add_task("a", "A", "architect", [], "A passes")
        self.c.add_task("b", "B", "principal", ["a"], "B passes")
        self.assertEqual(self.c.status()["ready_tasks"], ["a"])
        with self.assertRaises(ForgeError):
            self.c.task_status("b", "running")
        self.c.task_status("a", "done", "A evidence")
        self.assertEqual(self.c.status()["ready_tasks"], ["b"])
        self.c.task_status("b", "running")
        with self.assertRaises(ForgeError):
            self.c.task_status("a", "pending")
        self.c.task_status("b", "pending")
        self.c.task_status("a", "pending")

    def test_task_validation_and_acceptance(self) -> None:
        for dependencies in (["absent"], ["a", "a"]):
            with self.assertRaises(ForgeError):
                self.c.add_task("a", "A", "principal", dependencies, "Contract")
        self.c.add_task("a", "A", "principal", [], "Contract")
        with self.assertRaises(ForgeError):
            self.c.add_task("a", "A", "principal", [], "Contract")
        with self.assertRaises(ForgeError):
            self.c.task_status("a", "done")
        with self.assertRaises(ForgeError):
            self.c.task_status("missing", "pending")

    def test_new_task_invalidates_all_reviews(self) -> None:
        self.approvals()
        self.c.add_task("a", "A", "principal", [], "Contract")
        self.assertEqual(self.c.store.read()[1]["reviews"], {})

    def test_decision_invalidates_approvals(self) -> None:
        self.approvals()
        self.c.decide("Persistence", ["JSON", "SQLite"], "SQLite", "Atomic claims", "architect")
        self.assertEqual(self.c.store.read()[1]["reviews"], {})
        with self.assertRaises(ForgeError):
            self.c.decide("Persistence", ["JSON"], "SQLite", "Atomic", "architect")

    def test_finding_is_not_silently_dropped(self) -> None:
        self.finding()
        self.c.review(
            approval(Role.DEBUGGER),
            fingerprint(self.root),
            context_digest(self.c.store.read()[1], Role.DEBUGGER),
        )
        self.assertEqual(self.c.store.read()[1]["findings"]["debugger.race"]["status"], "open")
        self.assertTrue(any("debugger.race" in b for b in self.c.status()["blockers"]))

    def test_only_author_can_resolve(self) -> None:
        self.finding()
        with self.assertRaises(ForgeError):
            self.c.resolve("debugger.race", "product", "rejected", "Counterexample", [])

    def test_high_critical_cannot_be_waived(self) -> None:
        for severity in ("high", "critical"):
            self.finding(severity)
            with self.assertRaises(ForgeError):
                self.c.resolve("debugger.race", "debugger", "accepted_risk", "Known risk", [])

    def test_fixed_needs_current_passing_evidence_and_reapproval(self) -> None:
        self.finding()
        with self.assertRaises(ForgeError):
            self.c.resolve("debugger.race", "debugger", "fixed", "Atomic claim", [])
        with self.assertRaises(ForgeError):
            self.c.resolve("debugger.race", "debugger", "fixed", "Atomic claim", ["test"])
        self.c.check()
        self.c.resolve("debugger.race", "debugger", "fixed", "Atomic claim", ["test"])
        state = self.c.store.read()[1]
        self.assertNotIn("debugger", state["reviews"])
        self.assertEqual(state["findings"]["debugger.race"]["status"], "closed")

    def test_stale_evidence_cannot_close_finding(self) -> None:
        self.finding()
        self.c.check()
        (self.root / "source.py").write_text("pass")
        with self.assertRaises(ForgeError):
            self.c.resolve("debugger.race", "debugger", "fixed", "Patched", ["test"])

    def test_reappearing_finding_reopens_with_history(self) -> None:
        self.finding("low")
        self.c.resolve("debugger.race", "debugger", "accepted_risk", "Bounded impact", [])
        self.finding("high")
        finding = self.c.store.read()[1]["findings"]["debugger.race"]
        self.assertEqual(finding["status"], "open")
        self.assertEqual(len(finding["history"]), 1)

    def test_stale_review_and_optimistic_revision(self) -> None:
        source = fingerprint(self.root)
        revision = self.c.store.read()[0]
        self.c.review(
            approval(Role.ARCHITECT),
            source,
            context_digest(self.c.store.read()[1]),
            expected_revision=revision,
        )
        with self.assertRaises(ForgeError):
            self.c.review(
                approval(Role.PRODUCT),
                source,
                context_digest(self.c.store.read()[1]),
                expected_revision=revision,
            )
        (self.root / "app.py").write_text("pass")
        with self.assertRaises(ForgeError):
            self.c.review(approval(Role.PRODUCT), source, context_digest(self.c.store.read()[1]))

    def test_failed_transaction_rolls_back(self) -> None:
        before = self.c.store.read()
        with self.assertRaises(ForgeError):

            def fail(state):
                state["goal"] = "corruption"
                raise ForgeError("rollback")

            self.c.store.mutate("failure", {}, fail)
        self.assertEqual(before, self.c.store.read())
        self.c.store.verify()

    def test_concurrent_writers_do_not_lose_tasks(self) -> None:
        def add(index: int) -> None:
            Council(self.root).add_task(f"t{index}", "Task", "principal", [], "Contract")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(add, range(32)))
        self.assertEqual(len(self.c.store.read()[1]["tasks"]), 32)
        self.c.store.verify()

    def test_audit_detects_snapshot_and_event_changes(self) -> None:
        self.add_done()
        self.c.store.verify()
        with sqlite3.connect(self.c.store.path) as db:
            db.execute("UPDATE events SET payload = '{}' WHERE sequence = 1")
        with self.assertRaises(ForgeError):
            self.c.store.verify()

    def test_snapshot_tampering_detected(self) -> None:
        with sqlite3.connect(self.c.store.path) as db:
            db.execute("UPDATE snapshot SET body = '{}' WHERE singleton = 1")
        with self.assertRaises(ForgeError):
            self.c.store.verify()

    def test_symlink_source_rejected(self) -> None:
        (self.root / "escape.py").symlink_to(self.root / "forge5.toml")
        with self.assertRaises(ForgeError):
            fingerprint(self.root)

    def test_database_symlink_rejected(self) -> None:
        other = self.root / "other"
        other.mkdir()
        (other / ".forge5").symlink_to(self.root / ".forge5", target_is_directory=True)
        with self.assertRaises(ForgeError):
            Council(other)

    def test_large_file_rejected(self) -> None:
        with (self.root / "large.dat").open("wb") as handle:
            handle.truncate(16 * 1024 * 1024 + 1)
        with self.assertRaises(ForgeError):
            fingerprint(self.root)

    def test_unknown_check_and_invalid_policy(self) -> None:
        with self.assertRaises(ForgeError):
            self.c.check(["missing"])
        (self.root / "forge5.toml").write_text("schema = 2\nchecks=[]")
        with self.assertRaises(ForgeError):
            load_checks(self.root)


if __name__ == "__main__":
    unittest.main()
