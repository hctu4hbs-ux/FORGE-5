"""Explicit local execution or resource-limited Docker/Podman execution.

Container isolation is a deployment capability, not inferred from an executable's
presence. Local fallback requires affirmative configuration and reports its limits.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from forge5.models import ForgeError, finite_number
from forge5.runner import ProcessResult, run_process
from forge5.store import digest


@dataclass(frozen=True)
class IsolationPolicy:
    mode: str = "auto"
    image: str | None = None
    allow_local: bool = False
    timeout: float = 120
    memory_mb: int = 512
    cpus: float = 1
    pids_limit: int = 128
    output_limit: int = 131072
    writable: bool = False
    engine: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in ("auto", "local", "container"):
            raise ForgeError("Isolation mode must be auto, local or container")
        if self.engine not in (None, "docker", "podman"):
            raise ForgeError("Container engine must be docker or podman")
        for name in ("allow_local", "writable"):
            if type(getattr(self, name)) is not bool:
                raise ForgeError(f"{name} must be boolean")
        finite_number(self.timeout, "timeout", 0.05, 3600)
        finite_number(self.cpus, "cpus", 0.1, 64)
        for name, minimum, maximum in (
            ("memory_mb", 32, 65536),
            ("pids_limit", 8, 4096),
            ("output_limit", 1024, 1048576),
        ):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ForgeError(f"{name} must be an integer in {minimum}..{maximum}")
        if self.image is not None and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}", self.image
        ):
            raise ForgeError("Container image must be explicitly pinned with @sha256:<64 hex>")

    @property
    def digest(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class ExecutionResult:
    process: ProcessResult
    isolation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"process": self.process.to_dict(), "isolation": self.isolation}


def capabilities(cwd: Path) -> dict[str, Any]:
    engines: list[dict[str, Any]] = []
    for name in ("docker", "podman"):
        binary = shutil.which(name)
        available = False
        reason = "Executable not installed"
        if binary:
            probe = run_process(
                (binary, "info", "--format", "{{.ServerVersion}}"), cwd, 5, output_limit=4096
            )
            available = probe.exit_code == 0 and not probe.timed_out and not probe.truncated
            reason = "Engine responded" if available else "Engine daemon/runtime probe failed"
        engines.append(
            {"engine": name, "executable": binary, "available": available, "reason": reason}
        )
    return {
        "engines": engines,
        "local_process_groups": os.name == "posix",
        "local_is_sandbox": False,
    }


def execute(
    argv: tuple[str, ...], cwd: Path, policy: IsolationPolicy, *, stdin: str | None = None
) -> ExecutionResult:
    if not argv or any(not isinstance(a, str) or not a or "\0" in a for a in argv):
        raise ForgeError("Execution needs a nonempty argv without NUL bytes")
    cwd = cwd.resolve()
    if "," in str(cwd):
        raise ForgeError("Container mount paths containing commas are unsupported")
    if not cwd.is_dir():
        raise ForgeError("Execution workspace must exist")
    selected: str | None = None
    limitations: list[str] = []
    if policy.mode != "local":
        for item in capabilities(cwd)["engines"]:
            if item["available"] and (policy.engine is None or item["engine"] == policy.engine):
                selected = item["executable"]
                break
        if selected is None:
            limitations.append("No operational Docker/Podman runtime available")
        elif not policy.image:
            limitations.append("No explicitly pinned container image configured")
            selected = None
        else:
            probe = run_process(
                (selected, "image", "inspect", "--format", "{{.Id}}", policy.image),
                cwd,
                5,
                output_limit=4096,
            )
            if probe.exit_code != 0 or probe.timed_out:
                limitations.append("Pinned image is not installed; automatic pulls are disabled")
                selected = None
    if selected is None:
        if policy.mode == "container" or not policy.allow_local:
            raise ForgeError(
                "Isolation unavailable; explicitly opt into trusted local execution "
                "or provision an operational engine and pinned local image"
            )
        process = run_process(
            argv, cwd, policy.timeout, stdin=stdin, output_limit=policy.output_limit
        )
        return ExecutionResult(
            process,
            {
                "backend": "local",
                "sandboxed": False,
                "network_isolated": False,
                "memory_enforced": False,
                "policy_digest": policy.digest,
                "limitations": limitations
                + [
                    "Host permissions/environment inherited; no filesystem, "
                    "network, CPU or memory isolation; detached children may escape group cleanup"
                ],
            },
        )
    container_name = "forge5-" + uuid.uuid4().hex
    # Private container tmpfs; no shared host temporary files are created.
    private_tmpfs = "/tmp:rw,noexec,nosuid,size=64m"  # nosec B108
    translated = ("python3", *argv[1:]) if argv[0] == sys.executable else argv
    command = (
        selected,
        "run",
        "--rm",
        "--pull=never",
        "--name",
        container_name,
        "--network=none",
        "--memory",
        f"{policy.memory_mb}m",
        "--memory-swap",
        f"{policy.memory_mb}m",
        "--cpus",
        str(policy.cpus),
        "--pids-limit",
        str(policy.pids_limit),
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user",
        (
            f"{os.getuid()}:{os.getgid()}"
            if os.name == "posix" and os.getuid() != 0 and os.getgid() != 0
            else "65534:65534"
        ),
        "--tmpfs",
        private_tmpfs,
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "HOME=/tmp",
        "--workdir",
        "/workspace",
        "--mount",
        f"type=bind,src={cwd},dst=/workspace" + ("" if policy.writable else ",readonly"),
        "--interactive",
        policy.image or "",
        *translated,
    )
    try:
        process = run_process(
            command, cwd, policy.timeout, stdin=stdin, output_limit=policy.output_limit
        )
    finally:
        # Killing the client does not guarantee container shutdown. Remove by unique name.
        run_process((selected, "rm", "--force", container_name), cwd, 5, output_limit=4096)
    return ExecutionResult(
        process,
        {
            "backend": "container",
            "sandboxed": True,
            "network_isolated": True,
            "memory_enforced": True,
            "memory_mb": policy.memory_mb,
            "cpus": policy.cpus,
            "pids_limit": policy.pids_limit,
            "image": policy.image,
            "policy_digest": policy.digest,
            "limitations": [
                "Depends on host engine/kernel security; "
                "a writable mount permits workspace edits when explicitly enabled"
            ],
        },
    )
