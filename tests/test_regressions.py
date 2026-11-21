from __future__ import annotations

import contextlib
import io
import os
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch

from test_engine import approval, policy

from forge5.council import packet, review_round
from forge5.engine import Council, context_digest
from forge5.models import ForgeError, Review, Role
from forge5.security import scan
from forge5.testing import run_tests
from forge5.workspace import fingerprint


class ReviewRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        policy(self.root)
        self.c = Council.initialize(self.root, "Verified release")
        self.c.add_task("a", "A", "principal", [], "A passes")
        self.c.task_status("a", "done", "Tests")
        self.c.check()
        review_round(self.c, lambda role, _: approval(role))
        self.assertTrue(self.c.status()["release_ready"])

    def test_packet_cannot_reapprove_changed_plan(self) -> None:
        original = packet(self.c, Role.ARCHITECT)
        self.c.decide("Storage", ["SQLite", "JSON"], "SQLite", "Atomicity", "architect")
        with self.assertRaisesRegex(ForgeError, "goals, tasks or decisions"):
            self.c.review(
                approval(Role.ARCHITECT), original["source_digest"], original["context_digest"]
            )
        self.assertFalse(self.c.status()["release_ready"])

    def test_pre_objection_packet_cannot_renew_approval_after_disposition(self) -> None:
        old = packet(self.c, Role.ARCHITECT)
        objection = Review.parse(
            {
                "role": "architect",
                "verdict": "request_changes",
                "summary": "Review the bounded local tradeoff",
                "findings": [
                    {
                        "id": "tradeoff",
                        "severity": "medium",
                        "title": "Bounded risk",
                        "detail": "Document its consequence",
                        "evidence": "Isolated acceptance scenario",
                    }
                ],
            }
        )
        self.c.review(objection, old["source_digest"], old["context_digest"])
        self.c.resolve(
            "architect.tradeoff", "architect", "accepted_risk", "Mitigation recorded", []
        )
        with self.assertRaisesRegex(ForgeError, "authored finding"):
            self.c.review(approval(Role.ARCHITECT), old["source_digest"], old["context_digest"])
        fresh = packet(self.c, Role.ARCHITECT)
        self.assertNotEqual(old["context_digest"], fresh["context_digest"])
        # Atomic rounds support distinct per-role finding contexts too.
        review_round(self.c, lambda role, _: approval(role))
        self.assertTrue(self.c.status()["release_ready"])

    def test_metadata_race_does_not_preserve_any_old_approval(self) -> None:
        original_commit = self.c.complete_round

        def interleave(*args, **kwargs):
            # Simulate a concurrent writer immediately before final result commit.
            self.c.store.mutate("concurrent.check", {}, lambda state: None)
            return original_commit(*args, **kwargs)

        def reviewer(role, context):
            if role == Role.SECURITY:
                raise RuntimeError("offline")
            return approval(role)

        with patch.object(self.c, "complete_round", interleave), self.assertRaises(ForgeError):
            review_round(self.c, reviewer)
        status = self.c.status()
        self.assertFalse(status["release_ready"])
        self.assertEqual(status["state"]["reviews"], {})
        self.assertTrue(status["state"]["active_round"])
        self.c.cancel_round("Retry after concurrent writer stopped")
        self.assertFalse(self.c.status()["release_ready"])
        with self.assertRaises(ForgeError):
            self.c.cancel_round("No active round")

    def test_round_crash_revokes_existing_approvals(self) -> None:
        self.c.begin_round()
        self.assertFalse(self.c.status()["release_ready"])
        source = fingerprint(self.root)
        with self.assertRaisesRegex(ForgeError, "round is active"):
            self.c.review(approval(Role.PRODUCT), source, context_digest(self.c.store.read()[1]))
        # A fresh round recovers an interrupted dispatch.
        review_round(self.c, lambda role, _: approval(role))
        self.assertTrue(self.c.status()["release_ready"])

    def test_all_five_results_share_one_commit(self) -> None:
        before = self.c.store.read()[0]
        review_round(self.c, lambda role, _: approval(role))
        events = self.c.store.events()
        self.assertEqual(self.c.store.read()[0] - before, 2)
        self.assertEqual([e["action"] for e in events[-2:]], ["round.begin", "round.complete"])

    @unittest.skipUnless(os.name == "posix", "POSIX executable mode")
    def test_chmod_invalidates_current_release(self) -> None:
        script = self.root / "check.sh"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
        self.c.check()
        review_round(self.c, lambda role, _: approval(role))
        self.assertTrue(self.c.status()["release_ready"])
        before = fingerprint(self.root)
        script.chmod(0o644)
        self.assertNotEqual(before, fingerprint(self.root))
        self.assertFalse(self.c.status()["release_ready"])


class ResourceRegressionTest(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO")
    def test_security_scanner_rejects_fifo_without_opening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.mkfifo(root / "blocked.py")
            with self.assertRaisesRegex(ForgeError, "regular source files"):
                scan(root)

    def test_verification_streams_historical_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy(root)
            council = Council.initialize(root, "History")
            for _ in range(64):
                council.store.mutate("verbose.check", {"output": "x" * 131072}, lambda state: None)
            tracemalloc.start()
            try:
                council.store.verify()
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            # Eagerly loading the 8 MiB event history exceeds this bound.
            self.assertLess(peak, 3 * 1024 * 1024)

    def test_empty_and_all_skipped_discovery_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            root = Path(directory)
            self.assertFalse(run_tests(root)["passed"])
            (root / "test_empty.py").write_text(
                "import unittest\n@unittest.skip('unavailable')\n"
                "class Tests(unittest.TestCase):\n def test_a(self): pass\n"
            )
            result = run_tests(root)
            self.assertEqual(result["discovered"], 1)
            self.assertEqual(result["executed"], 0)
            self.assertFalse(result["passed"])

    def test_fixture_skip_does_not_cancel_real_executed_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            root = Path(directory)
            (root / "test_mixed_fixture_skip.py").write_text(
                "import unittest\nclass Skipped(unittest.TestCase):\n"
                " @classmethod\n def setUpClass(cls): raise unittest.SkipTest('unavailable')\n"
                " def test_skipped(self): pass\n"
                "class Runs(unittest.TestCase):\n def test_real(self): self.assertEqual(2+2,4)\n"
            )
            result = run_tests(root)
            self.assertEqual(result["discovered"], 2)
            self.assertEqual(result["executed"], 1)
            self.assertTrue(result["passed"])
