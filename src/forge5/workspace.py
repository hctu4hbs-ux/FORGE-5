"""Content-address project inputs; fail explicitly on unsupported snapshots."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from forge5.models import ForgeError

EXCLUDED = frozenset(
    {
        ".git",
        ".forge5",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        "htmlcov",
    }
)


def excluded(name: str) -> bool:
    return name in EXCLUDED or name.endswith(".egg-info") or name.startswith(".coverage")


def fingerprint(root: Path) -> str:
    hasher = hashlib.sha256()
    count = 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not excluded(d))
        for name in dirs + sorted(files):
            if excluded(name):
                continue
            path = Path(parent) / name
            if path.is_symlink():
                raise ForgeError(f"Source symlinks are unsupported: {path.relative_to(root)}")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ForgeError(f"Unsupported source entry: {path.relative_to(root)}")
            before = path.stat()
            if before.st_size > 16 * 1024 * 1024:
                raise ForgeError(f"Source file exceeds 16 MiB: {path.relative_to(root)}")
            content = path.read_bytes()
            after = path.stat()
            if (before.st_mtime_ns, before.st_size, before.st_mode) != (
                after.st_mtime_ns,
                after.st_size,
                after.st_mode,
            ):
                raise ForgeError("Source changed while snapshotting; retry after writers stop")
            rel = path.relative_to(root).as_posix().encode()
            hasher.update(len(rel).to_bytes(4, "big") + rel)
            hasher.update((before.st_mode & 0o111).to_bytes(2, "big"))
            hasher.update(hashlib.sha256(content).digest())
            count += 1
            if count > 100000:
                raise ForgeError("Snapshot exceeds 100000 files")
    return hasher.hexdigest()
