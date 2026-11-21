from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_engine import approval, policy

from forge5.cli import main
from forge5.config import Check
from forge5.council import CommandReviewer, command_repair, improve, packet, review_round
from forge5.engine import Council
from forge5.models import Finding, ForgeError, Review, Role
from forge5.report import markdown
from forge5.runner import run_process
from forge5.security import scan


class ModelTest(unittest.TestCase):
    def test_rejects_unknown_keys_invalid_values_and_conflicting_approval(self) -> None:
        valid = {"role": "architect", "verdict": "approve", "summary": "Inspected", "findings": []}
        self.assertEqual(Review.parse(valid).role, Role.ARCHITECT)
        for field, value in (
            ("role", "invented"),
            ("verdict", "yes"),
            ("summary", ""),
            ("findings", {}),
            ("unknown", True),
        ):
            with self.subTest(field=field), self.assertRaises(ForgeError):
                Review.parse({**valid, field: value})
        finding = {
            "id": "bug",
            "severity": "high",
            "title": "Bug",
            "detail": "Incorrect",
            "evidence": "source.py:1",
        }
        with self.assertRaises(ForgeError):
            Review.parse({**valid, "findings": [finding]})
        with self.assertRaises(ForgeError):
            Review.parse({**valid, "verdict": "request_changes", "findings": [finding, finding]})
        with self.assertRaises(ForgeError):
            Finding.parse({**finding, "severity": "cosmetic"})

    def test_check_strict_numbers_and_fields(self) -> None:
        valid = {
            "name": "tests",
            "argv": ["{python}", "-V"],
            "kind": "test",
            "timeout": 2,
            "required": True,
        }
        self.assertEqual(Check.parse(valid).argv[0], sys.executable)
        for field, value in (
            ("timeout", float("nan")),
            ("timeout", True),
            ("timeout", -1),
            ("required", "yes"),
            ("argv", []),
            ("kind", "magic"),
            ("max_seconds", float("inf")),
        ):
            with self.subTest(field=field), self.assertRaises(ForgeError):
                Check.parse({**valid, field: value})


class RunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_real_exit_and_stdin(self) -> None:
        result = run_process(
            (sys.executable, "-c", "import sys; print(sys.stdin.read())"),
            self.root,
            3,
            stdin="payload",
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output.strip(), "payload")

    def test_nonzero_and_missing_command(self) -> None:
        self.assertEqual(
            run_process((sys.executable, "-c", "raise SystemExit(7)"), self.root, 3).exit_code, 7
        )
        self.assertIsNone(run_process(("/missing/forge-command",), self.root, 3).exit_code)

    def test_timeout(self) -> None:
        result = run_process((sys.executable, "-c", "import time; time.sleep(10)"), self.root, 0.1)
        self.assertTrue(result.timed_out)
        self.assertLess(result.seconds, 2)

    def test_output_limit(self) -> None:
        result = run_process(
            (sys.executable, "-c", "print('x'*1000000)"), self.root, 3, output_limit=4096
        )
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.output), 4096)

    def test_shell_metacharacters_are_literal(self) -> None:
        result = run_process(
            (sys.executable, "-c", "import sys; print(sys.argv[1])", "$(touch pwned)"), self.root, 3
        )
        self.assertIn("$(touch pwned)", result.output)
        self.assertFalse((self.root / "pwned").exists())


class OrchestrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        policy(self.root)
        self.c = Council.initialize(self.root, "Integrated behavior")
        self.c.add_task("core", "Core", "principal", [], "Contract passes")
        self.c.task_status("core", "done", "Verified")

    def test_parallel_round_and_packet(self) -> None:
        result = review_round(self.c, lambda role, _: approval(role))
        self.assertEqual([r["role"] for r in result], [r.value for r in Role])
        context = packet(self.c, Role.SECURITY)
        self.assertEqual(len(context["previous_reviews"]), 5)
        self.assertIn("source_digest", context)

    def test_reviewer_failure_replaces_old_approval_with_abstention(self) -> None:
        review_round(self.c, lambda role, _: approval(role))

        def reviewer(role, context):
            if role == Role.SECURITY:
                raise RuntimeError("provider unavailable")
            return approval(role)

        result = review_round(self.c, reviewer)
        self.assertEqual(result[3]["verdict"], "abstain")
        self.assertIn("unavailable", result[3]["error"])

    def test_source_modification_during_review_discarded(self) -> None:
        def reviewer(role, context):
            (self.root / f"{role}.txt").write_text("modified")
            return approval(role)

        with self.assertRaises(ForgeError):
            review_round(self.c, reviewer)
        self.assertEqual(self.c.store.read()[1]["reviews"], {})

    def test_metadata_modification_rejects_stale_round(self) -> None:
        def reviewer(role, context):
            if role == Role.PRODUCT:
                self.c.decide("Choice", ["A", "B"], "A", "Evidence", "architect")
            return approval(role)

        with self.assertRaises(ForgeError):
            review_round(self.c, reviewer)

    def test_external_json_adapter(self) -> None:
        argv = (
            sys.executable,
            "-c",
            "import sys,json; p=json.load(sys.stdin); "
            "print(json.dumps(dict(role=p['role'],verdict='abstain',summary='Protocol fixture',"
            "findings=[])))",
        )
        reviewer = CommandReviewer(self.c, argv, 3)
        self.assertEqual(
            reviewer(Role.ARCHITECT, packet(self.c, Role.ARCHITECT)).verdict, "abstain"
        )

    def test_bad_adapter_responses_and_wrong_role(self) -> None:
        for output in (
            "garbage",
            json.dumps(
                {"role": "product", "verdict": "approve", "summary": "fixture", "findings": []}
            ),
        ):
            reviewer = CommandReviewer(self.c, (sys.executable, "-c", f"print({output!r})"), 3)
            with self.assertRaises(ForgeError):
                reviewer(Role.ARCHITECT, packet(self.c, Role.ARCHITECT))

    def test_measured_improvement_cycle_repairs_real_failing_check(self) -> None:
        # Test oracle adapters validate orchestration, not AI-generated code quality.
        (self.root / "app.py").write_text("value = 0\n")
        (self.root / "forge5.toml").write_text(
            "schema=1\n"
            + "\n".join(
                f'[[checks]]\nname="{kind}"\nkind="{kind}"\n'
                f"argv={json.dumps([sys.executable, '-c', 'import app; assert app.value == 42'])}\n"
                "timeout=5\nrequired=true\n"
                for kind in ("test", "security")
            )
        )

        def repair(council, status):
            self.assertTrue(any("Check" in b for b in status["blockers"]))
            (self.root / "app.py").write_text("value = 42\n")

        result = improve(self.c, lambda role, _: approval(role), repair, max_rounds=3)
        self.assertEqual(result["outcome"], "ready")
        self.assertEqual(len(result["rounds"]), 2)
        self.assertFalse(result["rounds"][0]["checks"][0]["passed"])
        self.assertTrue(result["rounds"][1]["checks"][0]["passed"])

    def test_stagnation_stops_early(self) -> None:
        policy(self.root, failing=True)
        calls = []
        result = improve(
            self.c, lambda role, _: approval(role), lambda c, s: calls.append(True), max_rounds=10
        )
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(len(result["rounds"]), 2)
        self.assertEqual(len(calls), 1)

    def test_bounds_and_no_repair(self) -> None:
        with self.assertRaises(ForgeError):
            improve(self.c, lambda role, _: approval(role), max_rounds=0)
        with self.assertRaises(ForgeError):
            review_round(self.c, lambda role, _: approval(role), workers=6)
        result = improve(self.c, lambda role, _: Review(role, "abstain", "No evidence", ()))
        self.assertEqual(result["outcome"], "blocked")

    def test_command_repair_protocol(self) -> None:
        command_repair((sys.executable, "-c", 'print(\'{"summary":"No changes"}\')'))(
            self.c, self.c.status()
        )
        with self.assertRaises(ForgeError):
            command_repair((sys.executable, "-c", "print('{}')"))(self.c, self.c.status())

    def test_self_mutating_check_cannot_pass(self) -> None:
        source = (self.root / "forge5.toml").read_text()
        mutation = json.dumps(
            [sys.executable, "-c", "from pathlib import Path; Path('new.py').write_text('x=1')"]
        )
        source = source.replace(json.dumps([sys.executable, "-c", "print('checked')"]), mutation, 1)
        (self.root / "forge5.toml").write_text(source)
        result = self.c.check(["test"])[0]
        self.assertFalse(result["passed"])
        self.assertTrue(result["source_changed"])

    def test_report_reflects_actual_gate(self) -> None:
        report = markdown(self.c.status())
        self.assertIn("Release ready: **False**", report)
        self.assertIn("not prove reviewer independence", report)


