"""Resumable goal-to-plan-to-change-set engineering workflow.

Providers are explicit executable integrations, not built-in or simulated LLMs.
"""

from __future__ import annotations

import difflib
import hashlib
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from forge5.agents import AgentRegistry
from forge5.config import load_checks
from forge5.council import packet
from forge5.engine import Council, context_digest
from forge5.models import ForgeError, Review, Role, identifier, keys, string_list, text
from forge5.store import State, canonical, digest, now
from forge5.workspace import excluded, fingerprint


@dataclass(frozen=True)
class ChangeSet:
    summary: str
    changes: tuple[dict[str, Any], ...]
    evidence: str

    @classmethod
    def parse(cls, response: Any, root: Path, allowed_paths: list[str]) -> ChangeSet:
        data = keys(response, {"summary", "changes", "evidence"})
        if not isinstance(data["changes"], list) or len(data["changes"]) > 32:
            raise ForgeError("A change set supports at most 32 files")
        seen: set[str] = set()
        total = 0
        changes = []
        for item in data["changes"]:
            item = keys(item, {"path", "before_sha256", "content"})
            path = text(item["path"], "change path", 500)
            parts = PurePosixPath(path).parts
            if (
                path not in allowed_paths
                or path in seen
                or path != PurePosixPath(path).as_posix()
                or not parts
                or "\\" in path
                or PurePosixPath(path).is_absolute()
                or any(p in (".", "..") or excluded(p) for p in parts)
                or path in ("forge5.toml", "pyproject.toml", "forge5-agents.json")
            ):
                raise ForgeError(f"Change outside task ownership or protected policy: {path}")
            target = root / path
            if not target.resolve().is_relative_to(root.resolve()) or any(
                (root.joinpath(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)
            ):
                raise ForgeError("Change path escapes workspace or traverses a symlink")
            before = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
            if item["before_sha256"] != before:
                raise ForgeError(f"Change preimage mismatch: {path}")
            content = item["content"]
            if content is not None and not isinstance(content, str):
                raise ForgeError("Change content must be text or null")
            if target.exists() and (path.startswith("tests/") or target.name.startswith("test_")):
                raise ForgeError(
                    "Implementation cannot replace or delete existing acceptance tests"
                )
            if content is None:
                raise ForgeError(
                    "Implementation deletion requires a separate human-reviewed change"
                )
            total += len(content.encode())
            seen.add(path)
            changes.append(dict(item))
        if total > 1024 * 1024:
            raise ForgeError("Change set exceeds 1 MiB")
        return cls(
            text(data["summary"], "summary"), tuple(changes), text(data["evidence"], "evidence")
        )

    def patch(self, root: Path) -> str:
        lines: list[str] = []
        for change in self.changes:
            path = root / change["path"]
            old = path.read_text().splitlines(keepends=True) if path.exists() else []
            new = change["content"].splitlines(keepends=True)
            lines.extend(
                difflib.unified_diff(
                    old, new, fromfile="a/" + change["path"], tofile="b/" + change["path"]
                )
            )
        return "".join(lines)

    def apply(self, root: Path) -> None:
        # Revalidate the whole batch before the first write; ordinary write failures roll back.
        ChangeSet.parse(
            asdict(self) | {"changes": list(self.changes)}, root, [c["path"] for c in self.changes]
        )
        backups: dict[Path, bytes | None] = {}
        try:
            for change in self.changes:
                target = root / change["path"]
                backups[target] = target.read_bytes() if target.exists() else None
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(change["content"])
        except OSError:
            for target, previous in backups.items():
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(previous)
            raise


def snapshot(root: Path, destination: Path) -> None:
    def ignore(parent: str, names: list[str]) -> list[str]:
        return [name for name in names if excluded(name)]

    shutil.copytree(root, destination, ignore=ignore)


class DevelopmentOrchestrator:
    def __init__(
        self, council: Council, registry: AgentRegistry, isolation: Any, *, execute: bool = False
    ) -> None:
        self.council, self.registry, self.isolation = council, registry, isolation
        self.execute = execute

    def _authorize(self) -> None:
        if not self.execute:
            raise ForgeError("Development providers require explicit execution authorization")

    def _policy_digest(self) -> str:
        return digest({"agents": self.registry.describe(), "isolation": asdict(self.isolation)})

    def _ensure_policy(self) -> None:
        current = self._policy_digest()
        development = self.council.store.read()[1].get("development", {})
        if development.get("policy_stale") or development.get("provider_policy_digest") != current:

            def invalidate(state: State) -> None:
                state["provider_policy_digest"] = current
                state["reviews"].clear()
                state["evidence"].clear()
                state.setdefault("development", {})["policy_stale"] = True

            self.council.store.mutate("development.policy_changed", {"digest": current}, invalidate)
            raise ForgeError("Provider or isolation configuration changed; create a new plan")

    def _checkpoint(self, phase: str, **updates: Any) -> None:
        def update(state: State) -> None:
            development = state.setdefault("development", {})
            development.update(phase=phase, updated_at=now(), **updates)

        self.council.store.mutate("development." + phase, updates, update)

    def _workspace(self) -> tempfile.TemporaryDirectory[str]:
        parent = self.council.root / ".forge5" / "runs"
        parent.mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(prefix="run-", dir=parent)

    def _invoke(self, role: str, request: dict[str, Any]) -> dict[str, Any]:
        before = fingerprint(self.council.root)
        with self._workspace() as directory:
            stage = Path(directory) / "workspace"
            snapshot(self.council.root, stage)
            stage_before = fingerprint(stage)
            response = self.registry.invoke(self.council, role, request, stage, self.isolation)
            if fingerprint(stage) != stage_before:
                raise ForgeError(
                    "Provider edited source directly; return declarative changes instead"
                )
        if before != fingerprint(self.council.root):
            raise ForgeError("Live workspace changed during provider invocation")
        return response

    def plan(self, goal: str) -> dict[str, Any]:
        self._authorize()
        goal = text(goal, "goal")
        before = fingerprint(self.council.root)
        revision, state = self.council.store.read()
        original_context = context_digest(state)
        self._checkpoint("planning", goal=goal)
        response = self._invoke(
            "architect",
            {
                "protocol": "forge5.plan.v1",
                "goal": goal,
                "existing_tasks": state["tasks"],
                "decisions": state["decisions"],
                "response_contract": {
                    "summary": "analysis and design",
                    "tasks": [
                        {
                            "id": "stable-id",
                            "title": "bounded task",
                            "dependencies": [],
                            "acceptance": "observable contract",
                            "allowed_paths": ["src/module.py"],
                            "check_names": ["tests"],
                        }
                    ],
                },
            },
        )
        data = keys(response, {"summary", "tasks"})
        summary = text(data["summary"], "plan summary")
        if not isinstance(data["tasks"], list) or not 1 <= len(data["tasks"]) <= 200:
            raise ForgeError("Plan must contain 1..200 tasks")
        tasks: dict[str, dict[str, Any]] = {}
        scopes: dict[str, list[str]] = {}
        task_checks: dict[str, list[str]] = {}
        known_checks = {check.name for check in load_checks(self.council.root)}
        previous_tasks = set(state.get("development", {}).get("task_order", []))
        for raw in data["tasks"]:
            task = keys(
                raw, {"id", "title", "dependencies", "acceptance", "allowed_paths"}, {"check_names"}
            )
            tid = identifier(task["id"])
            if tid in tasks or (tid in state["tasks"] and tid not in previous_tasks):
                raise ForgeError("Planned task IDs must be unique and owned by this workflow")
            deps = string_list(task["dependencies"], "dependencies")
            allowed = string_list(task["allowed_paths"], "allowed_paths")
            if not allowed or len(allowed) > 100 or len(set(deps)) != len(deps):
                raise ForgeError("Task needs bounded file ownership and unique dependencies")
            # Validate path syntax independently of whether a file exists yet.
            for path in allowed:
                parts = PurePosixPath(path).parts
                if (
                    not parts
                    or path != PurePosixPath(path).as_posix()
                    or path in ("forge5.toml", "pyproject.toml", "forge5-agents.json")
                    or PurePosixPath(path).is_absolute()
                    or "\\" in path
                    or any(part in (".", "..") or excluded(part) for part in parts)
                ):
                    raise ForgeError("Invalid planned file scope")
            tasks[tid] = {
                "id": tid,
                "title": text(task["title"], "title"),
                "owner": "principal",
                "dependencies": deps,
                "acceptance": text(task["acceptance"], "acceptance"),
                "status": "pending",
                "completion_evidence": None,
            }
            scopes[tid] = allowed
            selected = string_list(task.get("check_names", sorted(known_checks)), "check_names")
            if not selected or set(selected) - known_checks:
                raise ForgeError("Task check_names must select existing configured checks")
            task_checks[tid] = selected
        ordered = []
        remaining = dict(tasks)
        removed_tasks = previous_tasks - set(tasks)
        external_tasks = set(state["tasks"]) - previous_tasks
        if any(set(state["tasks"][tid]["dependencies"]) & removed_tasks for tid in external_tasks):
            raise ForgeError("Cannot remove workflow tasks referenced by external tasks")
        affected = previous_tasks & set(tasks)
        while True:
            dependents = {
                tid for tid in external_tasks if set(state["tasks"][tid]["dependencies"]) & affected
            } - affected
            if not dependents:
                break
            if any(state["tasks"][tid]["status"] != "pending" for tid in dependents):
                raise ForgeError("Reset active external dependent tasks before replanning")
            affected.update(dependents)
        remaining.update({tid: state["tasks"][tid] for tid in external_tasks})
        known: set[str] = set()
        while remaining:
            ready = [tid for tid, task in remaining.items() if set(task["dependencies"]) <= known]
            if not ready:
                raise ForgeError("Planned task dependencies contain a cycle or unknown task")
            for tid in ready:
                if tid in tasks:
                    ordered.append(tid)
                known.add(tid)
                del remaining[tid]
        if fingerprint(self.council.root) != before:
            raise ForgeError("Source changed during planning")

        def apply(state: State) -> None:
            if context_digest(state) != original_context:
                raise ForgeError("Council plan context changed during planning")
            state["provider_policy_digest"] = self._policy_digest()
            state["evidence"].clear()
            state["goal"] = goal
            for tid in removed_tasks:
                del state["tasks"][tid]
            state["tasks"].update(tasks)
            state["reviews"].clear()
            state["development"] = {
                "phase": "planned",
                "goal": goal,
                "summary": summary,
                "task_order": ordered,
                "scopes": scopes,
                "task_checks": task_checks,
                "provider_policy_digest": self._policy_digest(),
                "rounds": [],
                "providers": self.registry.describe(),
                "updated_at": now(),
            }

        self.council.store.mutate(
            "development.plan",
            {"summary": summary, "tasks": tasks, "scopes": scopes, "baseline_revision": revision},
            apply,
        )
        return dict(self.council.store.read()[1]["development"])

    def _checks(self, council: Council, names: list[str] | None = None) -> list[dict[str, Any]]:
        from forge5.tools import ToolBroker

        broker = ToolBroker(council, "orchestrator", isolation=self.isolation)
        results = []
        for check in load_checks(council.root):
            if names is None or check.name in names:
                results.append(dict(broker.call("run_check", name=check.name)))
        from forge5.architecture import check_architecture
        from forge5.memory import get_contracts

        contracts = get_contracts(council)
        source = fingerprint(council.root)
        architecture = {
            "name": "architecture",
            **check_architecture(council.root, contracts),
            "source_digest": source,
            "contracts_digest": digest(contracts),
            "timestamp": now(),
        }
        council.store.mutate(
            "architecture.check",
            architecture,
            lambda state: state.update(architecture_evidence=architecture),
        )
        results.append(architecture)
        return results

    def _implement(
        self, task: dict[str, Any], allowed: list[str], findings: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        before = fingerprint(self.council.root)
        _, state = self.council.store.read()
        plan = context_digest(state)
        response = self._invoke(
            "implementation",
            {
                "protocol": "forge5.implement.v1",
                "goal": state["goal"],
                "task": task,
                "allowed_paths": allowed,
                "source_digest": before,
                "open_findings": findings or {},
                "previous_failure": state.get("development", {}).get("last_failure"),
                "response_contract": {
                    "summary": "implementation summary",
                    "changes": [
                        {
                            "path": "owned/file.py",
                            "before_sha256": "hash or null for new file",
                            "content": "complete file content",
                        }
                    ],
                    "evidence": "what was actually checked",
                },
            },
        )
        changes = ChangeSet.parse(response, self.council.root, allowed)
        patch = changes.patch(self.council.root)
        artifact_id = digest({"source": before, "task": task["id"], "changes": changes.changes})
        artifact = self.council.root / ".forge5" / "changes"
        artifact.mkdir(exist_ok=True)
        (artifact / f"{artifact_id}.patch").write_text(patch)
        (artifact / f"{artifact_id}.json").write_text(canonical(asdict(changes)))
        self.council.store.mutate(
            "implementation.proposed",
            {
                "task": task["id"],
                "change_set": artifact_id,
                "paths": [c["path"] for c in changes.changes],
                "source_digest": before,
            },
            lambda state: None,
        )
        with self._workspace() as directory:
            stage = Path(directory) / "workspace"
            snapshot(self.council.root, stage)
            staged = Council.initialize(stage, state["goal"])
            from forge5.tools import ToolBroker

            if changes.changes:
                ToolBroker(
                    staged, "orchestrator", allowed_paths=tuple(allowed), isolation=self.isolation
                ).call("apply_changes", changes=list(changes.changes))
            from forge5.memory import get_contracts, set_contracts

            set_contracts(staged, get_contracts(self.council))
            selected = state.get("development", {}).get("task_checks", {}).get(task["id"])
            checks = self._checks(staged, selected)
            required = {c.name for c in load_checks(stage) if c.required} | {"architecture"}
            if any(not check["passed"] for check in checks if check["name"] in required):
                failure = {
                    "task": task["id"],
                    "change_set": artifact_id,
                    "checks": checks,
                    "reason": "Staged required checks failed",
                }
                self._checkpoint("implementation_failed", last_failure=failure)
                raise ForgeError("Staged required checks failed; change set was not applied")
        _, current = self.council.store.read()
        if fingerprint(self.council.root) != before or context_digest(current) != plan:
            raise ForgeError("Workspace or task context changed before applying change set")
        if changes.changes:
            from forge5.tools import ToolBroker

            ToolBroker(
                self.council, "orchestrator", allowed_paths=tuple(allowed), isolation=self.isolation
            ).call("apply_changes", changes=list(changes.changes))
        self.council.store.mutate(
            "implementation.applied",
            {
                "task": task["id"],
                "change_set": artifact_id,
                "source_digest": fingerprint(self.council.root),
            },
            lambda state: state["reviews"].clear(),
        )
        return {
            "summary": changes.summary,
            "evidence": changes.evidence,
            "change_set": artifact_id,
            "paths": [c["path"] for c in changes.changes],
        }

    def review(self) -> list[dict[str, Any]]:
        self._authorize()
        self._ensure_policy()
        round_id = self.council.begin_round()
        start_revision, _ = self.council.store.read()
        source = fingerprint(self.council.root)
        contexts = {role: packet(self.council, role) for role in Role}
        reviews = []
        for role in Role:
            try:
                review = Review.parse(self._invoke(role.value, contexts[role]))
                if review.role != role:
                    raise ForgeError("Provider returned a different role")
            except Exception as exc:
                review = Review(role, "abstain", f"Provider failed: {exc}", ())
            reviews.append(review)
        # Tool/provider audit events may advance revisions, but unrelated mutations abort.
        events = self.council.store.events()
        if any(
            event["action"] not in ("agent.request", "agent.result", "tool.operation")
            for event in events
            if event["sequence"] > start_revision
        ):
            raise ForgeError("Concurrent council mutation interrupted review round")
        revision, _ = self.council.store.read()
        self.council.complete_round(
            round_id,
            reviews,
            source,
            {role: contexts[role]["context_digest"] for role in Role},
            revision,
            "adapter",
        )
        return [asdict(review) for review in reviews]

    def _dispositions(self) -> None:
        state = self.council.store.read()[1]
        for fid, finding in state["findings"].items():
            if finding["status"] != "open":
                continue
            author = finding["author"]
            response = self._invoke(
                author,
                {
                    "protocol": "forge5.disposition.v1",
                    "finding": finding,
                    "checks": state["evidence"],
                    "response_contract": {
                        "finding_id": fid,
                        "disposition": "fixed/rejected/accepted_risk/needs_evidence",
                        "rationale": "evidence-based assessment",
                        "evidence_names": ["tests"],
                    },
                },
            )
            data = keys(response, {"finding_id", "disposition", "rationale", "evidence_names"})
            if data["finding_id"] != fid:
                raise ForgeError("Disposition references a different finding")
            evidence = string_list(data["evidence_names"], "evidence_names")
            disposition = data["disposition"]
            if disposition == "needs_evidence":
                continue
            self.council.conflict(
                fid, author, disposition, text(data["rationale"], "rationale"), evidence
            )

    def build(self, *, max_rounds: int = 3) -> dict[str, Any]:
        self._authorize()
        if not 1 <= max_rounds <= 20:
            raise ForgeError("Build rounds must be between 1 and 20")
        state = self.council.store.read()[1]
        development = state.get("development")
        if not development or "task_order" not in development:
            raise ForgeError("Run forge5 plan before build")
        self._ensure_policy()
        completed_rounds = list(development.get("rounds", []))
        for index in range(max_rounds):
            try:
                self._checkpoint("implementing")
                for tid in development["task_order"]:
                    state = self.council.store.read()[1]
                    task = state["tasks"][tid]
                    if task["status"] == "done":
                        continue
                    self.council.task_status(tid, "running")
                    result = self._implement(task, development["scopes"][tid])
                    self.council.task_status(tid, "done", canonical(result))
                checks = self._checks(self.council)
                self._checkpoint("reviewing")
                reviews = self.review()
                status = self.council.status()
                completed_rounds.append(
                    {
                        "round": len(completed_rounds) + 1,
                        "source_digest": status["source_digest"],
                        "checks": [{"name": c["name"], "passed": c["passed"]} for c in checks],
                        "reviews": reviews,
                        "blockers": status["blockers"],
                    }
                )
                self._checkpoint(
                    "ready" if status["release_ready"] else "blocked", rounds=completed_rounds
                )
                if status["release_ready"]:
                    return self.report()
                findings = {
                    k: v for k, v in status["state"]["findings"].items() if v["status"] == "open"
                }
                if findings:
                    allowed = sorted({p for paths in development["scopes"].values() for p in paths})
                    remediation = {
                        "id": "remediation",
                        "title": "Repair evidenced council objections",
                        "acceptance": "Current required checks and author dispositions pass",
                    }
                    self._checkpoint("repairing")
                    result = self._implement(remediation, allowed, findings)
                    for fid in findings:
                        self.council.conflict(fid, "implementation", "respond", canonical(result))
                    self._checks(self.council)
                    self._dispositions()
            except Exception as exc:
                self._checkpoint(
                    "failed", last_error=f"{type(exc).__name__}: {exc}", rounds=completed_rounds
                )
                if index + 1 == max_rounds:
                    return self.report()
        return self.report()

    def report(self) -> dict[str, Any]:
        status = self.council.status()
        return {
            "protocol": "forge5.development-report.v1",
            "outcome": "ready"
            if status["release_ready"]
            and status["state"].get("development", {}).get("phase") == "ready"
            else "blocked",
            "status": status,
            "development": status["state"].get("development", {}),
            "agents": self.registry.describe(),
            "limits": [
                "Provider identity and model independence are declarations; not authenticated",
                "Declarative live-root changes are bounded; local providers remain trusted",
                "Isolation availability and actual guarantees are recorded per execution",
                "Protocol tests do not establish live LLM quality or production capability",
            ],
        }
