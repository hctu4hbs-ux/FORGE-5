"""Explicit, versioned project policy. Configuration is part of the source digest."""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge5.models import ForgeError, finite_number, identifier, keys, string_list
from forge5.store import digest

DEFAULT_CONFIG = """schema = 1

[[checks]]
name = "tests"
argv = ["{python}", "-m", "forge5", "test", "--start", "tests"]
kind = "test"
timeout = 120
required = true

[[checks]]
name = "security"
argv = ["{python}", "-m", "forge5", "scan", "."]
kind = "security"
timeout = 60
required = true
"""


@dataclass(frozen=True)
class Check:
    name: str
    argv: tuple[str, ...]
    kind: str
    timeout: float
    required: bool
    max_seconds: float | None = None

    @classmethod
    def parse(cls, data: Any) -> Check:
        data = keys(data, {"name", "argv", "kind", "timeout", "required"}, {"max_seconds"})
        argv = string_list(data["argv"], "argv")
        if not argv or len(argv) > 100:
            raise ForgeError("Check argv must contain 1..100 arguments")
        if data["kind"] not in ("test", "security", "performance", "lint", "type", "custom"):
            raise ForgeError("Unknown check kind")
        if type(data["required"]) is not bool:
            raise ForgeError("required must be a boolean")
        budget = data.get("max_seconds")
        return cls(
            identifier(data["name"], "check name"),
            tuple(sys.executable if a == "{python}" else a for a in argv),
            data["kind"],
            finite_number(data["timeout"], "timeout", 0.05, 3600),
            data["required"],
            finite_number(budget, "max_seconds", 0.001, 3600) if budget is not None else None,
        )

    @property
    def digest(self) -> str:
        # Include the resolved interpreter: another environment needs fresh checks.
        return digest(
            {
                "argv": self.argv,
                "kind": self.kind,
                "timeout": self.timeout,
                "required": self.required,
                "max_seconds": self.max_seconds,
            }
        )


def load_checks(root: Path) -> tuple[Check, ...]:
    try:
        data = tomllib.loads((root / "forge5.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ForgeError(f"Cannot read forge5.toml: {exc}") from exc
    keys(data, {"schema", "checks"})
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise ForgeError("Only policy schema 1 is supported")
    if not isinstance(data["checks"], list) or not 1 <= len(data["checks"]) <= 100:
        raise ForgeError("Policy needs 1..100 checks")
    checks = tuple(Check.parse(c) for c in data["checks"])
    if len({c.name for c in checks}) != len(checks):
        raise ForgeError("Check names must be unique")
    required_kinds = {c.kind for c in checks if c.required}
    if not {"test", "security"} <= required_kinds:
        raise ForgeError("Policy must require at least one test and one security check")
    return checks