class SecurityTest(unittest.TestCase):
    def test_aliases_and_dangerous_apis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.py").write_text(
                "import pickle as p\nimport subprocess as sp\n"
                "from os import system as run\nimport yaml\nimport requests\n"
                "p.loads(data)\nsp.run(command, shell=True)\nrun(command)\n"
                "yaml.load(data)\nrequests.get(url, verify=False)\n"
            )
            findings = scan(root)
            self.assertEqual(len(findings), 5)
            self.assertEqual(
                {f["rule"] for f in findings},
                {"unsafe-call", "shell-true", "yaml-loader", "tls-verification"},
            )

    def test_safe_calls_syntax_errors_and_missing_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "safe.py").write_text("import yaml\nyaml.load(data, Loader=yaml.SafeLoader)")
            self.assertEqual(scan(root), [])
            (root / "syntax.py").write_text("def broken(")
            self.assertEqual(scan(root)[0]["rule"], "syntax")
            with self.assertRaises(ForgeError):
                scan(root / "absent")


class CliTest(unittest.TestCase):
    def invoke(self, argv: list[str]) -> tuple[int, str, str]:
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(argv)
        return code, output.getvalue(), error.getvalue()

    def test_end_to_end_cli_and_execution_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy(root)
            prefix = ["--root", directory]
            self.assertEqual(self.invoke(prefix + ["init", "--goal", "CLI journey"])[0], 0)
            self.assertEqual(self.invoke(prefix + ["release"])[0], 1)
            self.assertEqual(self.invoke(prefix + ["check"])[0], 2)
            self.assertEqual(self.invoke(prefix + ["check", "--execute"])[0], 0)
            self.assertEqual(
                self.invoke(
                    prefix + ["task", "add", "core", "--title", "Core", "--acceptance", "Works"]
                )[0],
                0,
            )
            self.assertEqual(
                self.invoke(prefix + ["task", "set", "core", "done", "--evidence", "Tests"])[0], 0
            )
            code, output, _ = self.invoke(prefix + ["review", "packet", "architect"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["role"], "architect")
            for role in Role:
                context = packet(Council(root), role)
                review_path = root / ".forge5" / f"{role}.json"
                review_path.write_text(
                    json.dumps(
                        {
                            "role": role.value,
                            "verdict": "approve",
                            "summary": "CLI protocol fixture only",
                            "findings": [],
                        }
                    )
                )
                self.assertEqual(
                    self.invoke(
                        prefix
                        + [
                            "review",
                            "submit",
                            str(review_path),
                            "--digest",
                            context["source_digest"],
                            "--context",
                            context["context_digest"],
                        ]
                    )[0],
                    0,
                )
            self.assertEqual(self.invoke(prefix + ["release"])[0], 0)
            self.assertEqual(self.invoke(prefix + ["audit"])[0], 0)
            report = root / ".forge5" / "report.md"
            self.assertEqual(self.invoke(prefix + ["report", "--output", str(report)])[0], 0)
            self.assertTrue(report.is_file())
            self.assertEqual(self.invoke(prefix + ["scan"])[0], 0)


if __name__ == "__main__":
    unittest.main()
