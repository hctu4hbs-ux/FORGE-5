"""Audited, role-scoped workspace tools for trusted agent hosts.

This broker validates its own operations. An external local process is not
constrained by this Python permission layer; use container execution for isolation.
"""

from __future__ import annotations

import difflib
import hashlib
import os
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from forge5.config import load_checks
from forge5.engine import Council
from forge5.isolation import IsolationPolicy, execute
from forge5.models import ForgeError, keys, text
from forge5.store import State, now
from forge5.workspace import excluded, fingerprint

READ = frozenset({"read_file", "search", "tree", "inspect_changes", "tasks", "findings"})
PERMISSIONS = {
    "architect": READ,
    "principal": READ | {"run_check"},
    "debugger": READ | {"run_check", "conflict"},
    "security": READ | {"run_check", "conflict"},
    "product": READ | {"run_check", "conflict"},
    "implementation": READ | {"apply_changes", "run_check", "conflict"},
    "orchestrator": READ | {"apply_changes", "run_check", "task_status", "conflict"},
}
CHECK_KINDS = {
    "principal": {"test", "lint", "type"},
    "debugger": {"test"},
    "security": {"security", "performance"},
    "product": {"test", "custom"},
    "implementation": {"test", "lint", "type"},
    "orchestrator": {"test", "lint", "type", "security", "performance", "custom"},
}


