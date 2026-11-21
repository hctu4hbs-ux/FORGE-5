"""Explicit safe built-in handlers for a useful dependency-free CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .model import Workflow
from .store import Store
from .worker import Worker


def echo(payload, dependencies):
    return payload


def add(payload, dependencies):
    values = list(payload.get("values", [])) + list(dependencies.values())
    if any(type(value) not in (int, float) for value in values):
        raise ValueError("add expects numeric values and numeric dependency results")
    return sum(values)


def collect(payload, dependencies):
    return {"payload": payload, "dependencies": dependencies}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Durable SQLite DAG workflows")
    parser.add_argument("--db", default="durableflow.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    submit = commands.add_parser("submit", help="submit a JSON DAG idempotently")
    submit.add_argument("file", type=Path)
    submit.add_argument("--key", required=True)
    run = commands.add_parser("run", help="drain queued jobs with safe built-in handlers")
    run.add_argument("--workflow")
    run.add_argument("--concurrency", type=int, default=4)
    run.add_argument("--lease-seconds", type=float, default=30)
    status = commands.add_parser("status", help="read persisted workflow state")
    status.add_argument("workflow")
    commands.add_parser("recover", help="recover expired leases and block failed dependents")
    args = parser.parse_args(argv)
    try:
        store = Store(args.db)
        if args.command == "submit":
            workflow = Workflow.from_dict(json.loads(args.file.read_text(encoding="utf-8")))
            print(json.dumps({"workflow_id": store.submit(workflow, key=args.key)}))
        elif args.command == "run":
            counts = Worker(store, {"echo": echo, "add": add, "collect": collect},
                            concurrency=args.concurrency, lease_seconds=args.lease_seconds).run(args.workflow)
            print(json.dumps(counts))
            if args.workflow and store.snapshot(args.workflow)["state"] == "failed":
                return 1
        elif args.command == "status":
            print(json.dumps(store.snapshot(args.workflow), sort_keys=True))
        else:
            store.recover()
            print(json.dumps({"recovered": True}))
    except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
        print(f"durableflow: {exc}", file=sys.stderr)
        return 2
    return 0
