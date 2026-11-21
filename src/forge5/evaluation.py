"""Reproducible paired repair evaluations; offline fixtures are not AI benchmarks."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge5.models import ForgeError, identifier
from forge5.runner import run_process
from forge5.store import canonical, digest, now
from forge5.workspace import fingerprint


@dataclass(frozen=True)
class Case:
    name: str
    goal: str
    broken: dict[str, str]
    repaired: dict[str, str]
    acceptance: str
    regression: str


def case_suite() -> dict[str, Case]:
    """Small unseen-to-the-test-runner defects with separate regression contracts."""
    definitions = [
        Case(
            "logic",
            "Repair invoice totals: discount is a fraction of the subtotal, then tax applies.",
            {
                "pricing.py": (
                    "def total(amount, discount, tax):\n return (amount-discount)*(1+tax)\n"
                )
            },
            {
                "pricing.py": (
                    "def total(amount, discount, tax):\n return amount*(1-discount)*(1+tax)\n"
                )
            },
            (
                "from pricing import total\nclass Acceptance(unittest.TestCase"
                "):\n def test_discount(self): self.assertAlmostEqual(total(10"
                "0,.2,.1),88)\n def test_full_discount(self): self.assertEqual"
                "(total(100,1,.2),0)\n"
            ),
            (
                "from pricing import total\nclass Regression(unittest.TestCase"
                "):\n def test_no_discount(self): self.assertEqual(total(100,0"
                ",0),100)\n"
            ),
        ),
        Case(
            "concurrency",
            "Make the shared Counter increments safe across concurrent threads.",
            {
                "counter.py": (
                    "import time\nclass Counter:\n def __init__(self): self.value=0"
                    "\n def increment(self):\n  old=self.value\n  time.sleep(.0001)\n"
                    "  self.value=old+1\n"
                )
            },
            {
                "counter.py": (
                    "from threading import Lock\nclass Counter:\n def __init__(self"
                    "): self.value=0; self.lock=Lock()\n def increment(self):\n  wi"
                    "th self.lock: self.value+=1\n"
                )
            },
            (
                "from counter import Counter\nfrom concurrent.futures import T"
                "hreadPoolExecutor\nclass Acceptance(unittest.TestCase):\n def "
                "test_parallel(self):\n  c=Counter()\n  with ThreadPoolExecutor"
                "(max_workers=8) as p: list(p.map(lambda _:c.increment(),rang"
                "e(160)))\n  self.assertEqual(c.value,160)\n"
            ),
            (
                "from counter import Counter\nclass Regression(unittest.TestCa"
                "se):\n def test_serial(self):\n  c=Counter(); c.increment(); c"
                ".increment(); self.assertEqual(c.value,2)\n"
            ),
        ),
        Case(
            "security",
            (
                "Confine resource resolution to the supplied root; reject tra"
                "versal and symlink escapes."
            ),
            {
                "paths.py": (
                    "from pathlib import Path\ndef resource(root, name):\n return Path(root)/name\n"
                )
            },
            {
                "paths.py": (
                    "from pathlib import Path\ndef resource(root, name):\n root=Pat"
                    "h(root).resolve(); result=(root/name).resolve()\n if not resu"
                    "lt.is_relative_to(root): raise ValueError('outside root')\n r"
                    "eturn result\n"
                )
            },
            (
                "from paths import resource\nfrom pathlib import Path\nimport t"
                "empfile\nclass Acceptance(unittest.TestCase):\n def test_trave"
                "rsal(self):\n  with tempfile.TemporaryDirectory() as root:\n  "
                " with self.assertRaises(ValueError): resource(root,'../outsi"
                "de')\n def test_symlink(self):\n  with tempfile.TemporaryDirec"
                "tory() as root, tempfile.TemporaryDirectory() as outside:\n  "
                " Path(root,'link').symlink_to(outside,target_is_directory=Tr"
                "ue)\n   with self.assertRaises(ValueError): resource(root,'li"
                "nk/file')\n"
            ),
            (
                "from paths import resource\nfrom pathlib import Path\nimport t"
                "empfile\nclass Regression(unittest.TestCase):\n def test_local"
                "(self):\n  with tempfile.TemporaryDirectory() as root: self.a"
                "ssertEqual(resource(root,'file'),Path(root,'file'))\n"
            ),
        ),
        Case(
            "migration",
            "Make the SQLite email-column migration idempotent without losing legacy rows.",
            {
                "migration.py": (
                    "def migrate(db):\n db.execute('ALTER TABLE users ADD COLUMN email TEXT')\n"
                )
            },
            {
                "migration.py": (
                    "def migrate(db):\n with db:\n  columns={row[1] for row in db.e"
                    "xecute('PRAGMA table_info(users)')}\n  if 'email' not in colu"
                    "mns: db.execute('ALTER TABLE users ADD COLUMN email TEXT')\n"
                )
            },
            (
                "from migration import migrate\nimport sqlite3\nclass Acceptanc"
                "e(unittest.TestCase):\n def test_repeat(self):\n  with sqlite3"
                ".connect(':memory:') as db:\n   db.execute('CREATE TABLE user"
                "s(id INTEGER,name TEXT)'); db.execute(\"INSERT INTO users VAL"
                "UES(1,'Ada')\"); migrate(db); migrate(db)\n   self.assertEqual"
                "(db.execute('SELECT name,email FROM users').fetchall(),[('Ad"
                "a',None)])\n"
            ),
            (
                "from migration import migrate\nimport sqlite3\nclass Regressio"
                "n(unittest.TestCase):\n def test_first_migration(self):\n  wit"
                "h sqlite3.connect(':memory:') as db:\n   db.execute('CREATE T"
                "ABLE users(id INTEGER)'); migrate(db)\n   self.assertIn('emai"
                "l',[r[1] for r in db.execute('PRAGMA table_info(users)')])\n"
            ),
        ),
        Case(
            "performance",
            (
                "Optimize repeated membership queries for hashable values whi"
                "le preserving query order and duplicates."
            ),
            {
                "lookup.py": (
                    "def select(values, queries):\n return [q for q in queries if q in values]\n"
                )
            },
            {
                "lookup.py": (
                    "def select(values, queries):\n index=set(values)\n return [q f"
                    "or q in queries if q in index]\n"
                )
            },
            (
                "from lookup import select\nclass Probe:\n calls=0\n def __init_"
                "_(self,n): self.n=n\n def __hash__(self): return self.n\n def "
                "__eq__(self,other): Probe.calls+=1; return self.n==other.n\nc"
                "lass Acceptance(unittest.TestCase):\n def test_operation_budg"
                "et(self):\n  values=[Probe(i) for i in range(500)]; queries=["
                "Probe(i) for i in range(400,500)]; Probe.calls=0\n  self.asse"
                "rtEqual(len(select(values,queries)),100); self.assertLess(Pr"
                "obe.calls,1500)\n"
            ),
            (
                "from lookup import select\nclass Regression(unittest.TestCase"
                "):\n def test_order(self): self.assertEqual(select([1,2,3],[3"
                ",7,1,3]),[3,1,3])\n"
            ),
        ),
    ]
    return {case.name: case for case in definitions}


FIXTURE_PROVIDER = (
    "import hashlib,json,sys\nfrom pathlib import Path\np=json.load"
    "(sys.stdin)\nfixed=json.loads(Path(__file__).with_name('fixtu"
    "re-fixes.json').read_text())\nprotocol=p['protocol']\nif proto"
    "col=='forge5.plan.v1':\n print(json.dumps({'summary':'Determi"
    "nistic evaluation fixture plan, not model reasoning','tasks'"
    ":[{'id':'repair','title':p.get('goal','Repair fixture'),'dep"
    "endencies':[],'acceptance':'Acceptance and regression tests "
    "both pass','allowed_paths':list(fixed)}]}))\nelif protocol=='"
    "forge5.implement.v1':\n changes=[]\n for name,content in fixed"
    ".items():\n  target=Path(name)\n  old=target.read_bytes() if t"
    "arget.exists() else None\n  if old is None or old.decode()!=c"
    "ontent:\n   changes.append({'path':name,'before_sha256':hashl"
    "ib.sha256(old).hexdigest() if old is not None else None,'con"
    "tent':content})\n print(json.dumps({'summary':'Applied declar"
    "ed fixture repair; no LLM inference','changes':changes,'evid"
    "ence':'Tests must be executed by the harness; this fixture d"
    "oes not claim test success'}))\nelif protocol=='forge5.review"
    ".v1':\n # Contract assertion over actual source, not an inven"
    "ted model opinion.\n matches=all(Path(name).read_text()==cont"
    "ent for name,content in fixed.items())\n print(json.dumps({'r"
    "ole':p['role'],'verdict':'approve' if matches else 'request_"
    "changes','summary':'Deterministic source-contract fixture; n"
    "ot independent AI review','findings':[] if matches else [{'i"
    "d':'source-contract','severity':'high','title':'Repair contr"
    "act unmet','detail':'Implementation differs from the declare"
    "d evaluation repair','evidence':'Compare source against fixt"
    "ure contract'}]}))\nelse:\n raise SystemExit('Unsupported fixt"
    "ure protocol '+protocol)\n"
)


def _write_json(path: Path, data: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def _prepare(root: Path, case: Case, timeout: float = 30) -> None:
    root.mkdir(parents=True)
    for name, content in {
        **case.broken,
        "service.py": f"from {next(iter(case.broken)).removesuffix('.py')} import "
        + {
            "logic": "total",
            "concurrency": "Counter",
            "security": "resource",
            "migration": "migrate",
            "performance": "select",
        }[case.name]
        + "\n",
    }.items():
        (root / name).write_text(content)
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_acceptance.py").write_text(
        "import unittest\n"
        + case.acceptance.replace(
            "from " + next(iter(case.broken)).removesuffix(".py") + " import ",
            "from service import ",
        )
    )
    (tests / "test_regression.py").write_text(
        "import unittest\n"
        + case.regression.replace(
            "from " + next(iter(case.broken)).removesuffix(".py") + " import ",
            "from service import ",
        )
    )
    state = root / ".forge5"
    state.mkdir()
    (state / "fixture-provider.py").write_text(FIXTURE_PROVIDER)
    _write_json(state / "fixture-fixes.json", case.repaired)
    (root / "forge5.toml").write_text(
        "schema=1\n"
        + "\n".join(
            f'[[checks]]\nname="{kind}"\nkind="{kind}"\nargv={json.dumps(argv)}\ntimeout={timeout}\nrequired=true\n'
            for kind, argv in [
                ("test", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]),
                ("security", [sys.executable, "-m", "forge5", "scan", "."]),
            ]
        )
    )


def _tests(root: Path, pattern: str, timeout: float) -> dict[str, Any]:
    result = run_process(
        (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", pattern, "-v"),
        root,
        timeout,
    )
    count = re.search(r"Ran (\d+) tests?", result.output)
    test_count = int(count.group(1)) if count else 0
    return {
        **result.to_dict(),
        "tests": test_count,
        "failures": sum(int(x) for x in re.findall(r"failures=(\d+)", result.output)),
        "errors": sum(int(x) for x in re.findall(r"errors=(\d+)", result.output)),
        "passed": result.exit_code == 0
        and not result.timed_out
        and not result.truncated
        and test_count > 0,
    }


def _test_digest(root: Path) -> str:
    return digest({p.name: p.read_text() for p in sorted((root / "tests").glob("*.py"))})


def _apply_baseline(root: Path, response: Any, allowed: set[str]) -> None:
    if not isinstance(response, dict) or not isinstance(response.get("changes"), list):
        raise ForgeError("Baseline provider must return an implementation change set")
    if len(response["changes"]) > len(allowed):
        raise ForgeError("Baseline change set exceeds allowed file count")
    seen: set[str] = set()
    staged = []
    for change in response["changes"]:
        if not isinstance(change, dict) or set(change) != {"path", "before_sha256", "content"}:
            raise ForgeError("Invalid baseline change entry")
        name = change["path"]
        if name not in allowed or name in seen:
            raise ForgeError("Baseline edit outside declared task paths")
        seen.add(name)
        target = root / name
        if target.is_symlink():
            raise ForgeError("Baseline source symlink")
        old = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        if old != change["before_sha256"]:
            raise ForgeError("Stale baseline change")
        content = change["content"]
        if not isinstance(content, str) or len(content.encode()) > 262144:
            raise ForgeError("Baseline content must be bounded text")
        staged.append((target, content))
    for target, content in staged:
        target.write_text(content)


def _baseline(
    root: Path, case: Case, argv: tuple[str, ...], rounds: int, timeout: float
) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    protected = _test_digest(root)
    for index in range(rounds):
        payload = {
            "protocol": "forge5.implement.v1",
            "goal": case.goal,
            "task": {
                "id": "repair",
                "allowed_paths": list(case.broken),
                "acceptance": "Acceptance and regression suites pass",
            },
            "round": index + 1,
            "root": str(root),
            "previous_results": history,
        }
        result = run_process(argv, root, timeout, stdin=canonical(payload), output_limit=262144)
        if result.exit_code != 0 or result.timed_out or result.truncated:
            raise ForgeError("Baseline provider failed or exceeded execution limits")
        _apply_baseline(root, json.loads(result.output), set(case.broken))
        if _test_digest(root) != protected:
            raise ForgeError("Baseline provider modified protected evaluation tests")
        acceptance = _tests(root, "test_acceptance.py", timeout)
        regression = _tests(root, "test_regression.py", timeout)
        history.append({"round": index + 1, "acceptance": acceptance, "regression": regression})
        if acceptance["passed"] and regression["passed"]:
            break
    return {"rounds": len(history), "provider_calls": len(history), "history": history}


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# FORGE-5 paired evaluation",
        "",
        f"Mode: {report['mode']}",
        "",
        report["interpretation"],
        "",
        "| Case | Arm | Success | Rounds | Seconds | Tests | Regressions |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report["results"]:
        for arm in ("baseline", "forge5"):
            row = item[arm]
            lines.append(
                f"| {item['case']} | {arm} | {row['success']} | {row['rounds']} | "
                f"{row['seconds']:.3f} | {row['tests']} | {row['regressions']} |"
            )
    lines.extend(
        [
            "",
            "Cost is null when the provider supplies no independently observable usage data.",
            (
                "Local execution is not a sandbox. Fixture timings measure or"
                "chestration overhead, not model quality."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def evaluate(
    root: Path,
    *,
    mode: str = "fixture",
    agents_file: Path | None = None,
    baseline_argv: tuple[str, ...] | None = None,
    cases_selected: list[str] | None = None,
    max_rounds: int = 3,
    timeout: float = 30,
    run_id: str | None = None,
    resume: bool = False,
    execute: bool = False,
    allow_unisolated: bool = False,
) -> dict[str, Any]:
    """Evaluate actual repair outcomes on fresh paired workspaces.

    Fixture mode is a deterministic protocol integration evaluation, explicitly
    not a measure of model engineering capability or five independent minds.
    """
    if not execute:
        raise ForgeError("Evaluation executes project/provider code; pass execute=True")
    if (
        mode not in ("fixture", "providers")
        or not 1 <= max_rounds <= 20
        or not 0.05 <= timeout <= 3600
    ):
        raise ForgeError("Invalid evaluation mode or execution budget")
    if not allow_unisolated:
        raise ForgeError(
            "Evaluation currently requires explicit allow_unisolated=True"
            "; provider isolation is reported separately"
        )
    selected = cases_selected or list(case_suite())
    definitions = case_suite()
    if not selected or len(set(selected)) != len(selected) or set(selected) - definitions.keys():
        raise ForgeError("Select unique known evaluation cases")
    if mode == "providers" and (agents_file is None or baseline_argv is None):
        raise ForgeError("Provider evaluation requires both agents_file and baseline_argv")
    registry_text = agents_file.read_text() if agents_file else None
    contract = digest(
        {
            "cases": [definitions[name].__dict__ for name in selected],
            "mode": mode,
            "registry": registry_text,
            "baseline": baseline_argv,
            "rounds": max_rounds,
            "timeout": timeout,
            "python": sys.executable,
            "fixture_provider": FIXTURE_PROVIDER,
            "engine_sources": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(Path(__file__).parent.glob("*.py"))
            },
        }
    )
    base = root.resolve() / ".forge5" / "evaluations"
    if (root.resolve() / ".forge5").is_symlink() or base.is_symlink():
        raise ForgeError("Evaluation state directories must not be symlinks")
    base.mkdir(parents=True, exist_ok=True)
    destination = base / identifier(run_id or f"run-{time.time_ns()}")
    manifest = destination / "manifest.json"
    if destination.exists():
        if destination.is_symlink() or not resume or not manifest.is_file():
            raise ForgeError("Evaluation run already exists; use resume for its matching manifest")
        if json.loads(manifest.read_text())["contract"] != contract:
            raise ForgeError("Evaluation resume contract changed")
    else:
        destination.mkdir()
        _write_json(manifest, {"contract": contract, "created_at": now(), "mode": mode})
    results = []
    for name in selected:
        case = definitions[name]
        checkpoint = destination / f"{name}.json"
        if checkpoint.exists() and resume:
            recorded = json.loads(checkpoint.read_text())
            for arm in ("baseline", "forge5"):
                if recorded[arm]["source_digest"] != fingerprint(destination / name / arm):
                    raise ForgeError("Completed evaluation workspace changed; start a new run")
            if all(recorded[arm]["success"] for arm in ("baseline", "forge5")):
                results.append(recorded)
                continue
        case_root = destination / name
        arms = {}
        for arm in ("baseline", "forge5"):
            workspace = case_root / arm
            arm_checkpoint = case_root / (arm + "-result.json")
            initial_path = case_root / (arm + "-initial.json")
            if resume and arm_checkpoint.exists():
                recorded_arm = json.loads(arm_checkpoint.read_text())
                if recorded_arm["source_digest"] != fingerprint(workspace):
                    raise ForgeError("Evaluation arm changed after checkpoint")
                if recorded_arm["success"]:
                    arms[arm] = recorded_arm
                    continue
            if not workspace.exists():
                _prepare(workspace, case, timeout)
                initial = {
                    "before": {
                        "acceptance": _tests(workspace, "test_acceptance.py", timeout),
                        "regression": _tests(workspace, "test_regression.py", timeout),
                    },
                    "protected_test_digest": _test_digest(workspace),
                }
                _write_json(initial_path, initial)
            elif not resume or not initial_path.is_file() or workspace.is_symlink():
                raise ForgeError("Partial evaluation workspace lacks a trusted initial checkpoint")
            initial = json.loads(initial_path.read_text())
            before = initial["before"]
            protected = initial["protected_test_digest"]
            if _test_digest(workspace) != protected:
                raise ForgeError("Protected evaluation tests changed before resumption")
            start = time.monotonic()
            argv = baseline_argv or (
                sys.executable,
                str(workspace / ".forge5" / "fixture-provider.py"),
            )
            try:
                if arm == "baseline":
                    execution = _baseline(workspace, case, argv, max_rounds, timeout)
                else:
                    execution = _forge(workspace, case, agents_file, mode, max_rounds, timeout)
                error = None
                if arm == "forge5" and execution["result"]["outcome"] != "ready":
                    error = "FORGE-5 release conditions remain blocked"
            except Exception as exc:
                execution = {"rounds": 0, "error": f"{type(exc).__name__}: {exc}"}
                error = execution["error"]
            after = {
                "acceptance": _tests(workspace, "test_acceptance.py", timeout),
                "regression": _tests(workspace, "test_regression.py", timeout),
            }
            tampered = _test_digest(workspace) != protected
            arms[arm] = {
                "seconds": time.monotonic() - start,
                "rounds": execution.get("rounds", 0),
                "provider_calls": execution.get("provider_calls", 0),
                "tests": after["acceptance"]["tests"] + after["regression"]["tests"],
                "failures": after["acceptance"]["failures"] + after["regression"]["failures"],
                "errors": after["acceptance"]["errors"] + after["regression"]["errors"],
                "success": not error
                and not tampered
                and after["acceptance"]["passed"]
                and after["regression"]["passed"],
                "regressions": int(
                    before["regression"]["passed"] and not after["regression"]["passed"]
                ),
                "error": error,
                "protected_tests_changed": tampered,
                "cost": None,
                "before": before,
                "after": after,
                "execution": execution,
                "source_digest": fingerprint(workspace),
                "isolation": "unisolated-local; command adapters are trusted",
            }
            _write_json(arm_checkpoint, arms[arm])
        result = {"case": name, "goal": case.goal, **arms}
        _write_json(checkpoint, result)
        results.append(result)
    report = {
        "schema": 1,
        "mode": mode,
        "contract": contract,
        "run_id": destination.name,
        "created_at": now(),
        "results": results,
        "max_rounds_per_arm": max_rounds,
        "timeout_per_process": timeout,
        "path": str(destination),
        "independent_ai_agents": False
        if mode == "fixture"
        else "configured identities; not authenticated",
        "interpretation": (
            ("Deterministic fixture integration evaluation: identical repair oracle in both arms. ")
            + "No claim of LLM superiority or reviewer independence."
            if mode == "fixture"
            else (
                "Configured-provider paired evaluation; inspect provider iden"
                "tities, usage limits and errors."
            )
        ),
        "success": all(item[arm]["success"] for item in results for arm in ("baseline", "forge5")),
        "passed": all(item[arm]["success"] for item in results for arm in ("baseline", "forge5")),
    }
    _write_json(destination / "report.json", report)
    (destination / "report.md").write_text(markdown(report))
    return report


def _forge(
    root: Path, case: Case, agents_file: Path | None, mode: str, rounds: int, timeout: float
) -> dict[str, Any]:
    """Loaded lazily so project inspection/CLI help does not execute providers."""
    from forge5.agents import AgentRegistry
    from forge5.development import DevelopmentOrchestrator
    from forge5.engine import Council
    from forge5.isolation import IsolationPolicy

    if mode == "fixture":
        argv = [sys.executable, str(root / ".forge5" / "fixture-provider.py")]
        data = {
            "schema": 1,
            "agents": {
                role: {
                    "argv": argv,
                    "provider": "deterministic-fixture",
                    "identity": f"fixture-{role}",
                    "timeout": timeout,
                }
                for role in (
                    "architect",
                    "principal",
                    "debugger",
                    "security",
                    "product",
                    "implementation",
                )
            },
        }
        config = root / ".forge5" / "agents.json"
        _write_json(config, data)
    else:
        if agents_file is None:
            raise ForgeError("Provider evaluation requires an agents file")
        config = agents_file.resolve()
    council = (
        Council(root)
        if (root / ".forge5" / "council.sqlite3").exists()
        else Council.initialize(root, case.goal)
    )
    registry = AgentRegistry.load(config)
    orchestrator = DevelopmentOrchestrator(
        council,
        registry,
        IsolationPolicy(mode="local", allow_local=True, timeout=timeout),
        execute=True,
    )
    if "development" not in council.store.read()[1]:
        orchestrator.plan(case.goal)
    result = orchestrator.build(max_rounds=rounds)
    return {
        "rounds": len(result.get("development", {}).get("rounds", [])),
        "result": result,
        "audit_events": len(council.store.events()),
        "provider_calls": sum(
            event["action"] == "agent.request" for event in council.store.events()
        ),
    }
