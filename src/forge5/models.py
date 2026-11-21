"""Small, strict wire contracts shared by hosts, the CLI and adapters."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ForgeError(Exception):
    """An actionable domain error, safe to render at the CLI boundary."""


class Role(StrEnum):
    ARCHITECT = "architect"
    PRINCIPAL = "principal"
    DEBUGGER = "debugger"
    SECURITY = "security"
    PRODUCT = "product"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def text(value: Any, name: str, limit: int = 16000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ForgeError(f"{name} must be nonempty text of at most {limit} characters")
    return value.strip()


def identifier(value: Any, name: str = "id") -> str:
    normalized = text(value, name, 80)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", normalized):
        raise ForgeError(f"{name} must contain only letters, numbers, dot, dash and underscore")
    return normalized


def string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ForgeError(f"{name} must be an array of nonempty strings")
    if len(value) > 10000:
        raise ForgeError(f"{name} exceeds 10000 entries")
    return value


def keys(data: Any, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ForgeError("Expected a JSON object")
    missing = required - data.keys()
    extra = data.keys() - required - (optional or set())
    if missing or extra:
        raise ForgeError(f"Invalid fields: missing={sorted(missing)}, unknown={sorted(extra)}")
    return data


def finite_number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ForgeError(f"{name} must be a number")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ForgeError(f"{name} must be finite and between {minimum} and {maximum}")
    return float(value)


@dataclass(frozen=True)
class Finding:
    id: str
    severity: Severity
    title: str
    detail: str
    evidence: str

    @classmethod
    def parse(cls, data: Any) -> Finding:
        data = keys(data, {"id", "severity", "title", "detail", "evidence"})
        try:
            severity = Severity(data["severity"])
        except (TypeError, ValueError) as exc:
            raise ForgeError("Invalid finding severity") from exc
        return cls(
            identifier(data["id"]),
            severity,
            text(data["title"], "title", 500),
            text(data["detail"], "detail"),
            text(data["evidence"], "evidence"),
        )


@dataclass(frozen=True)
class Review:
    role: Role
    verdict: str
    summary: str
    findings: tuple[Finding, ...]

    @classmethod
    def parse(cls, data: Any) -> Review:
        data = keys(data, {"role", "verdict", "summary", "findings"})
        try:
            role = Role(data["role"])
        except (TypeError, ValueError) as exc:
            raise ForgeError("Invalid expert role") from exc
        if data["verdict"] not in ("approve", "request_changes", "abstain"):
            raise ForgeError("verdict must be approve, request_changes or abstain")
        if not isinstance(data["findings"], (list, tuple)) or len(data["findings"]) > 100:
            raise ForgeError("findings must be an array of at most 100 entries")
        findings = tuple(Finding.parse(f) for f in data["findings"])
        if len({f.id for f in findings}) != len(findings):
            raise ForgeError("Finding IDs must be unique within a review")
        if data["verdict"] == "approve" and any(
            f.severity in (Severity.CRITICAL, Severity.HIGH) for f in findings
        ):
            raise ForgeError("A review with high/critical findings cannot approve")
        return cls(role, data["verdict"], text(data["summary"], "summary"), findings)
