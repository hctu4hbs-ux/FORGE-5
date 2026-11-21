"""A deliberately small AST triage scanner, not a complete security audit."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any

from forge5.models import ForgeError
from forge5.workspace import excluded

UNSAFE = {
    "eval": "Dynamic evaluation",
    "exec": "Dynamic code execution",
    "pickle.load": "Unsafe deserialization",
    "pickle.loads": "Unsafe deserialization",
    "marshal.loads": "Unsafe deserialization",
    "os.system": "Shell execution",
    "tempfile.mktemp": "Race-prone temporary filename",
}


def qualified(node: ast.expr, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f"{qualified(node.value, aliases)}.{node.attr}"
    return ""


def scan(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    root = root.resolve()
    if not root.is_dir():
        raise ForgeError(f"Scanner root must be an existing directory: {root}")
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not excluded(d))
        for name in dirs + sorted(files):
            path = Path(parent) / name
            if path.is_symlink():
                raise ForgeError(f"Scanner refuses source symlink: {path.relative_to(root)}")
            if path.is_dir() or not name.endswith(".py"):
                continue
            if not path.is_file():
                raise ForgeError(f"Scanner requires regular source files: {path.relative_to(root)}")
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ForgeError(f"Scanner source file exceeds 16 MiB: {path.relative_to(root)}")
            rel = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_bytes(), filename=rel)
            except (SyntaxError, UnicodeError) as exc:
                findings.append(
                    {
                        "path": rel,
                        "line": getattr(exc, "lineno", 1),
                        "rule": "syntax",
                        "severity": "high",
                        "message": str(exc),
                    }
                )
                continue
            aliases: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        aliases[alias.asname or alias.name.split(".")[0]] = (
                            alias.name if alias.asname else alias.name.split(".")[0]
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    for alias in node.names:
                        aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                target = qualified(node.func, aliases)
                rule = None
                message = ""
                if target in UNSAFE:
                    rule, message = "unsafe-call", UNSAFE[target]
                elif target.startswith("subprocess.") and any(
                    kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value
                    for kw in node.keywords
                ):
                    rule, message = "shell-true", "Subprocess uses shell=True"
                elif target in ("yaml.load", "yaml.unsafe_load"):
                    safe_loader = any(
                        kw.arg == "Loader"
                        and qualified(kw.value, aliases) in ("yaml.SafeLoader", "yaml.CSafeLoader")
                        for kw in node.keywords
                    )
                    if not safe_loader:
                        rule, message = "yaml-loader", "YAML load without an explicit safe loader"
                elif any(
                    kw.arg == "verify"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is False
                    for kw in node.keywords
                ) and (target.startswith(("requests.", "httpx."))):
                    rule, message = "tls-verification", "TLS verification disabled"
                if rule:
                    findings.append(
                        {
                            "path": rel,
                            "line": node.lineno,
                            "rule": rule,
                            "severity": "high",
                            "message": message,
                        }
                    )
    return sorted(findings, key=lambda f: (f["path"], f["line"], f["rule"]))
