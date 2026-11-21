"""Development platform CLI, retaining the original council commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from forge5.config import load_checks
from forge5.engine import Council
from forge5.models import ForgeError, string_list


def output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--output", type=Path)


def extend_parser(sub: Any) -> None:
    for name in ("plan", "build"):
        p = sub.add_parser(name, help=f"{name.title()} a resumable development workflow")
        p.add_argument("--agents", type=Path, default=Path("forge5-agents.json"))
        p.add_argument("--execute", action="store_true")
        p.add_argument(
            "--allow-local", action="store_true", help="Explicitly allow unsandboxed execution"
        )
        p.add_argument("--rounds", type=int, default=3)
        p.add_argument("--isolation", type=Path, help="JSON IsolationPolicy configuration")
        if name == "plan":
            p.add_argument("--goal", required=True)
        output_options(p)
    p = sub.add_parser("security", help="Security triage, secrets, dependency audit and SBOM")
    p.add_argument("--dependency-audit", action="store_true")
    p.add_argument("--require-dependency-audit", action="store_true")
    output_options(p)
    p = sub.add_parser("benchmark", help="Execute configured performance checks and budgets")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--allow-local", action="store_true")
    p.add_argument("--isolation", type=Path, help="JSON IsolationPolicy configuration")
    p.add_argument("--name", action="append")
    output_options(p)
    p = sub.add_parser("evaluate", help="Run matched baseline/council repair evaluations")
    p.add_argument("--mode", choices=["fixture", "providers"], default="fixture")
    p.add_argument("--agents", type=Path)
    p.add_argument("--baseline-json")
    p.add_argument("--case", action="append")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--run-id")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--allow-local", action="store_true")
    output_options(p)
    p = sub.add_parser("architecture", help="Check module boundaries and import cycles")
    p.add_argument("--contracts", type=Path)
    output_options(p)
    p = sub.add_parser("memory", help="Record or search persistent engineering lessons")
    p.add_argument("action", choices=["add", "search"])
    p.add_argument("text")
    p.add_argument("--kind", default=None)
    p.add_argument("--tag", action="append", default=[])
    output_options(p)
    p = sub.add_parser("conflict", help="Inspect, discuss and dispose of engineering objections")
    p.add_argument(
        "action",
        choices=[
            "list",
            "show",
            "respond",
            "disputed",
            "needs_evidence",
            "fixed",
            "rejected",
            "accepted_risk",
            "reopen",
        ],
    )
    p.add_argument("id", nargs="?")
    p.add_argument("--actor")
    p.add_argument("--message")
    p.add_argument("--evidence", action="append", default=[])
    p.add_argument("--independent", action="store_true")
    output_options(p)


def render(result: Any, args: argparse.Namespace) -> Any:
    if getattr(args, "format", "json") == "markdown":
        content = (
            "# FORGE-5 "
            + args.command.title()
            + "\n\n```json\n"
            + json.dumps(result, indent=2, ensure_ascii=False)
            + "\n```\n"
        )
    else:
        content = json.dumps(result, indent=2, ensure_ascii=False)
    if getattr(args, "output", None):
        args.output.write_text(content)
        return {"written": str(args.output.resolve())}
    return content if args.format == "markdown" else result


def platform_execute(args: argparse.Namespace) -> tuple[Any, int] | None:
    command = args.command
    result: dict[str, Any]
    if command == "review" and args.review_command == "run":
        council = Council(args.root)
        from forge5.agents import AgentRegistry
        from forge5.development import DevelopmentOrchestrator

        config = args.agents if args.agents.is_absolute() else council.root / args.agents
        orchestrator = DevelopmentOrchestrator(
            council,
            AgentRegistry.load(config),
            load_isolation(args),
            execute=args.execute,
        )
        result = {"reviews": orchestrator.review()}
        return render(result, args), int(
            any(r.get("verdict") != "approve" for r in result["reviews"])
        )
    supported = {
        "plan",
        "build",
        "security",
        "benchmark",
        "evaluate",
        "architecture",
        "memory",
        "conflict",
    }
    if command not in supported:
        return None
    root = args.root.resolve()
    if command == "security":
        from forge5.quality import quality_report

        result = quality_report(
            root,
            dependency_audit=args.dependency_audit or args.require_dependency_audit,
            required_audit=args.require_dependency_audit,
        )
        return render(result, args), int(not result["passed"])
    if command == "evaluate":
        from forge5.evaluation import evaluate

        result = evaluate(
            root,
            mode=args.mode,
            agents_file=args.agents,
            baseline_argv=tuple(string_list(json.loads(args.baseline_json), "baseline argv"))
            if args.baseline_json
            else None,
            cases_selected=args.case,
            max_rounds=args.rounds,
            timeout=args.timeout,
            run_id=args.run_id,
            resume=args.resume,
            execute=args.execute,
            allow_unisolated=args.allow_local,
        )
        return render(result, args), int(not result.get("passed", False))
    if command == "plan" and not (root / ".forge5" / "council.sqlite3").exists():
        if not args.execute:
            raise ForgeError("Planning executes a trusted agent; pass --execute")
        council = Council.initialize(root, args.goal)
    else:
        council = Council(root)
    if command in ("plan", "build"):
        result = development_command(council, args)
        return render(result, args), int(result.get("outcome") in ("blocked", "failed"))
    if command == "benchmark":
        if not args.execute:
            raise ForgeError("Benchmark execution requires --execute")
        names = [c.name for c in load_checks(root) if c.kind == "performance"]
        if args.name:
            if set(args.name) - set(names):
                raise ForgeError("Only configured performance checks can be benchmarked")
            names = args.name
        if not names:
            raise ForgeError("Configure at least one performance check and explicit budget first")
        from forge5.tools import ToolBroker

        broker = ToolBroker(council, "orchestrator", isolation=load_isolation(args))
        result = {"checks": [broker.call("run_check", name=name) for name in names]}
        result["passed"] = all(c["passed"] for c in result["checks"])
        return render(result, args), int(not result["passed"])
    if command == "architecture":
        from forge5.architecture import check_architecture
        from forge5.memory import get_contracts

        contracts = (
            json.loads(args.contracts.read_text()) if args.contracts else get_contracts(council)
        )
        result = check_architecture(root, contracts)
        council.store.mutate("architecture.check", result, lambda state: None)
        return render(result, args), int(not result["passed"])
    if command == "memory":
        from forge5.memory import record_memory, search_memory

        result = (
            {"id": record_memory(council, args.kind or "lesson", args.text, args.tag)}
            if args.action == "add"
            else {"matches": search_memory(council, args.text, args.kind)}
        )
        return render(result, args), 0
    if args.action in ("list", "show"):
        findings = council.store.read()[1]["findings"]
        if args.action == "show" and args.id not in findings:
            raise ForgeError("Unknown conflict")
        result = findings if args.action == "list" else findings[args.id]
    else:
        if not args.id or not args.actor or not args.message:
            raise ForgeError("Conflict mutations require id, --actor and --message")
        council.conflict(
            args.id,
            args.actor,
            args.action,
            args.message,
            args.evidence,
            independent=args.independent,
        )
        result = council.store.read()[1]["findings"][args.id]
    return render(result, args), 0


def development_command(council: Council, args: argparse.Namespace) -> dict[str, Any]:
    from forge5.agents import AgentRegistry
    from forge5.development import DevelopmentOrchestrator

    policy = load_isolation(args)
    config = args.agents if args.agents.is_absolute() else council.root / args.agents
    orchestrator = DevelopmentOrchestrator(
        council, AgentRegistry.load(config), policy, execute=args.execute
    )
    return (
        orchestrator.plan(args.goal)
        if args.command == "plan"
        else orchestrator.build(max_rounds=args.rounds)
    )


def load_isolation(args: argparse.Namespace) -> Any:
    from forge5.isolation import IsolationPolicy

    data = json.loads(args.isolation.read_text()) if args.isolation else {}
    if not isinstance(data, dict):
        raise ForgeError("Isolation configuration must be a JSON object")
    if args.allow_local:
        data["allow_local"] = True
    try:
        return IsolationPolicy(**data)
    except TypeError as exc:
        raise ForgeError(f"Invalid isolation configuration: {exc}") from exc
