from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from forge5.agents import CONTRACTS, AgentRegistry, AgentSpec
from forge5.development import ChangeSet, DevelopmentOrchestrator
from forge5.engine import Council
from forge5.isolation import IsolationPolicy
from forge5.models import ForgeError
from forge5.workspace import fingerprint

PROVIDER = (
    "import hashlib,json,sys\n"
    "from pathlib import Path\n"
    "if Path(__file__).with_name('fail-marker').exists(): raise System"
    "Exit(9)\n"
    "p=json.load(sys.stdin)\n"
    "role=p['role']; protocol=p['protocol']; root=Path.cwd()\n"
    "if protocol=='forge5.plan.v1':\n"
    " result={'summary':'Two-module calculator with boundary regressio"
    "n', 'tasks':[{'id':'calculator','title':'Implement calculator','d"
    "ependencies':[],'acceptance':'Positive and negative inputs double"
    " correctly','allowed_paths':['calc/core.py','tests/test_positive."
    "py','tests/test_negative.py']}]}\n"
    "elif protocol=='forge5.implement.v1':\n"
    " repair=bool(p.get('open_findings'))\n"
    " code='def compute(value):\\n return value*2\\n' if repair else 'de"
    "f compute(value):\\n return value*2 if value>=0 else 0\\n'\n"
    " files={'calc/core.py':code}\n"
    " if repair: files['tests/test_negative.py']='import unittest\\nfro"
    "m calc.core import compute\\nclass Negative(unittest.TestCase):\\n "
    "def test_negative(self): self.assertEqual(compute(-2),-4)\\n'\n"
    " else: files['tests/test_positive.py']='import unittest\\nfrom cal"
    "c.core import compute\\nclass Positive(unittest.TestCase):\\n def t"
    "est_positive(self): self.assertEqual(compute(3),6)\\n'\n"
    " result={'summary':'Repair negative boundary' if repair else 'Imp"
    "lement positive slice','evidence':'Host must run generated tests'"
    ",'changes':[{'path':name,'before_sha256':hashlib.sha256((root/nam"
    "e).read_bytes()).hexdigest() if (root/name).is_file() else None,'"
    "content':content} for name,content in files.items()]}\n"
    "elif protocol=='forge5.disposition.v1':\n"
    " result={'finding_id':p['finding']['id'],'disposition':'fixed','r"
    "ationale':'Inspected negative path and current regression check',"
    "'evidence_names':['tests']}\n"
    "else:\n"
    " bad='else 0' in (root/'calc/core.py').read_text()\n"
    " findings=[{'id':'negative-boundary','severity':'medium','title':"
    "'Negative inputs lost','detail':'Negative values incorrectly retu"
    "rn zero','evidence':'calc/core.py:2','claim':'compute(-2) should "
    "return -4','locations':[{'path':'calc/core.py','start_line':2,'en"
    "d_line':2}],'required_test':'tests'}] if role=='debugger' and bad"
    " else []\n"
    " result={'role':role,'verdict':'request_changes' if findings else"
    " 'approve','summary':'Deterministic integration fixture; not an A"
    "I review','findings':findings}\n"
    "print(json.dumps(result))\n"
)


class DevelopmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "calc").mkdir()
        (self.root / "calc/__init__.py").write_text("")
        (self.root / "calc/core.py").write_text("def compute(value):\n return value\n")
        (self.root / "tests").mkdir()
        (self.root / "tests/test_zero.py").write_text(
            "import unittest\n"
            "from calc.core import compute\n"
            "class Zero(unittest.TestCase):\n"
            " def test_zero(self): self.assertEqual(compute(0),0)\n"
        )
        self.c = Council.initialize(self.root, "Double all integer inputs")
        (self.root / "forge5.toml").write_text(
            "schema=1\n"
            + "\n".join(
                f'[[checks]]\nname="{name}"\nkind="{kind}"\nargv={json.dumps(argv)}\ntimeout=10\nrequired=true\n'
                for name, kind, argv in [
                    (
                        "tests",
                        "test",
                        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                    ),
                    (
                        "security",
                        "security",
                        [
                            sys.executable,
                            "-c",
                            (
                                "import ast; from pathlib import Path; [ast.parse(p.read_text()) f"
                                'or p in Path("calc").glob("*.py")]'
                            ),
                        ],
                    ),
                ]
            )
        )
        provider = self.root / ".forge5/provider.py"
        provider.write_text(PROVIDER)
        self.registry = AgentRegistry(
            {
                role: AgentSpec(
                    role,
                    (sys.executable, str(provider)),
                    "deterministic-test-fixture",
                    f"fixture-{role}",
                )
                for role in CONTRACTS
            }
        )
        self.policy = IsolationPolicy(mode="local", allow_local=True, timeout=10)
        self.o = DevelopmentOrchestrator(self.c, self.registry, self.policy, execute=True)

    def test_real_plan_implementation_objection_repair_and_release(self):
        self.o.plan("Double all integer inputs")
        result = self.o.build(max_rounds=3)
        self.assertEqual(result["outcome"], "ready", result["development"])
        self.assertTrue(result["status"]["release_ready"])
        finding = result["status"]["state"]["findings"]["debugger.negative-boundary"]
        self.assertEqual(finding["lifecycle"], "fixed")
        self.assertTrue((self.root / "tests/test_negative.py").exists())
        self.assertGreaterEqual(len(list((self.root / ".forge5/changes").glob("*.patch"))), 2)
        self.assertEqual(
            {
                r["identity"]
                for r in [
                    json.loads(e["payload"])
                    for e in self.c.store.events()
                    if e["action"] == "agent.request"
                ]
            },
            {f"fixture-{role}" for role in CONTRACTS},
        )
        self.c.store.verify()
        # Resume does not reapply completed implementation or alter source.
        before = fingerprint(self.root)
        self.assertEqual(self.o.build(max_rounds=1)["outcome"], "ready")
        self.assertEqual(before, fingerprint(self.root))

    def test_authorization_and_missing_plan(self):
        with self.assertRaises(ForgeError):
            DevelopmentOrchestrator(self.c, self.registry, self.policy).plan("Goal")
        with self.assertRaises(ForgeError):
            self.o.build()
        with self.assertRaises(ForgeError):
            self.o.build(max_rounds=0)

    def test_agent_failures_persist_and_retry(self):
        self.o.plan("Double all integer inputs")
        marker = self.root / ".forge5/fail-marker"
        marker.touch()
        failed = self.o.build(max_rounds=1)
        self.assertEqual(failed["outcome"], "blocked")
        self.assertIn("last_error", failed["development"])
        marker.unlink()
        self.assertEqual(self.o.build(max_rounds=3)["outcome"], "ready")

    def test_changed_provider_configuration_invalidates_prior_evidence(self):
        self.o.plan("Double all integer inputs")
        self.assertEqual(self.o.build(max_rounds=3)["outcome"], "ready")
        from dataclasses import replace

        changed = AgentRegistry(
            {role: replace(spec, model="changed") for role, spec in self.registry.specs.items()}
        )
        other = DevelopmentOrchestrator(self.c, changed, self.policy, execute=True)
        with self.assertRaisesRegex(ForgeError, "configuration changed"):
            other.build()
        state = self.c.store.read()[1]
        self.assertFalse(state["reviews"])
        self.assertFalse(state["evidence"])
        self.assertTrue(state["development"]["policy_stale"])
        self.assertFalse(self.c.status()["release_ready"])
        with self.assertRaisesRegex(ForgeError, "configuration changed"):
            self.o.build()
        other.plan("Double all integer inputs with changed provider")
        self.assertEqual(self.c.store.read()[1]["development"]["phase"], "planned")
        self.assertNotIn("policy_stale", self.c.store.read()[1]["development"])

    def test_replan_replaces_owned_tasks_and_builds_to_release(self):
        from unittest.mock import patch

        self.o.plan("Original plan")
        replacement = {
            "summary": "Replacement plan",
            "tasks": [
                {
                    "id": "replacement",
                    "title": "Replace calculator task",
                    "dependencies": [],
                    "acceptance": "Double inputs",
                    "allowed_paths": [
                        "calc/core.py",
                        "tests/test_positive.py",
                        "tests/test_negative.py",
                    ],
                }
            ],
        }
        with patch.object(self.o, "_invoke", return_value=replacement):
            self.o.plan("Replacement plan")
        self.assertEqual(set(self.c.store.read()[1]["tasks"]), {"replacement"})
        self.assertEqual(self.o.build(max_rounds=3)["outcome"], "ready")

    def test_replan_cannot_orphan_external_or_replacement_dependencies(self):
        from unittest.mock import patch

        self.o.plan("Original plan")
        replacement = {
            "summary": "Replacement",
            "tasks": [
                {
                    "id": "replacement",
                    "title": "Task",
                    "dependencies": ["calculator"],
                    "acceptance": "Pass",
                    "allowed_paths": ["calc/core.py"],
                }
            ],
        }
        with (
            patch.object(self.o, "_invoke", return_value=replacement),
            self.assertRaisesRegex(ForgeError, "cycle or unknown"),
        ):
            self.o.plan("Invalid replacement")
        self.c.add_task("external", "External task", "principal", ["calculator"], "Pass")
        replacement["tasks"][0]["dependencies"] = []
        with (
            patch.object(self.o, "_invoke", return_value=replacement),
            self.assertRaisesRegex(ForgeError, "referenced by external"),
        ):
            self.o.plan("Cannot orphan external task")
        self.assertEqual(set(self.c.store.read()[1]["tasks"]), {"calculator", "external"})

    def test_current_invocation_failure_cannot_reuse_prior_ready_outcome(self):
        from unittest.mock import patch

        self.o.plan("Double all integer inputs")
        self.assertEqual(self.o.build(max_rounds=3)["outcome"], "ready")
        with patch.object(self.o, "_checks", side_effect=OSError("Check runner unavailable")):
            failed = self.o.build(max_rounds=1)
        self.assertEqual(failed["outcome"], "blocked")
        self.assertEqual(failed["development"]["phase"], "failed")
        self.assertIn("Check runner unavailable", failed["development"]["last_error"])

    def test_replan_rejects_cycle_through_retained_external_task(self):
        from unittest.mock import patch

        self.o.plan("Original plan")
        self.c.add_task("external", "External", "principal", ["calculator"], "Pass")
        proposed = {
            "summary": "Cyclic replacement",
            "tasks": [
                {
                    "id": "calculator",
                    "title": "Task",
                    "dependencies": ["external"],
                    "acceptance": "Pass",
                    "allowed_paths": ["calc/core.py"],
                }
            ],
        }
        with patch.object(self.o, "_invoke", return_value=proposed), self.assertRaises(ForgeError):
            self.o.plan("Reject cycle")
        self.assertEqual(self.c.store.read()[1]["tasks"]["calculator"]["dependencies"], [])

    def test_replan_cannot_reset_task_under_active_external_dependents(self):
        from unittest.mock import patch

        self.o.plan("Original plan")
        self.c.task_status("calculator", "done", "Original completion evidence")
        self.c.add_task("external", "External", "principal", ["calculator"], "Pass")
        proposed = {
            "summary": "Reset original task",
            "tasks": [
                {
                    "id": "calculator",
                    "title": "Task",
                    "dependencies": [],
                    "acceptance": "Changed acceptance",
                    "allowed_paths": ["calc/core.py"],
                }
            ],
        }
        for status in ("running", "done"):
            self.c.task_status(
                "external", status, "External completion" if status == "done" else None
            )
            with (
                patch.object(self.o, "_invoke", return_value=proposed),
                self.assertRaisesRegex(ForgeError, "Reset active external dependent"),
            ):
                self.o.plan("Invalid reset")
            state = self.c.store.read()[1]
            self.assertEqual(state["tasks"]["calculator"]["status"], "done")
            self.assertEqual(state["tasks"]["external"]["status"], status)
            self.assertEqual(
                state["tasks"]["calculator"]["completion_evidence"], "Original completion evidence"
            )

    def test_two_tasks_can_repair_separately_failing_modules(self):
        (self.root / "calc/other.py").write_text("def triple(value): return value\n")
        for name, module, function, expected in [
            ("double", "core", "compute", 4),
            ("triple", "other", "triple", 6),
        ]:
            (self.root / f"tests/test_{name}.py").write_text(
                "import unittest\n"
                f"from calc.{module} import {function}\n"
                "class Check(unittest.TestCase):\n"
                f" def test_value(self): self.assertEqual({function}(2),{expected})\n"
            )
        policy = "schema=1\n"
        for name, pattern, kind in [
            ("double", "test_double.py", "test"),
            ("triple", "test_triple.py", "security"),
        ]:
            argv = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", pattern]
            policy += (
                f'[[checks]]\nname="{name}"\nkind="{kind}"\n'
                f"argv={json.dumps(argv)}\ntimeout=10\nrequired=true\n"
            )
        (self.root / "forge5.toml").write_text(policy)
        provider = self.root / ".forge5/provider.py"
        provider.write_text(
            "import json,sys,hashlib\nfrom pathlib import Path\np=json.load(sys.stdin)\n"
            + (
                "if p['protocol']=='forge5.plan.v1':\n"
                " r={'summary':'Separate repairs','tasks':[{'id':n,'title':n,'depe"
                "ndencies':[] if n=='double' else ['double'],'acceptance':'Regress"
                "ion passes','allowed_paths':[path],'check_names':[n]} for n,path "
                "in [('double','calc/core.py'),('triple','calc/other.py')]]}\n"
            )
            + (
                "elif p['protocol']=='forge5.implement.v1':\n"
                " path=p['allowed_paths'][0]; code='def compute(value): return val"
                "ue*2\\n' if p['task']['id']=='double' else 'def triple(value): ret"
                "urn value*3\\n'; r={'summary':'Repair module','evidence':'Generate"
                "d change requires host tests','changes':[{'path':path,'before_sha"
                "256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'content"
                "':code}]}\n"
            )
            + (
                "else: r={'role':p['role'],'verdict':'approve','summary':'Determin"
                "istic fixture','findings':[]}\n"
                "print(json.dumps(r))\n"
            )
        )
        baseline = self.o._checks(self.c)
        self.assertFalse(all(c["passed"] for c in baseline))
        self.o.plan("Repair two failing modules")
        result = self.o.build(max_rounds=2)
        self.assertEqual(result["outcome"], "ready", result["development"])
        self.assertTrue(all(e["passed"] for e in result["status"]["state"]["evidence"].values()))
        self.assertEqual(len(list((self.root / ".forge5/changes").glob("*.patch"))), 2)

    def test_architecture_failure_is_not_accepted_as_ready(self):
        from forge5.memory import set_contracts

        (self.root / "calc/core.py").write_text(
            "import calc.other\ndef compute(value): return value\n"
        )
        (self.root / "calc/other.py").write_text("import calc.core\n")
        self.o.plan("Double all integer inputs")
        # An explicit module boundary also applies to staged implementations.
        set_contracts(self.c, {"forbidden_imports": [{"from": "tests", "to": "calc"}]})
        result = self.o.build(max_rounds=1)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("Staged required checks failed", result["development"]["last_error"])
        self.assertIn("return value", (self.root / "calc/core.py").read_text())

    def test_changes_reject_scope_stale_policy_and_existing_tests(self):
        path = "calc/core.py"
        before = hashlib.sha256((self.root / path).read_bytes()).hexdigest()
        valid = {
            "summary": "Change",
            "evidence": "Test fixture",
            "changes": [
                {
                    "path": path,
                    "before_sha256": before,
                    "content": "def compute(value): return value*2\n",
                }
            ],
        }
        change = ChangeSet.parse(valid, self.root, [path])
        self.assertIn("a/calc/core.py", change.patch(self.root))
        for bad in (
            "../outside.py",
            "forge5.toml",
            "tests/test_zero.py",
            "./forge5-agents.json",
            "./forge5.toml",
            "calc//core.py",
            "calc/core.py/",
        ):
            candidate = {
                "summary": "Change",
                "evidence": "Fixture",
                "changes": [{"path": bad, "before_sha256": None, "content": "pass"}],
            }
            with self.assertRaises(ForgeError):
                ChangeSet.parse(candidate, self.root, [bad])
        with self.assertRaises(ForgeError):
            ChangeSet.parse(valid, self.root, [])
        (self.root / path).write_text("changed")
        with self.assertRaises(ForgeError):
            change.apply(self.root)

    def test_registry_loading_and_provider_protocol_fail_closed(self):
        from dataclasses import asdict, replace

        config = self.root / ".forge5/agents.json"
        config.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "agents": {
                        role: {key: value for key, value in asdict(spec).items() if key != "role"}
                        for role, spec in self.registry.specs.items()
                    },
                }
            )
        )
        self.assertEqual(AgentRegistry.load(config).describe(), self.registry.describe())
        for raw in ("not JSON", '{"schema":2,"agents":{}}'):
            config.write_text(raw)
            with self.assertRaises(ForgeError):
                AgentRegistry.load(config)
        original = self.registry.specs["architect"]
        for output in ("not JSON", "[]"):
            self.registry.specs["architect"] = replace(
                original, argv=(sys.executable, "-c", f"print({output!r})")
            )
            with self.assertRaises(ForgeError):
                self.registry.invoke(self.c, "architect", {}, self.root, self.policy)
        self.registry.specs["architect"] = replace(
            original,
            argv=(
                sys.executable,
                "-c",
                "from pathlib import Path; Path('changed.py').write_text('pass'); print('{}')",
            ),
        )
        with self.assertRaisesRegex(ForgeError, "edited source directly"):
            self.o._invoke("architect", {})
        self.assertFalse((self.root / "changed.py").exists())

    def test_planner_rejects_cycle_unknown_check_and_unbounded_ownership(self):
        from unittest.mock import patch

        task = {
            "id": "a",
            "title": "Task",
            "dependencies": ["a"],
            "acceptance": "Pass",
            "allowed_paths": ["calc/core.py"],
        }
        cases = [
            task,
            task | {"dependencies": [], "allowed_paths": []},
            task | {"dependencies": [], "check_names": ["missing"]},
            task | {"dependencies": [], "allowed_paths": ["./forge5-agents.json"]},
            task | {"dependencies": [], "allowed_paths": ["forge5-agents.json"]},
            task | {"dependencies": [], "allowed_paths": ["calc//core.py"]},
        ]
        for proposed in cases:
            with (
                patch.object(
                    self.o, "_invoke", return_value={"summary": "Plan", "tasks": [proposed]}
                ),
                self.assertRaises(ForgeError),
            ):
                self.o.plan("Goal")
            self.assertFalse(self.c.store.read()[1]["tasks"])

    def test_registry_independence_is_declared_and_contracts_differ(self):
        self.assertEqual(len({v["mission"] for v in CONTRACTS.values()}), 6)
        self.assertNotEqual(CONTRACTS["architect"]["tools"], CONTRACTS["implementation"]["tools"])
        self.assertIn("not authenticated", self.registry.describe()["identity_assurance"])
        with self.assertRaises(ForgeError):
            AgentRegistry({})
        with self.assertRaises(ForgeError):
            AgentRegistry({role: AgentSpec(role, ("x",), "fixture", "same") for role in CONTRACTS})
        with self.assertRaises(ForgeError):
            AgentSpec.parse("architect", {"argv": [], "provider": "x", "identity": "x"})


if __name__ == "__main__":
    unittest.main()
