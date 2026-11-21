from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge5.evaluation import _apply_baseline, _prepare, _tests, case_suite, evaluate, markdown
from forge5.models import ForgeError


class EvaluationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_evaluation(self, **kwargs):
        return evaluate(self.root, execute=True, allow_unisolated=True, **kwargs)

    def test_five_real_defects_and_paired_full_orchestration(self):
        report = self.run_evaluation(run_id="paired")
        self.assertTrue(report["passed"], json.dumps(report, indent=2))
        self.assertEqual(len(report["results"]), 5)
        self.assertFalse(report["independent_ai_agents"])
        for result in report["results"]:
            for arm in ("baseline", "forge5"):
                row = result[arm]
                self.assertFalse(row["before"]["acceptance"]["passed"])
                self.assertTrue(row["before"]["regression"]["passed"])
                self.assertTrue(row["after"]["acceptance"]["passed"])
                self.assertTrue(row["after"]["regression"]["passed"])
                self.assertEqual(row["regressions"], 0)
                self.assertIsNone(row["cost"])
                self.assertGreater(row["seconds"], 0)
                self.assertGreater(row["tests"], 1)
            self.assertEqual(result["forge5"]["execution"]["result"]["outcome"], "ready")
            self.assertGreater(
                result["forge5"]["provider_calls"], result["baseline"]["provider_calls"]
            )
        self.assertIn("identical repair oracle", report["interpretation"])
        destination = Path(report["path"])
        self.assertEqual(json.loads((destination / "report.json").read_text())["passed"], True)
        self.assertIn("| security | forge5 | True", markdown(report))
        with patch("forge5.evaluation._forge", side_effect=AssertionError("must reuse checkpoint")):
            resumed = self.run_evaluation(run_id="paired", resume=True)
        self.assertEqual(json.loads(json.dumps(report["results"])), resumed["results"])
        with self.assertRaisesRegex(ForgeError, "contract changed"):
            self.run_evaluation(run_id="paired", resume=True, max_rounds=2)
        (destination / "logic/baseline/pricing.py").write_text("# changed\n")
        with self.assertRaisesRegex(ForgeError, "workspace changed"):
            self.run_evaluation(run_id="paired", resume=True)

    def test_execution_is_opt_in_and_inputs_checked(self):
        with self.assertRaisesRegex(ForgeError, "execute=True"):
            evaluate(self.root)
        with self.assertRaisesRegex(ForgeError, "allow_unisolated"):
            evaluate(self.root, execute=True)
        for options in [
            {"mode": "unknown"},
            {"max_rounds": 0},
            {"timeout": 0},
            {"cases_selected": ["unknown"]},
            {"cases_selected": ["logic", "logic"]},
            {"mode": "providers"},
        ]:
            with self.subTest(options=options), self.assertRaises(ForgeError):
                self.run_evaluation(**options)

    def test_blocked_council_is_not_reported_as_success(self):
        def blocked(root, case, *_args):
            for name, content in case.repaired.items():
                (root / name).write_text(content)
            return {"rounds": 1, "result": {"outcome": "blocked"}}

        with patch("forge5.evaluation._forge", side_effect=blocked):
            report = self.run_evaluation(cases_selected=["logic"], run_id="blocked")
        row = report["results"][0]["forge5"]
        self.assertTrue(row["after"]["acceptance"]["passed"])
        self.assertFalse(report["passed"])
        self.assertIn("release conditions", row["error"])

    def test_provider_failure_is_recorded_and_retryable(self):
        with patch("forge5.evaluation._forge", side_effect=RuntimeError("provider unavailable")):
            report = self.run_evaluation(cases_selected=["logic"], run_id="retry")
        self.assertFalse(report["passed"])
        self.assertIn("provider unavailable", report["results"][0]["forge5"]["error"])
        report = self.run_evaluation(cases_selected=["logic"], run_id="retry", resume=True)
        self.assertTrue(report["passed"])

    def test_baseline_changes_are_atomic_scoped_and_content_addressed(self):
        target = self.root / "module.py"
        target.write_text("original\n")
        change = {
            "path": "module.py",
            "before_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "content": "fixed\n",
        }
        for response in [
            None,
            {"changes": [{}]},
            {"changes": [dict(change, path="../escape")]},
            {"changes": [dict(change, before_sha256="stale")]},
            {"changes": [dict(change, content=1)]},
            {"changes": [change, change]},
        ]:
            with self.subTest(response=response), self.assertRaises(ForgeError):
                _apply_baseline(self.root, response, {"module.py"})
            self.assertEqual(target.read_text(), "original\n")
        _apply_baseline(self.root, {"changes": [change]}, {"module.py"})
        self.assertEqual(target.read_text(), "fixed\n")

    def test_test_runner_distinguishes_failures_errors_and_zero_tests(self):
        workspace = self.root / "fixture"
        _prepare(workspace, case_suite()["migration"])
        before = _tests(workspace, "test_acceptance.py", 5)
        self.assertFalse(before["passed"])
        self.assertEqual(before["errors"], 1)
        empty = _tests(workspace, "missing.py", 5)
        self.assertFalse(empty["passed"])
        self.assertEqual(empty["tests"], 0)

    def test_existing_run_and_symlink_state_rejected(self):
        state = self.root / ".forge5"
        external = self.root / "external"
        external.mkdir()
        state.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ForgeError, "symlinks"):
            self.run_evaluation(cases_selected=["logic"])
        state.unlink()
        self.run_evaluation(cases_selected=["logic"], run_id="existing")
        with self.assertRaisesRegex(ForgeError, "already exists"):
            self.run_evaluation(cases_selected=["logic"], run_id="existing")
