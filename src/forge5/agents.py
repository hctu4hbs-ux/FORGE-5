"""Distinct engineering-agent contracts and independently configured command providers."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from forge5.engine import Council
from forge5.models import ForgeError, finite_number, identifier, keys, string_list, text
from forge5.store import canonical

CONTRACTS: dict[str, dict[str, Any]] = {
    "architect": {
        "mission": (
            "Analyze the objective, identify invariants and failure boundaries, compare "
            "architectural alternatives, and decompose acceptance behavior into an "
            "acyclic implementation graph before coding."
        ),
        "tools": ["read_file", "search", "tree", "tasks"],
        "deliverables": [
            "dependency-aware plan",
            "explicit module contracts",
            "bounded file ownership",
        ],
        "criteria": [
            "feasible task dependencies",
            "testable acceptance contracts",
            "minimal coherent design",
        ],
    },
    "principal": {
        "mission": (
            "Review actual implementation flow and public contracts. Check integration, "
            "compatibility, state ownership, and whether concrete acceptance behavior is "
            "implemented rather than stubbed."
        ),
        "tools": ["read_file", "search", "tree", "run_check", "inspect_changes", "tasks"],
        "deliverables": ["implementation review", "file/line evidence", "integration objections"],
        "criteria": [
            "correct data transformations",
            "maintainable APIs",
            "sound concurrency and recovery",
        ],
    },
    "debugger": {
        "mission": (
            "Investigate reproducible failures, error handling and missing regressions. "
            "Challenge test discovery, boundary values, partial failures, restart and "
            "cancellation. Require evidence that fixes address root causes."
        ),
        "tools": ["read_file", "search", "tree", "run_check", "inspect_changes"],
        "deliverables": ["failure reproductions", "regression requirements", "verification review"],
        "criteria": ["meaningful failing/passing tests", "determinism", "recovery behavior"],
    },
    "security": {
        "mission": (
            "Inspect assets, attackers and trust boundaries. Review injection, "
            "credentials, filesystem access, dependencies, unsafe parsing, resource "
            "exhaustion and measured performance budgets."
        ),
        "tools": ["read_file", "search", "tree", "run_check", "inspect_changes"],
        "deliverables": ["threat findings", "security evidence", "performance budget assessment"],
        "criteria": [
            "least privilege",
            "bounded resources",
            "measured rather than assumed safety/speed",
        ],
    },
    "product": {
        "mission": (
            "Execute the documented installation and first useful journey. Review "
            "packaging, licensing, compatibility, release evidence and actual user value; "
            "reject unsupported capability and production claims."
        ),
        "tools": ["read_file", "tree", "run_check", "inspect_changes", "tasks"],
        "deliverables": [
            "release usability review",
            "documentation gaps",
            "bounded readiness verdict",
        ],
        "criteria": [
            "reproducible installation",
            "fulfilled acceptance goals",
            "honest operational limits",
        ],
    },
    "implementation": {
        "mission": (
            "Implement only the assigned DAG task and its tests. Return a bounded, "
            "evidence-backed change set. Preserve existing tests and quality policy; "
            "respond to objections but never close your own findings."
        ),
        "tools": [
            "read_file",
            "search",
            "tree",
            "run_check",
            "inspect_changes",
            "apply_changes",
        ],
        "deliverables": [
            "explicit file changes with before hashes",
            "new regression tests",
            "implementation evidence",
        ],
        "criteria": ["scope compliance", "passing independent checks", "reviewable minimal patch"],
    },
}


@dataclass(frozen=True)
class AgentSpec:
    role: str
    argv: tuple[str, ...]
    provider: str
    identity: str
    model: str | None = None
    timeout: float = 180

    @classmethod
    def parse(cls, role: str, value: Any) -> AgentSpec:
        data = keys(value, {"argv", "provider", "identity"}, {"model", "timeout"})
        argv = string_list(data["argv"], "agent argv")
        if not argv or len(argv) > 100:
            raise ForgeError("Agent argv must contain 1..100 arguments")
        return cls(
            role,
            tuple(argv),
            text(data["provider"], "provider", 200),
            identifier(data["identity"], "identity"),
            text(data["model"], "model", 200) if data.get("model") else None,
            finite_number(data.get("timeout", 180), "agent timeout", 0.05, 3600),
        )


class AgentRegistry:
    """Provider declarations are recorded, never treated as authenticated identities."""

    def __init__(self, specs: dict[str, AgentSpec]) -> None:
        if set(specs) != set(CONTRACTS):
            raise ForgeError("Configure all five reviewers and a separate implementation agent")
        if any(key != spec.role for key, spec in specs.items()):
            raise ForgeError("Agent role does not match its registry key")
        identities = [specs[role].identity for role in CONTRACTS]
        if len(set(identities)) != len(identities):
            raise ForgeError("All six agent identities must be distinct declarations")
        self.specs = specs

    @classmethod
    def load(cls, path: Path) -> AgentRegistry:
        try:
            raw = keys(json.loads(path.read_text()), {"schema", "agents"})
        except (OSError, ValueError) as exc:
            raise ForgeError(f"Cannot load agents: {exc}") from exc
        if raw["schema"] != 1 or not isinstance(raw["agents"], dict):
            raise ForgeError("Agent registry requires schema 1 and an agents object")
        return cls({role: AgentSpec.parse(role, spec) for role, spec in raw["agents"].items()})

    def describe(self) -> dict[str, Any]:
        return {
            "execution": "fresh command process per invocation",
            "identity_assurance": "declared identities; provider independence is not authenticated",
            "agents": {
                role: {
                    "provider": spec.provider,
                    "identity": spec.identity,
                    "model": spec.model,
                    "argv": list(spec.argv),
                    "timeout": spec.timeout,
                    "contract": CONTRACTS[role],
                }
                for role, spec in self.specs.items()
            },
        }

    def invoke(
        self, council: Council, role: str, request: dict[str, Any], workspace: Path, isolation: Any
    ) -> dict[str, Any]:
        from forge5.isolation import execute

        spec = self.specs[role]
        packet = {
            **request,
            "role": role,
            # The provider's cwd is the project in both local and container execution.
            "root": ".",
            "contract": CONTRACTS[role],
            "provider": spec.provider,
            "model": spec.model,
            "identity": spec.identity,
        }
        council.store.mutate(
            "agent.request",
            {
                "role": role,
                "identity": spec.identity,
                "provider": spec.provider,
                "model": spec.model,
                "protocol": request.get("protocol"),
            },
            lambda state: None,
        )
        encoded = canonical(packet)
        if len(encoded.encode()) > 1048576:
            raise ForgeError("Agent packet exceeds 1 MiB")
        policy = replace(isolation, timeout=min(spec.timeout, isolation.timeout), writable=False)
        result = execute(spec.argv, workspace, policy, stdin=encoded)
        process = result.process
        # Preserve actual isolation metadata supplied by the execution layer.
        isolation_metadata = getattr(result, "isolation", None)
        if isolation_metadata is None:
            isolation_metadata = getattr(result, "metadata", {})
        council.store.mutate(
            "agent.result",
            {
                "role": role,
                "identity": spec.identity,
                "exit_code": process.exit_code,
                "seconds": process.seconds,
                "timed_out": process.timed_out,
                "truncated": process.truncated,
                "isolation": isolation_metadata,
            },
            lambda state: None,
        )
        if process.exit_code != 0 or process.timed_out or process.truncated:
            raise ForgeError(
                f"{role} provider failed: exit={process.exit_code}, "
                f"timeout={process.timed_out}, truncated={process.truncated}"
            )
        try:
            response = json.loads(process.output)
        except (ValueError, RecursionError) as exc:
            raise ForgeError(f"{role} provider returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise ForgeError("Agent response must be a JSON object")
        return response
