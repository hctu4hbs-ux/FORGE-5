from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge5.architecture import check_architecture, validate_contracts
from forge5.engine import Council, context_digest
from forge5.memory import get_contracts, record_memory, search_memory, set_contracts
from forge5.models import ForgeError, Review, Role
from forge5.quality import dependency_audit, license_inventory, quality_report, sbom, secret_scan
from forge5.runner import ProcessResult


class ArchitectureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.package = self.root / "src" / "app"
        self.package.mkdir(parents=True)
        (self.package / "__init__.py").write_text("")

    def source(self, name: str, content: str) -> None:
        (self.package / name).write_text(content)

    def test_relative_imports_and_allowed_direction(self) -> None:
        self.source("api.py", "from .domain import Item\n")
        self.source("domain.py", "class Item: pass\n")
        result = check_architecture(self.root, {"layers": [["app.api"], ["app.domain"]]})
        self.assertTrue(result["passed"], result)
        self.assertEqual(["app.domain"], result["graph"]["app.api"])
        self.assertEqual(1, result["edges"][0]["line"])

    def test_package_reexports_do_not_form_implicit_self_cycles(self) -> None:
        self.source("__init__.py", "from . import child\n")
        self.source("child.py", "VALUE = 1\n")
        result = check_architecture(self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual(["app.child"], result["graph"]["app"])
        self.source("__init__.py", "import app\n")
        self.assertFalse(check_architecture(self.root)["passed"])

    def test_forbidden_edge_and_layer_violation(self) -> None:
        self.source("api.py", "VALUE = 1\n")
        self.source("store.py", "from app.api import VALUE\n")
        result = check_architecture(
            self.root,
            {
                "forbidden_imports": [{"from": "app.store", "to": "app.api"}],
                "layers": [["app.api"], ["app.store"]],
            },
        )
        self.assertFalse(result["passed"])
        self.assertEqual(
            {"forbidden-import", "layer-direction"}, {v["kind"] for v in result["violations"]}
        )

    def test_cycle_is_detected_and_external_dependencies_ignored(self) -> None:
        self.source("a.py", "import app.b\nimport json\n")
        self.source("b.py", "import app.a\n")
        result = check_architecture(self.root)
        self.assertFalse(result["passed"])
        self.assertEqual(["app.a", "app.b"], result["violations"][0]["modules"])
        self.assertNotIn("json", result["graph"])

    def test_syntax_failure_does_not_silently_pass(self) -> None:
        self.source("bad.py", "def invalid(")
        result = check_architecture(self.root)
        self.assertFalse(result["passed"])
        self.assertEqual("syntax", result["violations"][0]["kind"])

    def test_relative_escape_rejected(self) -> None:
        self.source("api.py", "from ...outside import thing\n")
        self.assertEqual("relative-import", check_architecture(self.root)["violations"][0]["kind"])

    def test_contract_file_and_strict_validation(self) -> None:
        (self.root / ".forge5").mkdir()
        contract_file = self.root / ".forge5" / "architecture.json"
        contract_file.write_text(json.dumps({"layers": [["app"]]}))
        self.assertTrue(check_architecture(self.root)["passed"])
        contract_file.write_text("invalid")
        with self.assertRaises(ForgeError):
            check_architecture(self.root)
        for policy in (
            {"unknown": True},
            {"layers": "bad"},
            {"layers": [[]]},
            {"forbidden_imports": [{"from": "app"}]},
        ):
            with self.subTest(policy=policy), self.assertRaises(ForgeError):
                validate_contracts(policy)

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_source_symlink_rejected(self) -> None:
        (self.package / "outside.py").symlink_to(self.root / "outside")
        with self.assertRaises(ForgeError):
            check_architecture(self.root)

    def test_deep_graph_avoids_recursion_limit(self) -> None:
        from forge5.architecture import _cycles

        graph = {str(i): {str(i + 1)} if i < 1499 else set() for i in range(1500)}
        self.assertEqual([], _cycles(graph))
        graph["1499"].add("0")
        self.assertEqual(1500, len(_cycles(graph)[0]))


class MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.council = Council.initialize(self.root, "Remember engineering constraints")

    def test_memory_survives_reopen_and_search_is_audited(self) -> None:
        identifier = record_memory(
            self.council,
            "lesson",
            "SQLite writer contention needs bounded retries",
            ["storage", "concurrency"],
        )
        result = search_memory(Council(self.root), "sqlite retries")
        self.assertEqual(identifier, result[0]["id"])
        self.assertEqual([], search_memory(self.council, "network"))
        self.assertEqual("memory.search", self.council.store.events()[-1]["action"])
        self.council.store.verify()

    def test_contract_change_invalidates_packet_and_approval(self) -> None:
        before = context_digest(self.council.store.read()[1], Role.ARCHITECT)
        from forge5.workspace import fingerprint

        source = fingerprint(self.root)
        self.council.review(
            Review(Role.ARCHITECT, "approve", "Initial contract", ()), source, before
        )
        set_contracts(self.council, {"forbidden_imports": [{"from": "app.api", "to": "app.db"}]})
        self.assertNotEqual(before, context_digest(self.council.store.read()[1], Role.ARCHITECT))
        self.assertEqual({}, self.council.store.read()[1]["reviews"])
        self.assertEqual(
            "app.api", get_contracts(Council(self.root))["forbidden_imports"][0]["from"]
        )
        with self.assertRaises(ForgeError):
            self.council.review(Review(Role.ARCHITECT, "approve", "Stale", ()), source, before)

    def test_decisions_are_searchable_and_filters_work(self) -> None:
        self.council.decide(
            "Database", ["SQLite", "JSON"], "SQLite", "Transactional state", "architect"
        )
        record_memory(self.council, "constraint", "Never store plaintext passwords")
        self.assertEqual(1, len(search_memory(self.council, "SQLite", "decision")))
        self.assertEqual(1, len(search_memory(self.council, "passwords", "constraint")))
        self.assertEqual([], search_memory(self.council, "SQLite", "lesson"))
        with self.assertRaises(ForgeError):
            record_memory(self.council, "invented", "Content")


class QualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_secret_value_never_appears_in_report(self) -> None:
        credential = "AKIA" + "X" * 16
        (self.root / "settings.py").write_text("credential = '" + credential + "'\n")
        result = secret_scan(self.root)
        self.assertFalse(result["passed"])
        self.assertEqual("aws-access-key", result["findings"][0]["rule"])
        self.assertNotIn(credential, json.dumps(result))
        self.assertEqual(1, result["findings"][0]["line"])

    def test_secret_patterns_and_redacted_capture(self) -> None:
        token = "ghp_" + "a" * 32
        private = "-----BEGIN " + "PRIVATE KEY-----"
        (self.root / ".env").write_text(
            token + "\n" + private + "\n" + "pass" + "word = '" + "q" * 16 + "'\n"
        )
        result = secret_scan(self.root)
        self.assertEqual(
            {"github-token", "private-key", "credential-assignment"},
            {f["rule"] for f in result["findings"]},
        )
        self.assertNotIn(token, json.dumps(result))

    def test_skipped_file_is_explicitly_incomplete(self) -> None:
        (self.root / "binary.dat").write_bytes(b"\0payload")
        result = secret_scan(self.root)
        self.assertFalse(result["passed"])
        self.assertFalse(result["complete"])
        self.assertEqual("binary content", result["skipped"][0]["reason"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO")
    def test_secret_scan_rejects_fifo(self) -> None:
        os.mkfifo(self.root / "blocked.txt")
        with self.assertRaises(ForgeError):
            secret_scan(self.root)

    def test_sbom_describes_actual_installed_distribution(self) -> None:
        report = sbom(["pip"])
        self.assertEqual("CycloneDX", report["bomFormat"])
        self.assertEqual("pip", report["components"][0]["name"])
        self.assertTrue(report["components"][0]["version"])
        self.assertEqual([], sbom([])["components"])
        with self.assertRaises(ForgeError):
            sbom(["forge5-nonexistent-evaluation-distribution"])

    def test_license_policy_is_executed_not_just_inventory(self) -> None:
        inventory = license_inventory(["pip"], allowed=[])
        self.assertTrue(inventory["policy_applied"])
        self.assertFalse(inventory["passed"])
        self.assertEqual("pip", inventory["violations"][0]["name"])
        self.assertTrue(license_inventory(["pip"])["passed"])

    def test_missing_dependency_auditor_is_not_success(self) -> None:
        with patch("forge5.quality.importlib.util.find_spec", return_value=None):
            result = dependency_audit(self.root, required=True)
            self.assertFalse(result["available"])
            self.assertFalse(result["passed"])
            report = quality_report(self.root, required_audit=True, packages=[])
            self.assertFalse(report["passed"])
            self.assertEqual("unavailable", report["dependency_audit"]["status"])

    def test_real_auditor_result_contract(self) -> None:
        result = ProcessResult(("fixture",), 0, 0.1, False, json.dumps({"dependencies": []}), False)
        with (
            patch("forge5.quality.importlib.util.find_spec", return_value=object()),
            patch("forge5.quality.run_process", return_value=result) as run,
        ):
            report = dependency_audit(self.root)
        self.assertTrue(report["passed"])
        self.assertIn("pip_audit", run.call_args.args[0])
        bad = ProcessResult(("fixture",), 0, 0.1, False, "incomplete", False)
        with (
            patch("forge5.quality.importlib.util.find_spec", return_value=object()),
            patch("forge5.quality.run_process", return_value=bad),
        ):
            self.assertFalse(dependency_audit(self.root)["passed"])

    def test_combined_report_and_ast_failure(self) -> None:
        self.assertTrue(quality_report(self.root, packages=[])["passed"])
        (self.root / "unsafe.py").write_text("ev" + "al(user_input)\n")
        result = quality_report(self.root, packages=[])
        self.assertFalse(result["passed"])
        self.assertFalse(result["ast"]["passed"])
        self.assertEqual("not_requested", result["dependency_audit"]["status"])
