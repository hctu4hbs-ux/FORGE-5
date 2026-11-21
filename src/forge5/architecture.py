"""AST architecture contracts for project-local Python imports.

This is static import analysis, not dynamic dependency tracing. Contracts and
results are explicit so a host can decide whether additional tooling is needed.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import os
from pathlib import Path
from typing import Any

from forge5.models import ForgeError
from forge5.workspace import excluded


def _matches(module: str, pattern: str) -> bool:
    return (
        module == pattern
        or module.startswith(pattern + ".")
        or fnmatch.fnmatchcase(module, pattern)
    )


def validate_contracts(contracts: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(contracts, dict) or set(contracts) - {"forbidden_imports", "layers"}:
        raise ForgeError("Architecture contracts accept only forbidden_imports and layers")
    forbidden = contracts.get("forbidden_imports", [])
    layers = contracts.get("layers", [])
    if not isinstance(forbidden, list) or not isinstance(layers, list):
        raise ForgeError("Architecture rules and layers must be arrays")
    if len(forbidden) > 1000 or len(layers) > 100:
        raise ForgeError("Architecture contract size limit exceeded")
    for rule in forbidden:
        if (
            not isinstance(rule, dict)
            or set(rule) != {"from", "to"}
            or any(not isinstance(rule[k], str) or not rule[k].strip() for k in rule)
        ):
            raise ForgeError("Forbidden import rules require nonempty from/to module patterns")
    for layer in layers:
        if (
            not isinstance(layer, list)
            or not layer
            or any(not isinstance(p, str) or not p.strip() for p in layer)
        ):
            raise ForgeError("Each layer must contain nonempty module patterns")
    return {"forbidden_imports": forbidden, "layers": layers}


def _sources(root: Path) -> dict[str, tuple[Path, bool]]:
    source = root / "src" if (root / "src").is_dir() else root
    if source.is_symlink():
        raise ForgeError("Architecture source root must not be a symlink")
    modules: dict[str, tuple[Path, bool]] = {}
    for parent, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not excluded(d))
        for name in dirs + sorted(files):
            path = Path(parent) / name
            if path.is_symlink():
                raise ForgeError(f"Architecture scan refuses source symlink: {path}")
            if path.is_dir() or not name.endswith(".py"):
                continue
            if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
                raise ForgeError(f"Unsupported or oversized Python source: {path}")
            relative = path.relative_to(source).with_suffix("")
            package = relative.name == "__init__"
            parts = relative.parts[:-1] if package else relative.parts
            name = ".".join(parts)
            if name:
                modules[name] = (path, package)
            if len(modules) > 10000:
                raise ForgeError("Architecture analysis exceeds 10000 Python modules")
    return modules


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    # Iterative Kosaraju avoids recursion limits on deeply nested real projects.
    visited: set[str] = set()
    order: list[str] = []
    for start in graph:
        if start in visited:
            continue
        stack = [(start, False)]
        while stack:
            node, finished = stack.pop()
            if finished:
                order.append(node)
                continue
            if node in visited:
                continue
            visited.add(node)
            stack.append((node, True))
            stack.extend(
                (target, False)
                for target in sorted(graph[node], reverse=True)
                if target not in visited
            )
    reverse: dict[str, set[str]] = {node: set() for node in graph}
    for node, targets in graph.items():
        for target in targets:
            reverse[target].add(node)
    visited.clear()
    components = []
    for start in reversed(order):
        if start in visited:
            continue
        component: list[str] = []
        pending = [start]
        while pending:
            node = pending.pop()
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            pending.extend(reverse[node] - visited)
        if len(component) > 1 or start in graph[start]:
            components.append(sorted(component))
    return sorted(components)


def check_architecture(root: Path, contracts: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise ForgeError("Architecture root must be an existing directory")
    if contracts is None:
        path = root / ".forge5" / "architecture.json"
        if path.exists():
            if (
                path.parent.is_symlink()
                or path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > 1048576
            ):
                raise ForgeError("Architecture contract file must be a bounded regular file")
            try:
                contracts = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                raise ForgeError(f"Invalid architecture contract: {exc}") from exc
        else:
            contracts = {}
    policy = validate_contracts(contracts)
    sources = _sources(root)
    graph: dict[str, set[str]] = {name: set() for name in sources}
    edges: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []

    def resolve(name: str) -> str | None:
        parts = name.split(".")
        while parts:
            candidate = ".".join(parts)
            if candidate in sources:
                return candidate
            parts.pop()
        return None

    for module, (path, is_package) in sources.items():
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path.relative_to(root)))
        except (SyntaxError, UnicodeError) as exc:
            violations.append(
                {
                    "kind": "syntax",
                    "path": path.relative_to(root).as_posix(),
                    "line": getattr(exc, "lineno", 1),
                    "message": str(exc),
                }
            )
            continue
        for node in ast.walk(tree):
            imports: list[str] = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    package = module.split(".") if is_package else module.split(".")[:-1]
                    if node.level > len(package):
                        violations.append(
                            {
                                "kind": "relative-import",
                                "path": str(path.relative_to(root)),
                                "line": getattr(node, "lineno", 0),
                                "message": "Relative import escapes package",
                            }
                        )
                        continue
                    prefix = package[: len(package) - node.level + 1]
                    base = ".".join(prefix + ([node.module] if node.module else []))
                else:
                    base = node.module or ""
                imports = [f"{base}.{alias.name}" if base else alias.name for alias in node.names]
                if base and base != module:
                    imports.append(base)
            for imported in imports:
                target = resolve(imported)
                if target is None:
                    continue
                # `from . import child` refers to child, not an implicit self-import.
                if target == module and imported != module:
                    continue
                graph[module].add(target)
                edge = {
                    "from": module,
                    "to": target,
                    "path": path.relative_to(root).as_posix(),
                    "line": getattr(node, "lineno", 0),
                }
                if edge in edges:
                    continue
                edges.append(edge)
                for rule in policy["forbidden_imports"]:
                    if _matches(module, rule["from"]) and _matches(target, rule["to"]):
                        violations.append({**edge, "kind": "forbidden-import", "rule": rule})
                origin_layers = [
                    i
                    for i, layer in enumerate(policy["layers"])
                    if any(_matches(module, p) for p in layer)
                ]
                target_layers = [
                    i
                    for i, layer in enumerate(policy["layers"])
                    if any(_matches(target, p) for p in layer)
                ]
                if origin_layers and target_layers and min(origin_layers) > min(target_layers):
                    violations.append({**edge, "kind": "layer-direction"})
    for cycle in _cycles(graph):
        violations.append({"kind": "cycle", "modules": cycle})
    return {
        "passed": not violations,
        "modules": len(sources),
        "edges": edges,
        "graph": {key: sorted(value) for key, value in sorted(graph.items())},
        "violations": violations,
        "contracts": policy,
        "limits": [
            "Static Python imports only; dynamic imports and runtime plugin loading "
            "require additional review"
        ],
    }
