from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_engine import approval, policy

from forge5.council import packet
from forge5.engine import Council, context_digest
from forge5.isolation import IsolationPolicy
from forge5.models import Finding, ForgeError, Review, Role
from forge5.tools import ToolBroker
from forge5.workspace import fingerprint


class RichConflictTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        policy(self.root)
        self.c = Council.initialize(self.root, "Engineering discussion")

    def open(self, severity: str = "medium", required: str | None = "test") -> None:
        review = Review.parse(
            {
                "role": "security",
                "verdict": "request_changes",
                "summary": "Inspect malformed inputs",
                "findings": [
                    {
                        "id": "parse",
                        "severity": severity,
                        "title": "Parser loses input",
                        "detail": "Malformed input crashes",
                        "claim": "The parser violates its documented rejection contract",
                        "evidence": "tests/test_parser.py reproduces",
                        "locations": [{"path": "src/parser.py", "start_line": 12, "end_line": 14}],
                        "required_test": required,
                    }
                ],
            }
        )
        self.c.review(
            review, fingerprint(self.root), context_digest(self.c.store.read()[1], Role.SECURITY)
        )

    def finding(self):
        return self.c.store.read()[1]["findings"]["security.parse"]

    def test_structured_discussion_and_reviewed_fix(self) -> None:
        self.open()
        finding = self.finding()
        self.assertEqual(finding["claim"], "The parser violates its documented rejection contract")
        self.assertEqual(finding["locations"][0]["start_line"], 12)
        self.c.conflict("security.parse", "implementation", "disputed", "Need boundary case")
        self.assertEqual(self.finding()["lifecycle"], "disputed")
        self.c.conflict("security.parse", "security", "needs_evidence", "Add regression")
        self.c.conflict("security.parse", "implementation", "respond", "Input guard added")
        self.assertEqual(self.finding()["implementation_response"]["message"], "Input guard added")
        self.c.check()
        self.c.conflict(
            "security.parse", "security", "fixed", "Reviewed passing regression", ["test"]
        )
        self.assertEqual(self.finding()["status"], "closed")
        self.assertEqual(self.finding()["lifecycle"], "fixed")
        self.assertEqual(len(self.finding()["discussion"]), 4)
        self.c.store.verify()

    def test_implementation_cannot_dispose_even_with_independent_flag(self) -> None:
        self.open()
        self.c.check()
        for action in ("fixed", "rejected", "accepted_risk"):
            with self.subTest(action=action), self.assertRaises(ForgeError):
                self.c.conflict(
                    "security.parse",
                    "implementation",
                    action,
                    "Self approval",
                    ["test"],
                    independent=True,
                )
        self.assertEqual(self.finding()["status"], "open")

    def test_independent_role_can_close_but_needs_explicit_independence(self) -> None:
        self.open()
        self.c.check()
        with self.assertRaises(ForgeError):
            self.c.conflict("security.parse", "debugger", "fixed", "Verified", ["test"])
        self.c.conflict(
            "security.parse", "debugger", "fixed", "Verified", ["test"], independent=True
        )
        self.assertTrue(self.finding()["resolution"]["independent"])
        self.assertNotIn("security", self.c.store.read()[1]["reviews"])

    def test_required_regression_and_current_evidence(self) -> None:
        self.open()
        self.c.check()
        with self.assertRaisesRegex(ForgeError, "requested regression"):
            self.c.conflict(
                "security.parse", "security", "fixed", "Checked unrelated gate", ["security"]
            )
        (self.root / "parser.py").write_text("pass")
        with self.assertRaisesRegex(ForgeError, "stale evidence"):
            self.c.conflict("security.parse", "security", "fixed", "Checked old gate", ["test"])

    def test_high_risk_requires_counterevidence_and_cannot_be_waived(self) -> None:
        self.open("high")
        for action in ("accepted_risk", "rejected"):
            with self.assertRaises(ForgeError):
                self.c.conflict("security.parse", "security", action, "Dismiss unsupported claim")
        self.c.check()
        self.c.conflict(
            "security.parse", "security", "rejected", "Passing counterexample", ["test"]
        )
        self.assertEqual(self.finding()["lifecycle"], "rejected")

    def test_reopen_keeps_history_and_invalidates_author_packet(self) -> None:
        self.open()
        self.c.conflict("security.parse", "security", "accepted_risk", "Bounded documented impact")
        old = packet(self.c, Role.SECURITY)
        self.c.review(approval(Role.SECURITY), old["source_digest"], old["context_digest"])
        self.c.conflict("security.parse", "security", "reopen", "New contradictory evidence")
        self.assertEqual(self.finding()["lifecycle"], "reopened")
        self.assertEqual(len(self.finding()["history"]), 1)
        with self.assertRaises(ForgeError):
            self.c.review(approval(Role.SECURITY), old["source_digest"], old["context_digest"])
        self.assertTrue(any("security.parse" in b for b in self.c.status()["blockers"]))

    def test_execution_policy_invalidates_reviews_and_evidence(self) -> None:
        self.c.check()
        old = packet(self.c, Role.SECURITY)
        self.c.store.mutate(
            "policy", {}, lambda s: s.update(execution_policy_digest="container-v1")
        )
        with self.assertRaises(ForgeError):
            self.c.review(approval(Role.SECURITY), old["source_digest"], old["context_digest"])
        self.assertTrue(any("Check test" in b for b in self.c.status()["blockers"]))
        with self.assertRaises(ForgeError):
            self.c.check()
        broker = ToolBroker(
            self.c, "implementation", isolation=IsolationPolicy(mode="local", allow_local=True)
        )
        broker.call("run_check", name="test")
        self.assertFalse(any("Check test" in b for b in self.c.status()["blockers"]))

    def test_locations_reject_escape_and_invalid_ranges(self) -> None:
        base = {"id": "x", "severity": "low", "title": "X", "detail": "D", "evidence": "E"}
        for location in (
            {"path": "../secret", "start_line": 1},
            {"path": "/secret", "start_line": 1},
            {"path": "source.py", "start_line": True},
            {"path": "source.py", "start_line": 8, "end_line": 2},
        ):
            with self.subTest(location=location), self.assertRaises(ForgeError):
                Finding.parse({**base, "locations": [location]})

    def test_closed_discussion_requires_reopen_and_respond_requires_implementer(self) -> None:
        self.open()
        with self.assertRaises(ForgeError):
            self.c.conflict("security.parse", "architect", "respond", "Pretend implementer")
        self.c.conflict("security.parse", "security", "accepted_risk", "Documented impact")
        with self.assertRaises(ForgeError):
            self.c.conflict("security.parse", "implementation", "disputed", "Late discussion")
        with self.assertRaises(ForgeError):
            self.c.conflict("security.parse", "implementation", "reopen", "Not independent")
