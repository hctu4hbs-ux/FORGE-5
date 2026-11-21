"""Bounded subprocess execution without shell interpolation.

This is process management, not a sandbox. Only run trusted commands in an
appropriately isolated workspace. POSIX process groups are terminated on timeout.
"""

from __future__ import annotations

import os
import signal

# Explicit trusted-command runner; see SECURITY.md.
import subprocess  # nosec B404
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    exit_code: int | None
    seconds: float
    timed_out: bool
    output: str
    truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass


def run_process(
    argv: tuple[str, ...],
    cwd: Path,
    timeout: float,
    *,
    stdin: str | None = None,
    output_limit: int = 131072,
) -> ProcessResult:
    start = time.monotonic()
    timed_out = False
    # Temporary files avoid unbounded RAM and pipe deadlocks. Disk usage is bounded
    # by polling and terminating a noisy child; an OS/container quota remains useful.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as input_file:
        if stdin is not None:
            input_file.write(stdin.encode())
            input_file.seek(0)
        try:
            # argv is explicitly authorized; shell interpolation is disabled.
            process = subprocess.Popen(  # nosec B603
                argv,
                cwd=cwd,
                stdin=input_file,
                stdout=output,
                stderr=subprocess.STDOUT,
                shell=False,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            return ProcessResult(argv, None, time.monotonic() - start, False, str(exc), False)
        overflow = False
        try:
            while process.poll() is None:
                elapsed = time.monotonic() - start
                overflow = os.fstat(output.fileno()).st_size > output_limit
                if elapsed >= timeout or overflow:
                    timed_out = elapsed >= timeout
                    terminate(process)
                    break
                time.sleep(min(0.02, max(0.001, timeout - elapsed)))
            process.wait()
        finally:
            # Also terminate surviving children after a parent exits successfully.
            terminate(process)
            process.wait()
        size = os.fstat(output.fileno()).st_size
        output.seek(0)
        content = output.read(output_limit).decode("utf-8", errors="replace")
        return ProcessResult(
            argv,
            process.returncode,
            time.monotonic() - start,
            timed_out,
            content,
            overflow or size > output_limit,
        )
