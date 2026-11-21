#!/usr/bin/env python3
"""Install the portable skill into an explicitly selected discovery directory."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def install(destination: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "skills" / "forge-5"
    target = destination.expanduser().resolve() / "forge-5"
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing skill: {target}")
    destination.expanduser().mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest", type=Path, required=True, help="Your agent's skill discovery directory"
    )
    args = parser.parse_args()
    try:
        target = install(args.dest)
    except OSError as exc:
        parser.exit(2, f"{exc}\n")
    print(f"Installed FORGE-5 skill at {target}")
    print("The Python runtime must also be installed in the agent's execution environment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
