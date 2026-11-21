"""Immutable workflow definitions; no executable code is stored in the database."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any


def encode(value: Any) -> str:
    """Strict JSON: reject non-finite values and implicit key conversion."""
    def check(item: Any) -> None:
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child)
            return
        raise ValueError("values must be finite JSON with string object keys")
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def positive(value: float, name: str) -> None:
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class Job:
    id: str
    handler: str
    payload: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    max_attempts: int = 3
    backoff: float = 1.0


@dataclass(frozen=True)
class Workflow:
    jobs: tuple[Job, ...]

    def document(self) -> dict[str, Any]:
        if not self.jobs or len(self.jobs) > 10000:
            raise ValueError("workflow needs 1..10000 jobs")
        ids: set[str] = set()
        for job in self.jobs:
            if not isinstance(job, Job):
                raise ValueError("workflow entries must be Job instances")
            for name, value in (("id", job.id), ("handler", job.handler)):
                if not isinstance(value, str) or not value or len(value) > 200:
                    raise ValueError(f"{name} must be a nonempty string of at most 200 characters")
            if job.id in ids:
                raise ValueError(f"duplicate job: {job.id}")
            ids.add(job.id)
            if type(job.max_attempts) is not int or not 1 <= job.max_attempts <= 100:
                raise ValueError("max_attempts must be an integer in 1..100")
            positive(job.backoff, "backoff")
            if type(job.payload) is not dict:
                raise ValueError("payload must be a JSON object")
            encode(job.payload)
            if not isinstance(job.depends_on, (list, tuple)) or any(not isinstance(dep, str) for dep in job.depends_on):
                raise ValueError("depends_on must contain job id strings")
            if len(set(job.depends_on)) != len(job.depends_on):
                raise ValueError("duplicate dependency")
        remaining = {job.id: set(job.depends_on) for job in self.jobs}
        for deps in remaining.values():
            if not deps <= ids:
                raise ValueError("unknown dependency")
        # Iterative Kahn traversal also handles deep DAGs without recursion.
        followers: dict[str, list[str]] = {id: [] for id in ids}
        for id, deps in remaining.items():
            for dep in deps:
                followers[dep].append(id)
        ready = [id for id, deps in remaining.items() if not deps]
        visited = 0
        while ready:
            id = ready.pop()
            visited += 1
            for follower in followers[id]:
                remaining[follower].remove(id)
                if not remaining[follower]:
                    ready.append(follower)
        if visited != len(ids):
            raise ValueError("workflow contains a dependency cycle")
        return {"jobs": [dict(id=j.id, handler=j.handler, payload=j.payload,
                              depends_on=sorted(j.depends_on), max_attempts=j.max_attempts,
                              backoff=float(j.backoff)) for j in sorted(self.jobs, key=lambda j: j.id)]}

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Workflow:
        try:
            if set(document) != {"jobs"} or not isinstance(document["jobs"], list):
                raise ValueError("expected an object containing a jobs array")
            workflow = cls(tuple(Job(**job) for job in document["jobs"]))
            workflow.document()
            return workflow
        except (TypeError, KeyError) as exc:
            raise ValueError(f"invalid workflow: {exc}") from exc

    def fingerprint(self) -> str:
        return hashlib.sha256(encode(self.document()).encode()).hexdigest()


@dataclass(frozen=True)
class Claim:
    workflow_id: str
    job_id: str
    handler: str
    payload: dict[str, Any]
    dependencies: dict[str, Any]
    token: str
    attempt: int
    lease_until: float
