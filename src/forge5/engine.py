"""Council state machine, conflict resolution and evidence-based release policy."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from forge5.config import DEFAULT_CONFIG, load_checks
from forge5.models import ForgeError, Review, Role, identifier, string_list, text
from forge5.runner import run_process
from forge5.store import State, Store, digest, now
from forge5.workspace import fingerprint


def context_digest(state: State, role: Role | None = None) -> str:
    """Bind approvals to acceptance contracts, not just the filesystem."""
    plan = digest({key: state[key] for key in ("goal", "tasks", "decisions")})
    if role is None:
        return plan
    authored = {
        key: value for key, value in state["findings"].items() if value["author"] == role.value
    }
    return digest({"plan": plan, "authored_findings": authored}) if authored else plan


def apply_review(state: State, review: Review, source: str, plan: str, mode: str) -> None:
    role = review.role.value
    state["reviews"][role] = {
        **asdict(review),
        "source_digest": source,
        "context_digest": plan,
        "mode": mode,
        "timestamp": now(),
    }
    for finding in review.findings:
        fid = f"{role}.{finding.id}"
        previous = state["findings"].get(fid)
        state["findings"][fid] = {
            **asdict(finding),
            "id": fid,
            "author": role,
            "source_digest": source,
            "status": "open",
            "resolution": None,
            "history": previous.get("history", []) if previous else [],
        }


class Council:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.store = Store(self.root)

    @classmethod
    def initialize(cls, root: Path, goal: str) -> Council:
        root = root.resolve()
        if not root.is_dir():
            raise ForgeError("Project root must exist")
        goal = text(goal, "goal")
        store = Store(root, create=True)
        policy = root / "forge5.toml"
        if not policy.exists():
            with policy.open("x") as handle:
                handle.write(DEFAULT_CONFIG)
        load_checks(root)
        store.initialize(goal)
        return cls(root)

    def add_task(
        self, task_id: str, title: str, owner: str, dependencies: list[str], acceptance: str
    ) -> None:
        task_id = identifier(task_id)
        title, acceptance = text(title, "title"), text(acceptance, "acceptance")
        try:
            role = Role(owner)
        except ValueError as exc:
            raise ForgeError("Invalid task owner") from exc
        dependencies = string_list(dependencies, "dependencies")
        if len(set(dependencies)) != len(dependencies):
            raise ForgeError("Duplicate task dependency")

        def update(state: State) -> None:
            if task_id in state["tasks"]:
                raise ForgeError(f"Task already exists: {task_id}")
            if any(d not in state["tasks"] for d in dependencies):
                raise ForgeError("Dependencies must reference existing tasks")
            state["tasks"][task_id] = {
                "id": task_id,
                "title": title,
                "owner": role.value,
                "dependencies": dependencies,
                "acceptance": acceptance,
                "status": "pending",
                "completion_evidence": None,
            }
            state["reviews"].clear()

        self.store.mutate("task.add", {"id": task_id, "title": title}, update)

    def task_status(self, task_id: str, status: str, evidence: str | None = None) -> None:
        if status not in ("pending", "running", "done"):
            raise ForgeError("Task status must be pending, running or done")
        if status == "done":
            evidence = text(evidence, "completion evidence")

        def update(state: State) -> None:
            tasks = state["tasks"]
            if task_id not in tasks:
                raise ForgeError(f"Unknown task: {task_id}")
            task = tasks[task_id]
            if status in ("running", "done") and any(
                tasks[d]["status"] != "done" for d in task["dependencies"]
            ):
                raise ForgeError("Complete task dependencies first")
            if status != "done" and any(
                task_id in t["dependencies"] and t["status"] != "pending" for t in tasks.values()
            ):
                raise ForgeError("Reset dependent tasks before reopening this task")
            task.update(status=status, completion_evidence=evidence if status == "done" else None)
            # Planning/completion changes require a new council review even without source edits.
            state["reviews"].clear()

        self.store.mutate(
            "task.status", {"id": task_id, "status": status, "evidence": evidence}, update
        )

    def review(
        self,
        review: Review,
        source_digest: str,
        plan_digest: str,
        *,
        mode: str = "host",
        expected_revision: int | None = None,
    ) -> None:
        review = Review.parse(asdict(review))
        if source_digest != fingerprint(self.root):
            raise ForgeError("Review is stale: source digest no longer matches")
        if mode not in ("host", "adapter", "sequential"):
            raise ForgeError("Review mode must be host, adapter or sequential")

        def update(state: State) -> None:
            if state.get("active_round"):
                raise ForgeError("A review round is active; complete or retry that round first")
            if plan_digest != context_digest(state, review.role):
                raise ForgeError(
                    "Review is stale: council goals, tasks or decisions, "
                    "or authored finding history changed"
                )
            # Old objections remain open even when a reviewer silently drops them.
            apply_review(state, review, source_digest, plan_digest, mode)

        self.store.mutate(
            "review.submit",
            {
                "role": review.role,
                "digest": source_digest,
                "context_digest": plan_digest,
                "review": asdict(review),
                "mode": mode,
            },
            update,
            expected_revision,
        )

    def begin_round(self) -> str:
        """Invalidate old approvals before dispatch; crashes stay fail-closed."""
        round_id = uuid.uuid4().hex

        def update(state: State) -> None:
            if state["reviews"]:
                state["prior_reviews"] = state["reviews"]
            state["reviews"] = {}
            state["active_round"] = round_id

        self.store.mutate("round.begin", {"id": round_id}, update)
        return round_id

    def complete_round(
        self,
        round_id: str,
        reviews: list[Review],
        source: str,
        plans: dict[Role, str],
        revision: int,
        mode: str,
    ) -> None:
        reviews = [Review.parse(asdict(review)) for review in reviews]
        if len(reviews) != 5 or {r.role for r in reviews} != set(Role):
            raise ForgeError("A round must contain exactly one review for each expert")
        if mode not in ("host", "adapter", "sequential"):
            raise ForgeError("Invalid review mode")
        if source != fingerprint(self.root):
            raise ForgeError("Source changed during read-only review; discard round and retry")

        def update(state: State) -> None:
            if state.get("active_round") != round_id or any(
                plans.get(review.role) != context_digest(state, review.role) for review in reviews
            ):
                raise ForgeError("Review round context is stale; retry the round")
            for review in reviews:
                apply_review(state, review, source, plans[review.role], mode)
            state["active_round"] = None

        self.store.mutate(
            "round.complete",
            {
                "id": round_id,
                "reviews": [asdict(r) for r in reviews],
                "source_digest": source,
                "context_digests": plans,
                "mode": mode,
            },
            update,
            revision,
        )

    def cancel_round(self, reason: str) -> None:
        reason = text(reason, "reason")

        def update(state: State) -> None:
            if not state.get("active_round"):
                raise ForgeError("No active round to cancel")
            state["active_round"] = None
            state["reviews"] = {}

        self.store.mutate("round.cancel", {"reason": reason}, update)

    def resolve(
        self,
        finding_id: str,
        actor: str,
        disposition: str,
        rationale: str,
        evidence_names: list[str],
    ) -> None:
        try:
            role = Role(actor)
        except ValueError as exc:
            raise ForgeError("Invalid resolving role") from exc
        if disposition not in ("fixed", "rejected", "accepted_risk"):
            raise ForgeError("Disposition must be fixed, rejected or accepted_risk")
        rationale = text(rationale, "rationale")
        evidence_names = string_list(evidence_names, "evidence_names")
        current = fingerprint(self.root)
        checks = {c.name: c for c in load_checks(self.root)}

        def update(state: State) -> None:
            finding = state["findings"].get(finding_id)
            if finding is None or finding["status"] != "open":
                raise ForgeError("Finding must exist and be open")
            if role.value != finding["author"]:
                raise ForgeError("Only the objecting expert can close their finding")
            if disposition == "accepted_risk" and finding["severity"] in ("critical", "high"):
                raise ForgeError("High/critical findings cannot be waived for release")
            if disposition == "fixed" and not evidence_names:
                raise ForgeError("A fixed finding needs current passing check evidence")
            for name in evidence_names:
                evidence = state["evidence"].get(name)
                check = checks.get(name)
                if (
                    not evidence
                    or not evidence["passed"]
                    or evidence["source_digest"] != current
                    or check is None
                    or evidence["check_digest"] != check.digest
                ):
                    raise ForgeError(f"Missing, failing or stale evidence: {name}")
            resolution = {
                "actor": role.value,
                "disposition": disposition,
                "rationale": rationale,
                "evidence_names": evidence_names,
                "source_digest": current,
                "timestamp": now(),
            }
            finding.update(status="closed", resolution=resolution)
            finding["history"].append(resolution)
            state["reviews"].pop(role.value, None)

        self.store.mutate(
            "finding.resolve",
            {
                "id": finding_id,
                "actor": actor,
                "disposition": disposition,
                "rationale": rationale,
                "evidence": evidence_names,
            },
            update,
        )

    def decide(
        self, title: str, options: list[str], choice: str, rationale: str, owner: str
    ) -> None:
        title, choice, rationale = (
            text(title, "title"),
            text(choice, "choice"),
            text(rationale, "rationale"),
        )
        options = string_list(options, "options")
        if len(options) < 2 or choice not in options:
            raise ForgeError("Decision needs two options and a choice from those options")
        try:
            role = Role(owner)
        except ValueError as exc:
            raise ForgeError("Invalid decision owner") from exc

        def update(state: State) -> None:
            state["decisions"].append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "title": title,
                    "options": options,
                    "choice": choice,
                    "rationale": rationale,
                    "owner": role.value,
                    "timestamp": now(),
                }
            )
            state["reviews"].clear()

        self.store.mutate("decision.record", {"title": title, "choice": choice}, update)

    def check(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        checks = load_checks(self.root)
        if names and set(names) - {c.name for c in checks}:
            raise ForgeError("Unknown check name")
        results = []
        for check in checks:
            if names and check.name not in names:
                continue
            before = fingerprint(self.root)
            result = run_process(check.argv, self.root, check.timeout)
            after = fingerprint(self.root)
            passed = (
                result.exit_code == 0
                and not result.timed_out
                and not result.truncated
                and before == after
                and (check.max_seconds is None or result.seconds <= check.max_seconds)
            )
            evidence = {
                **result.to_dict(),
                "name": check.name,
                "kind": check.kind,
                "passed": passed,
                "source_digest": before,
                "check_digest": check.digest,
                "source_changed": before != after,
                "timestamp": now(),
            }

            def record(state: State, e: dict[str, Any] = evidence, name: str = check.name) -> None:
                state["evidence"].update({name: e})

            self.store.mutate("check.record", evidence, record)
            results.append(evidence)
        return results

    def status(self) -> dict[str, Any]:
        self.store.verify()
        revision, state = self.store.read()
        current = fingerprint(self.root)
        current_context = context_digest(state)
        checks = load_checks(self.root)
        blockers = []
        if state.get("active_round"):
            blockers.append("An incomplete review round must be retried or cancelled")
        if not state["tasks"]:
            blockers.append("No implementation tasks recorded")
        for task in state["tasks"].values():
            if task["status"] != "done":
                blockers.append(f"Task {task['id']} is {task['status']}")
        for role in Role:
            review = state["reviews"].get(role.value)
            if (
                not review
                or review["source_digest"] != current
                or review.get("context_digest") != context_digest(state, role)
                or review["verdict"] != "approve"
            ):
                blockers.append(f"Expert {role.value} needs a current approval")
        for finding in state["findings"].values():
            if finding["status"] == "open":
                blockers.append(f"Unresolved {finding['severity']} finding: {finding['id']}")
        for check in checks:
            evidence = state["evidence"].get(check.name)
            if check.required and (
                not evidence
                or not evidence["passed"]
                or evidence["source_digest"] != current
                or evidence["check_digest"] != check.digest
            ):
                blockers.append(f"Check {check.name} needs current passing evidence")
        return {
            "revision": revision,
            "source_digest": current,
            "context_digest": current_context,
            "release_ready": not blockers,
            "blockers": blockers,
            "ready_tasks": [
                t["id"]
                for t in state["tasks"].values()
                if t["status"] == "pending"
                and all(state["tasks"][d]["status"] == "done" for d in t["dependencies"])
            ],
            "state": state,
        }