class ToolBroker:
    def __init__(
        self,
        council: Council,
        actor: str,
        *,
        allowed_paths: tuple[str, ...] = (),
        isolation: IsolationPolicy | None = None,
    ) -> None:
        if actor not in PERMISSIONS:
            raise ForgeError(f"Unknown tool actor: {actor}")
        self.council, self.actor = council, actor
        self.root = council.root
        self.allowed_paths = frozenset(allowed_paths)
        self.isolation = isolation or IsolationPolicy()
        for path in self.allowed_paths:
            self._path(path)

    def _audit(self, tool: str, status: str, arguments: dict[str, Any], detail: str) -> None:
        # Do not persist source contents or potential secret search results in audit payloads.
        payload = {
            "actor": self.actor,
            "tool": tool,
            "status": status,
            "argument_names": sorted(arguments),
            "detail": detail[:1000],
            "timestamp": now(),
        }
        self.council.store.mutate("tool.operation", payload, lambda state: None)

    def _path(self, raw: str) -> Path:
        raw = text(raw, "workspace path", 1000)
        relative = PurePosixPath(raw)
        if relative.is_absolute() or any(p in ("..", ".git", ".forge5") for p in relative.parts):
            raise ForgeError("Path is outside the permitted source workspace")
        if "\\" in raw or ":" in raw or raw in (".", ""):
            raise ForgeError("Use a nonempty relative POSIX source path")
        current = self.root
        for part in relative.parts:
            if excluded(part):
                raise ForgeError(
                    f"Generated/internal path is not an agent source tool target: {raw}"
                )
            current /= part
            if current.is_symlink():
                raise ForgeError(f"Source symlinks are not permitted: {raw}")
            if current.exists() and not (current.is_dir() or current.is_file()):
                raise ForgeError(f"Special filesystem entries are not permitted: {raw}")
        if not current.resolve().is_relative_to(self.root):
            raise ForgeError("Path escaped source workspace")
        return current

    def _files(self) -> list[str]:
        files = []
        for parent, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not excluded(d))
            for name in dirs + sorted(names):
                if excluded(name):
                    continue
                path = Path(parent) / name
                relative = path.relative_to(self.root).as_posix()
                self._path(relative)
                if path.is_file():
                    files.append(relative)
                    if len(files) > 10000:
                        raise ForgeError("Tool tree exceeds 10000 files; scope the project")
        return sorted(files)

    def _read(self, path: str) -> str:
        target = self._path(path)
        if not target.is_file() or target.stat().st_size > 1048576:
            raise ForgeError("Tool input must be a regular source file of at most 1 MiB")
        with target.open(encoding="utf-8", newline="") as handle:
            content = handle.read(1048577)
        if len(content.encode()) > 1048576:
            raise ForgeError("Tool source file exceeds 1 MiB")
        return content

    def call(self, tool: str, **arguments: Any) -> Any:
        self._audit(tool, "started", arguments, "Dispatch requested")
        try:
            if tool not in PERMISSIONS[self.actor]:
                raise ForgeError(f"Role {self.actor} is not permitted to use {tool}")
            result = self._dispatch(tool, arguments)
        except Exception as exc:
            self._audit(tool, "denied_or_failed", arguments, str(exc))
            raise
        self._audit(tool, "success", arguments, "Completed")
        return result

    def _dispatch(self, tool: str, args: dict[str, Any]) -> Any:
        if tool == "read_file":
            keys(args, {"path"})
            return {"path": args["path"], "content": self._read(args["path"])}
        if tool == "tree":
            keys(args, set())
            return self._files()
        if tool == "search":
            keys(args, {"query"})
            query = text(args["query"], "query", 1000)
            results = []
            for path in self._files():
                try:
                    content = self._read(path)
                except UnicodeError:
                    continue
                for line, value in enumerate(content.splitlines(), 1):
                    if query in value:
                        results.append({"path": path, "line": line, "text": value[:500]})
                        if len(results) == 100:
                            return {"matches": results, "truncated": True}
            return {"matches": results, "truncated": False}
        if tool in ("tasks", "findings"):
            keys(args, set())
            return self.council.store.read()[1][tool]
        if tool in ("inspect_changes", "apply_changes"):
            keys(args, {"changes"})
            return self._changes(args["changes"], apply=tool == "apply_changes")
        if tool == "run_check":
            keys(args, {"name"})
            return self._check(args["name"])
        if tool == "task_status":
            keys(args, {"id", "status"}, {"evidence"})
            self.council.task_status(args["id"], args["status"], args.get("evidence"))
            return {"ok": True}
        if tool == "conflict":
            keys(args, {"id", "action", "message"}, {"evidence_names"})
            if self.actor == "implementation" and args["action"] in (
                "fixed",
                "rejected",
                "accepted_risk",
            ):
                raise ForgeError("Implementation agent cannot close objections")
            self.council.conflict(
                args["id"], self.actor, args["action"], args["message"], args.get("evidence_names")
            )
            return {"ok": True}
        raise ForgeError("Unknown tool")

    def _changes(self, changes: Any, *, apply: bool) -> dict[str, Any]:
        if not isinstance(changes, list) or not 1 <= len(changes) <= 32:
            raise ForgeError("Change set must contain 1..32 files")
        prepared: list[tuple[Path, str | None, str | None, int]] = []
        paths: set[str] = set()
        patch: list[str] = []
        total = 0
        for change in changes:
            keys(change, {"path", "before_sha256", "content"})
            raw = change["path"]
            target = self._path(raw)
            normalized = target.relative_to(self.root).as_posix()
            if normalized in paths:
                raise ForgeError("Duplicate file in change set")
            paths.add(normalized)
            if apply and normalized not in self.allowed_paths:
                raise ForgeError(f"Change is outside the exact task scope: {normalized}")
            if target.exists() and (
                normalized in ("forge5.toml", "pyproject.toml", "forge5-agents.json")
                or "tests" in PurePosixPath(normalized).parts
                or normalized.startswith(".github/workflows/")
                or target.name.startswith("test_")
            ):
                raise ForgeError("Existing policy and regression tests are protected")
            original = self._read(normalized) if target.exists() else None
            expected = (
                hashlib.sha256(original.encode()).hexdigest() if original is not None else None
            )
            if change["before_sha256"] != expected:
                raise ForgeError(f"Stale change set base: {normalized}")
            content = change["content"]
            if content is not None and not isinstance(content, str):
                raise ForgeError("Changed content must be UTF-8 text or null for deletion")
            if content is None and original is None:
                raise ForgeError("Cannot delete a nonexistent file")
            total += len(content.encode()) if content is not None else 0
            if total > 1048576:
                raise ForgeError("Change set exceeds 1 MiB")
            patch.extend(
                difflib.unified_diff(
                    (original or "").splitlines(keepends=True),
                    (content or "").splitlines(keepends=True),
                    fromfile="a/" + normalized,
                    tofile="b/" + normalized,
                )
            )
            prepared.append(
                (target, original, content, target.stat().st_mode if target.exists() else 0o644)
            )
        if any(
            str(parent) in paths
            for path in paths
            for parent in PurePosixPath(path).parents
            if str(parent) != "."
        ):
            raise ForgeError("Change paths cannot contain both a file and its descendant")
        if apply:
            changed = []
            try:
                for target, original, content, mode in prepared:
                    changed.append((target, original, mode))
                    if content is None:
                        target.unlink()
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(content, encoding="utf-8")
                        target.chmod(mode)
            except BaseException:
                for target, original, mode in reversed(changed):
                    if original is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.write_text(original, encoding="utf-8")
                        target.chmod(mode)
                raise
        return {"paths": sorted(paths), "patch": "".join(patch), "applied": apply}

    def _check(self, name: str) -> dict[str, Any]:
        check = next((c for c in load_checks(self.root) if c.name == name), None)
        if check is None or check.kind not in CHECK_KINDS.get(self.actor, set()):
            raise ForgeError(f"Role cannot run configured check: {name}")

        def register(state: State) -> None:
            previous = state.get("execution_policy_digest")
            if previous != self.isolation.digest:
                state["execution_policy_digest"] = self.isolation.digest
                state["evidence"].clear()
                state["reviews"].clear()

        self.council.store.mutate("execution.policy", {"digest": self.isolation.digest}, register)
        source = fingerprint(self.root)
        effective = replace(self.isolation, timeout=min(self.isolation.timeout, check.timeout))
        execution = execute(check.argv, self.root, effective)
        result = execution.process
        changed = fingerprint(self.root) != source
        evidence = {
            **result.to_dict(),
            "name": name,
            "kind": check.kind,
            "passed": result.exit_code == 0
            and not result.timed_out
            and not result.truncated
            and not changed
            and (check.max_seconds is None or result.seconds <= check.max_seconds),
            "source_digest": source,
            "source_changed": changed,
            "check_digest": check.digest,
            "execution_policy_digest": self.isolation.digest,
            "isolation": execution.isolation,
            "timestamp": now(),
        }
        self.council.store.mutate(
            "check.record", evidence, lambda state: state["evidence"].update({name: evidence})
        )
        return evidence
