from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge5.engine import Council
from forge5.isolation import IsolationPolicy, capabilities, execute
from forge5.models import ForgeError
from forge5.runner import ProcessResult
from forge5.tools import ToolBroker


class IsolationTests(unittest.TestCase):
    def test_policy_validation_and_digest(self):
        self.assertNotEqual(IsolationPolicy().digest, IsolationPolicy(memory_mb=256).digest)
        for kwargs in (
            {"mode": "fake"},
            {"engine": "sh"},
            {"image": "python:latest"},
            {"timeout": float("nan")},
            {"memory_mb": True},
            {"allow_local": "yes"},
            {"cpus": 0},
            {"pids_limit": 1},
            {"output_limit": 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ForgeError):
                IsolationPolicy(**kwargs)

    def test_defaults_fail_closed_without_runtime(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("forge5.isolation.shutil.which", return_value=None),
        ):
            root = Path(directory)
            self.assertFalse(any(e["available"] for e in capabilities(root)["engines"]))
            with self.assertRaises(ForgeError):
                execute((sys.executable, "-V"), root, IsolationPolicy())
            with self.assertRaises(ForgeError):
                execute(
                    (sys.executable, "-V"),
                    root,
                    IsolationPolicy(mode="container", allow_local=True),
                )

    def test_explicit_local_runs_and_discloses_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            result = execute(
                (sys.executable, "-c", "print(42)"),
                Path(directory),
                IsolationPolicy(mode="local", allow_local=True),
            )
            self.assertEqual(result.process.exit_code, 0)
            self.assertEqual(result.process.output.strip(), "42")
            for key in ("sandboxed", "network_isolated", "memory_enforced"):
                self.assertFalse(result.isolation[key])
            self.assertTrue(result.isolation["limitations"])
            self.assertIn("process", result.to_dict())

    def test_local_timeout_and_output_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = execute(
                (sys.executable, "-c", "import time; time.sleep(10)"),
                root,
                IsolationPolicy(mode="local", allow_local=True, timeout=0.1),
            )
            self.assertTrue(result.process.timed_out)
            result = execute(
                (sys.executable, "-c", "print('x'*100000)"),
                root,
                IsolationPolicy(mode="local", allow_local=True, output_limit=1024),
            )
            self.assertTrue(result.process.truncated)
            self.assertLessEqual(len(result.process.output), 1024)

    def test_container_fixture_enforces_flags_and_cleanup(self):
        # This tests argv construction, not actual kernel/container isolation.
        calls = []

        def runner(argv, cwd, timeout, **kwargs):
            calls.append(argv)
            return ProcessResult(argv, 0, 0.01, False, "fixture", False)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "forge5.isolation.capabilities",
                return_value={
                    "engines": [{"engine": "docker", "available": True, "executable": "/docker"}]
                },
            ),
            patch("forge5.isolation.run_process", side_effect=runner),
        ):
            policy = IsolationPolicy(mode="container", image="python@sha256:" + "a" * 64)
            result = execute((sys.executable, "-V"), Path(directory), policy)
            command = calls[1]
            for flag in (
                "--network=none",
                "--pull=never",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--pids-limit",
                "--memory",
                "--cpus",
            ):
                self.assertIn(flag, command)
            self.assertTrue(any("readonly" in a for a in command))
            self.assertNotIn(command[command.index("--user") + 1], ("0:0", "0"))
            self.assertEqual(command[-2:], ("python3", "-V"))
            self.assertEqual(calls[-1][1:3], ("rm", "--force"))
            self.assertTrue(result.isolation["network_isolated"])

    def test_unavailable_image_never_pulls(self):
        calls = []

        def runner(argv, cwd, timeout, **kwargs):
            calls.append(argv)
            return ProcessResult(argv, 1, 0.01, False, "not installed", False)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "forge5.isolation.capabilities",
                return_value={
                    "engines": [{"engine": "podman", "available": True, "executable": "/podman"}]
                },
            ),
            patch("forge5.isolation.run_process", side_effect=runner),
        ):
            with self.assertRaises(ForgeError):
                execute(
                    ("python3", "-V"),
                    Path(directory),
                    IsolationPolicy(mode="container", image="python@sha256:" + "a" * 64),
                )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1:3], ("image", "inspect"))

    def test_daemon_presence_is_not_capability(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("forge5.isolation.shutil.which", return_value="/docker"),
            patch(
                "forge5.isolation.run_process",
                return_value=ProcessResult(
                    ("/docker",), 1, 0.01, False, "daemon unavailable", False
                ),
            ),
        ):
            self.assertFalse(
                any(item["available"] for item in capabilities(Path(directory))["engines"])
            )


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.c = Council.initialize(self.root, "Tool validation")
        command = json.dumps([sys.executable, "-c", "print('verified')"])
        (self.root / "forge5.toml").write_text(
            "schema=1\n"
            + "".join(
                f'[[checks]]\nname="{kind}"\nkind="{kind}"\nargv={command}\ntimeout=2\nrequired=true\n'
                for kind in ("test", "security")
            )
        )
        (self.root / "app.py").write_text("VALUE = 1\n")
        self.local = IsolationPolicy(mode="local", allow_local=True)

    def broker(self, actor="implementation", paths=("app.py", "tests/test_new.py")):
        return ToolBroker(self.c, actor, allowed_paths=paths, isolation=self.local)

    def test_legacy_check_cannot_claim_registered_isolation(self):
        self.c.store.mutate(
            "execution.policy",
            {},
            lambda state: state.update(execution_policy_digest="container-policy"),
        )
        with patch("forge5.engine.run_process") as runner:
            with self.assertRaisesRegex(ForgeError, "execution policy"):
                self.c.check()
            runner.assert_not_called()
        self.assertFalse(self.c.store.read()[1]["evidence"])

    def test_provider_configuration_cannot_be_replaced_through_alias(self):
        (self.root / "forge5-agents.json").write_text('{"original":true}')
        change = self.change("forge5-agents.json", '{"replacement":true}')
        change["path"] = "./forge5-agents.json"
        with self.assertRaisesRegex(ForgeError, "protected"):
            self.broker(paths=("forge5-agents.json",)).call("apply_changes", changes=[change])
        self.assertEqual((self.root / "forge5-agents.json").read_text(), '{"original":true}')

    def test_overlapping_changes_are_rejected_before_writing(self):
        changes = [self.change("new", "parent"), self.change("new/child.py", "child")]
        with self.assertRaisesRegex(ForgeError, "descendant"):
            self.broker(paths=("new", "new/child.py")).call("apply_changes", changes=changes)
        self.assertFalse((self.root / "new").exists())

    def test_change_preimage_uses_exact_crlf_bytes(self):
        (self.root / "app.py").write_bytes(b"VALUE = 1\r\n")
        self.broker().call("apply_changes", changes=[self.change(content="VALUE = 2\r\n")])
        self.assertEqual((self.root / "app.py").read_bytes(), b"VALUE = 2\r\n")

    def change(self, path="app.py", content="VALUE = 2\n"):
        old = (self.root / path).read_bytes() if (self.root / path).exists() else None
        return {
            "path": path,
            "before_sha256": hashlib.sha256(old).hexdigest() if old else None,
            "content": content,
        }

    def test_read_tree_search_and_audit(self):
        b = self.broker("architect")
        self.assertEqual(b.call("read_file", path="app.py")["content"], "VALUE = 1\n")
        self.assertIn("app.py", b.call("tree"))
        self.assertEqual(b.call("search", query="VALUE")["matches"][0]["line"], 1)
        self.assertEqual(b.call("tasks"), {})
        self.assertEqual(b.call("findings"), {})
        events = self.c.store.events()
        self.assertEqual(len([e for e in events if e["action"] == "tool.operation"]), 10)
        self.c.store.verify()

    def test_denied_role_and_path_audited(self):
        b = self.broker("architect")
        for tool, args in (
            ("apply_changes", {"changes": [self.change()]}),
            ("run_check", {"name": "test"}),
            ("read_file", {"path": "../outside"}),
            ("read_file", {"path": "/etc/passwd"}),
            ("read_file", {"path": ".forge5/council.sqlite3"}),
        ):
            with self.subTest(tool=tool, args=args), self.assertRaises(ForgeError):
                b.call(tool, **args)
        self.assertIn("denied_or_failed", self.c.store.events()[-1]["payload"])
        self.assertEqual((self.root / "app.py").read_text(), "VALUE = 1\n")

    def test_symlinks_special_files_and_big_inputs(self):
        b = self.broker()
        (self.root / "escape.py").symlink_to(self.root / "app.py")
        with self.assertRaises(ForgeError):
            b.call("read_file", path="escape.py")
        (self.root / "escape.py").unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.root / "pipe.py")
            with self.assertRaises(ForgeError):
                b.call("read_file", path="pipe.py")
        (self.root / "large.py").write_bytes(b"x" * 1048577)
        with self.assertRaises(ForgeError):
            b.call("read_file", path="large.py")

    def test_scoped_patch_new_tests_and_protected_policy(self):
        b = self.broker()
        changes = [self.change(), self.change("tests/test_new.py", "import unittest\n")]
        preview = b.call("inspect_changes", changes=changes)
        self.assertIn("-VALUE = 1", preview["patch"])
        self.assertEqual((self.root / "app.py").read_text(), "VALUE = 1\n")
        result = b.call("apply_changes", changes=changes)
        self.assertTrue(result["applied"])
        self.assertEqual((self.root / "app.py").read_text(), "VALUE = 2\n")
        with self.assertRaises(ForgeError):
            b.call("apply_changes", changes=[self.change("tests/test_new.py", "")])
        with self.assertRaises(ForgeError):
            self.broker(paths=("forge5.toml",)).call(
                "apply_changes", changes=[self.change("forge5.toml", "")]
            )

    def test_stale_or_out_of_scope_batch_is_all_rejected(self):
        b = self.broker()
        for changes in (
            [self.change(), self.change("extra.py", "oops")],
            [self.change(), {**self.change("tests/test_new.py", "new"), "before_sha256": "bad"}],
            [self.change(), self.change()],
        ):
            with self.assertRaises(ForgeError):
                b.call("apply_changes", changes=changes)
            self.assertEqual((self.root / "app.py").read_text(), "VALUE = 1\n")
        self.assertFalse((self.root / "extra.py").exists())

    def test_named_checks_permissions_and_policy_freshness(self):
        b = self.broker("debugger")
        evidence = b.call("run_check", name="test")
        self.assertTrue(evidence["passed"])
        self.assertFalse(evidence["isolation"]["sandboxed"])
        with self.assertRaises(ForgeError):
            b.call("run_check", name="security")
        revision, state = self.c.store.read()
        self.assertEqual(state["execution_policy_digest"], self.local.digest)
        changed = ToolBroker(
            self.c,
            "security",
            isolation=IsolationPolicy(mode="local", allow_local=True, memory_mb=256),
        )
        changed.call("run_check", name="security")
        self.assertNotIn("test", self.c.store.read()[1]["evidence"])
        self.assertGreater(self.c.store.read()[0], revision)

    def test_implementer_cannot_close_objection(self):
        with self.assertRaisesRegex(ForgeError, "cannot close"):
            self.broker().call("conflict", id="debugger.bug", action="fixed", message="I fixed it")

    def test_explicit_execution_opt_in_required(self):
        b = ToolBroker(self.c, "debugger", isolation=IsolationPolicy(mode="local"))
        with self.assertRaises(ForgeError):
            b.call("run_check", name="test")
        self.assertNotIn("test", self.c.store.read()[1]["evidence"])
