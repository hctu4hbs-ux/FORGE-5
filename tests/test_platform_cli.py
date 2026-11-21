from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from test_engine import policy

from forge5.cli import main
from forge5.council import packet
from forge5.engine import Council
from forge5.models import Review, Role


class PlatformCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        policy(self.root)
        self.c = Council.initialize(self.root, "CLI platform integration")

    def invoke(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--root", str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_memory_roundtrip_and_markdown_output(self):
        code, raw, _ = self.invoke(
            "memory",
            "add",
            "Atomic leases prevent stale commits",
            "--kind",
            "lesson",
            "--tag",
            "concurrency",
        )
        self.assertEqual(code, 0, raw)
        code, raw, _ = self.invoke("memory", "search", "leases")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(raw)["matches"]), 1)
        target = self.root / ".forge5" / "memory.md"
        code, raw, _ = self.invoke(
            "memory", "search", "leases", "--format", "markdown", "--output", str(target)
        )
        self.assertEqual(code, 0, raw)
        self.assertIn("# FORGE-5 Memory", target.read_text())

    def test_architecture_reports_real_forbidden_edge(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("import b\n")
        (self.root / "src" / "b.py").write_text("VALUE = 1\n")
        config = self.root / ".forge5" / "rules.json"
        config.write_text(json.dumps({"forbidden_imports": [{"from": "a", "to": "b"}]}))
        code, raw, _ = self.invoke("architecture", "--contracts", str(config))
        self.assertEqual(code, 1, raw)
        self.assertFalse(json.loads(raw)["passed"])

    def test_security_produces_real_inventory_and_triage(self):
        (self.root / "safe.py").write_text("VALUE = 1\n")
        code, raw, _ = self.invoke("security")
        self.assertEqual(code, 0, raw)
        result = json.loads(raw)
        self.assertTrue(result["passed"])
        self.assertIn("sbom", result)
        self.assertEqual(result["dependency_audit"]["status"], "not_requested")
        (self.root / "unsafe.py").write_text("eval(input())\n")
        code, raw, _ = self.invoke("security", "--format", "markdown")
        self.assertEqual(code, 1)
        self.assertIn("unsafe-call", raw)

    def test_conflict_discussion_and_no_self_closure(self):
        context = packet(self.c, Role.DEBUGGER)
        self.c.review(
            Review.parse(
                {
                    "role": "debugger",
                    "verdict": "request_changes",
                    "summary": "Inspect a regression",
                    "findings": [
                        {
                            "id": "race",
                            "severity": "high",
                            "title": "Race",
                            "detail": "Serialize access",
                            "evidence": "src/store.py:1",
                        }
                    ],
                }
            ),
            context["source_digest"],
            context["context_digest"],
        )
        self.assertEqual(self.invoke("conflict", "list")[0], 0)
        self.assertEqual(self.invoke("conflict", "show", "debugger.race")[0], 0)
        code, raw, _ = self.invoke(
            "conflict",
            "respond",
            "debugger.race",
            "--actor",
            "implementation",
            "--message",
            "Proposed a guarded transaction",
        )
        self.assertEqual(code, 0, raw)
        self.assertEqual(json.loads(raw)["lifecycle"], "needs_evidence")
        self.assertEqual(
            self.invoke(
                "conflict",
                "fixed",
                "debugger.race",
                "--actor",
                "implementation",
                "--message",
                "Self approved",
            )[0],
            2,
        )
        self.assertEqual(self.invoke("conflict", "show", "absent")[0], 2)
        self.assertEqual(self.invoke("conflict", "respond", "debugger.race")[0], 2)

    def test_execution_is_opt_in_and_performance_checks_are_scoped(self):
        self.assertEqual(self.invoke("benchmark")[0], 2)
        self.assertEqual(self.invoke("benchmark", "--execute")[0], 2)
        self.assertEqual(self.invoke("benchmark", "--execute", "--name", "test")[0], 2)
        self.assertEqual(self.invoke("plan", "--goal", "Goal")[0], 2)
        self.assertEqual(self.invoke("evaluate")[0], 2)
        code, raw, _ = self.invoke("release", "--format", "markdown")
        self.assertEqual(code, 1)
        self.assertIn("# FORGE-5 Release", raw)

    def test_benchmark_executes_budgeted_command(self):
        with (self.root / "forge5.toml").open("a") as handle:
            handle.write(
                '\n[[checks]]\nname="budget"\nkind="performance"\n'
                'argv=["{python}","-c","assert sum(range(100)) == 4950"]\n'
                "required=true\ntimeout=5\nmax_seconds=3\n"
            )
        code, raw, _ = self.invoke("benchmark", "--execute", "--allow-local")
        self.assertEqual(code, 0, raw)
        self.assertTrue(json.loads(raw)["passed"])


if __name__ == "__main__":
    unittest.main()
