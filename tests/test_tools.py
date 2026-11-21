from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallerTest(unittest.TestCase):
    def test_install_and_overwrite_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = [
                sys.executable,
                str(ROOT / "scripts" / "install_skill.py"),
                "--dest",
                directory,
            ]
            first = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            skill = Path(directory) / "forge-5"
            self.assertTrue((skill / "SKILL.md").is_file())
            self.assertTrue((skill / "references" / "runtime.md").is_file())
            repeated = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(repeated.returncode, 2)
            self.assertIn("Refusing to overwrite", repeated.stderr)

    def test_checkout_skill_helper_runs(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "skills" / "forge-5" / "scripts" / "forge.py"),
                "--version",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0.1.0", result.stdout)

    def test_uninstalled_checkout_helper_runs_its_default_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv.EnvBuilder(with_pip=False).create(root / "environment")
            binary = (
                root / "environment" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )
            project = root / "project"
            project.mkdir()
            (project / "tests").mkdir()
            (project / "tests" / "test_smoke.py").write_text(
                "import unittest\nclass Smoke(unittest.TestCase):\n"
                " def test_value(self): self.assertEqual(2+2,4)\n"
            )
            env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
            command = [
                str(binary),
                str(ROOT / "skills" / "forge-5" / "scripts" / "forge.py"),
                "--root",
                str(project),
            ]
            init = subprocess.run(
                command + ["init", "--goal", "Uninstalled checkout journey"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            checks = subprocess.run(
                command + ["check", "--execute"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(checks.returncode, 0, checks.stderr + checks.stdout)
            self.assertTrue(all(item["passed"] for item in json.loads(checks.stdout)))


class CodexBridgeTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "Executable fixture needs POSIX")
    def test_real_subprocess_protocol_with_fixture_codex(self) -> None:
        # This validates CLI integration; the fixture is not an LLM review.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "fixture-codex"
            binary.write_text(
                f"#!{sys.executable}\n" + "import sys,json\nfrom pathlib import Path\n"
                "assert sys.argv[1:4] == ['exec','--sandbox','read-only']\n"
                "assert not any('dangerously' in a for a in sys.argv)\n"
                "p=json.loads(sys.stdin.read().split('PACKET_JSON\\n',1)[1])\n"
                "schema=json.loads(Path(sys.argv[sys.argv.index('--output-schema')+1]).read_text())\n"
                "assert schema['properties']['role']['enum'] == [p['role']]\n"
                "out=Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
                "out.write_text(json.dumps(dict(role=p['role'],verdict='abstain',"
                "summary='Protocol fixture only',findings=[])))\n"
                "print('progress logs do not corrupt final JSON')\n"
            )
            binary.chmod(0o755)
            payload = {"protocol": "forge5.review.v1", "role": "architect", "root": str(root)}
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "adapters" / "codex_reviewer.py"),
                    "--binary",
                    str(binary),
                    "--timeout",
                    "3",
                ],
                cwd=root,
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["verdict"], "abstain")

    def test_bad_packet_fails_closed(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "adapters" / "codex_reviewer.py")],
            input='{"protocol":"wrong"}',
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Expected forge5.review.v1", result.stderr)

    def test_schema_is_role_specific(self) -> None:
        path = ROOT / "adapters" / "codex_reviewer.py"
        spec = importlib.util.spec_from_file_location("bridge_under_test", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from forge5.models import Role

        self.assertEqual(module.schema(Role.SECURITY)["properties"]["role"]["enum"], ["security"])
