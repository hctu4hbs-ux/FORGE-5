"""A JSON-first command line for coding-agent hosts and humans."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from forge5 import __version__
from forge5.council import CommandReviewer, command_repair, improve, packet, review_round
from forge5.engine import Council
from forge5.models import ForgeError, Review, Role, string_list
from forge5.report import markdown
from forge5.security import scan
from forge5.testing import run_tests


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="forge5", description="FORGE-5 — AI Engineering Council")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--root", type=Path, default=Path.cwd(), help="Project root (default: cwd)")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser(
        "init", help="Initialize council state and a required test/security policy"
    )
    init.add_argument("--goal", required=True)
    sub.add_parser("status", help="Current state, ready tasks and release blockers")
    task = sub.add_parser("task", help="Manage the implementation DAG")
    ts = task.add_subparsers(dest="task_command", required=True)
    add = ts.add_parser("add")
    add.add_argument("id")
    add.add_argument("--title", required=True)
    add.add_argument("--owner", choices=list(Role), default="principal")
    add.add_argument("--depends", action="append", default=[])
    add.add_argument("--acceptance", required=True)
    update = ts.add_parser("set")
    update.add_argument("id")
    update.add_argument("status", choices=["pending", "running", "done"])
    update.add_argument("--evidence")
    review = sub.add_parser("review", help="Export role packets or import host-produced reviews")
    rs = review.add_subparsers(dest="review_command", required=True)
    export = rs.add_parser("packet")
    export.add_argument("role", choices=list(Role))
    ingest = rs.add_parser("submit")
    ingest.add_argument("file", type=Path, help="JSON review; '-' reads stdin")
    ingest.add_argument("--digest", required=True, help="Digest from the original review packet")
    ingest.add_argument("--context", required=True, help="Context digest from the original packet")
    ingest.add_argument("--revision", type=int, help="Reject concurrent council changes")
    ingest.add_argument("--mode", choices=["host", "sequential"], default="host")
    conflict = sub.add_parser("resolve", help="Close an objection with its author's disposition")
    conflict.add_argument("id")
    conflict.add_argument("--actor", choices=list(Role), required=True)
    conflict.add_argument(
        "--disposition", choices=["fixed", "rejected", "accepted_risk"], required=True
    )
    conflict.add_argument("--rationale", required=True)
    conflict.add_argument("--evidence", action="append", default=[])
    decision = sub.add_parser("decide", help="Record a tradeoff and invalidate approvals")
    decision.add_argument("--title", required=True)
    decision.add_argument("--option", action="append", required=True)
    decision.add_argument("--choice", required=True)
    decision.add_argument("--rationale", required=True)
    decision.add_argument("--owner", choices=list(Role), required=True)
    check = sub.add_parser("check", help="Execute configured commands and record bounded evidence")
    check.add_argument("--name", action="append")
    check.add_argument("--execute", action="store_true", help="Authorize trusted project commands")
    council = sub.add_parser("council", help="Run five external reviewers, optionally with repair")
    council.add_argument(
        "--adapter-json", required=True, help='Command argv JSON, e.g. ["my-agent"]'
    )
    council.add_argument("--repair-json", help="Optional trusted repair command argv JSON")
    council.add_argument("--rounds", type=int, default=1)
    council.add_argument("--workers", type=int, default=5)
    council.add_argument("--timeout", type=float, default=180)
    council.add_argument("--execute", action="store_true")
    council.add_argument("--reviews-only", action="store_true")
    report = sub.add_parser("report", help="Render a current Markdown or JSON evidence report")
    report.add_argument("--format", choices=["markdown", "json"], default="markdown")
    report.add_argument(
        "--output", type=Path, help="Prefer .forge5/report.md to avoid source edits"
    )
    sub.add_parser("release", help="Exit 0 only when every release condition passes")
    sub.add_parser("audit", help="Verify and export the hash-linked event log")
    scanner = sub.add_parser("scan", help="Run built-in Python AST security triage")
    scanner.add_argument("path", type=Path, nargs="?", default=Path("."))
    testing = sub.add_parser("test", help="Discover unittest cases; fail if empty or all skipped")
    testing.add_argument("--start", type=Path, default=Path("tests"))
    testing.add_argument("--pattern", default="test*.py")
    cancel = sub.add_parser(
        "round-cancel", help="Cancel an incomplete round; approvals stay revoked"
    )
    cancel.add_argument("--reason", required=True)
    return p


def read_json(path: Path) -> Any:
    if str(path) == "-":
        raw = sys.stdin.read(1048577)
    else:
        if not path.is_file():
            raise ForgeError("Review input must be a regular file or '-' for stdin")
        with path.open() as handle:
            raw = handle.read(1048577)
    if len(raw) > 1048576:
        raise ForgeError("Review input exceeds 1 MiB")
    return json.loads(raw)


def command_argv(raw: str) -> tuple[str, ...]:
    argv = string_list(json.loads(raw), "adapter argv")
    if not 1 <= len(argv) <= 100:
        raise ForgeError("Adapter argv must contain 1..100 arguments")
    return tuple(argv)


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    if args.command == "test":
        result = run_tests((args.root / args.start).resolve(), args.pattern)
        return result, int(not result["passed"])
    if args.command == "scan":
        findings = scan((args.root / args.path).resolve())
        return {"scanner": "forge5-ast-triage", "findings": findings}, int(bool(findings))
    if args.command == "init":
        council = Council.initialize(args.root, args.goal)
        return council.status(), 0
    council = Council(args.root)
    if args.command == "status":
        return council.status(), 0
    if args.command == "round-cancel":
        council.cancel_round(args.reason)
        return {"ok": True}, 0
    if args.command == "task":
        if args.task_command == "add":
            council.add_task(args.id, args.title, args.owner, args.depends, args.acceptance)
        else:
            council.task_status(args.id, args.status, args.evidence)
    elif args.command == "review":
        if args.review_command == "packet":
            return packet(council, Role(args.role)), 0
        council.review(
            Review.parse(read_json(args.file)),
            args.digest,
            args.context,
            mode=args.mode,
            expected_revision=args.revision,
        )
    elif args.command == "resolve":
        council.resolve(args.id, args.actor, args.disposition, args.rationale, args.evidence)
    elif args.command == "decide":
        council.decide(args.title, args.option, args.choice, args.rationale, args.owner)
    elif args.command in ("check", "council"):
        if not args.execute:
            raise ForgeError("Execution requires --execute; inspect trusted commands first")
        if args.command == "check":
            results = council.check(args.name)
            return results, int(any(not r["passed"] for r in results))
        from forge5.models import finite_number

        timeout = finite_number(args.timeout, "timeout", 0.05, 3600)
        reviewer = CommandReviewer(council, command_argv(args.adapter_json), timeout)
        if args.reviews_only:
            results = review_round(council, reviewer, workers=args.workers)
            return results, int(any(r["verdict"] != "approve" for r in results))
        repair = command_repair(command_argv(args.repair_json)) if args.repair_json else None
        result = improve(council, reviewer, repair, max_rounds=args.rounds, workers=args.workers)
        return result, int(result["outcome"] != "ready")
    elif args.command == "report":
        status = council.status()
        content = markdown(status) if args.format == "markdown" else json.dumps(status, indent=2)
        if args.output:
            args.output.write_text(content)
            return {"written": str(args.output.resolve())}, 0
        return content, 0
    elif args.command == "release":
        status = council.status()
        return status, int(not status["release_ready"])
    elif args.command == "audit":
        council.store.verify()
        return council.store.events(), 0
    return {"ok": True, "revision": council.store.read()[0]}, 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result, code = execute(args)
        print(
            result if isinstance(result, str) else json.dumps(result, indent=2, ensure_ascii=False)
        )
        return code
    except (ForgeError, OSError, sqlite3.Error, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
