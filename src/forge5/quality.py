"""Reproducible quality inventories and narrow security checks.

Inventories describe the current Python environment, not a resolved project lock.
Optional scanners report unavailable honestly; none are silently marked successful.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from forge5.models import ForgeError
from forge5.runner import run_process
from forge5.security import scan
from forge5.workspace import excluded

SECRET_RULES = {
    "aws-access-key": re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,255}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "credential-assignment": re.compile(
        r"""(?i)\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*['"]([^'"\n]{12,})['"]"""
    ),
}


def secret_scan(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise ForgeError("Secret scan root must be an existing directory")
    findings: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    scanned = 0
    count = 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not excluded(d))
        for name in dirs + sorted(files):
            if excluded(name):
                continue
            path = Path(parent) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ForgeError(f"Secret scanner refuses source symlink: {rel}")
            if path.is_dir():
                continue
            count += 1
            if count > 100000:
                raise ForgeError("Secret scan exceeds 100000 source entries")
            if not path.is_file():
                raise ForgeError(f"Secret scanner requires regular files: {rel}")
            if path.stat().st_size > 1048576:
                skipped.append({"path": rel, "reason": "file exceeds 1 MiB scan limit"})
                continue
            try:
                content = path.read_bytes().decode("utf-8")
            except UnicodeError:
                skipped.append({"path": rel, "reason": "non-UTF-8 content"})
                continue
            if "\0" in content:
                skipped.append({"path": rel, "reason": "binary content"})
                continue
            scanned += 1
            for line, value in enumerate(content.splitlines(), 1):
                for rule, pattern in SECRET_RULES.items():
                    if pattern.search(value):
                        # Never copy the credential or source line to a report/audit log.
                        findings.append(
                            {
                                "path": rel,
                                "line": line,
                                "rule": rule,
                                "severity": "high",
                                "value": "[REDACTED]",
                            }
                        )
    return {
        "available": True,
        "passed": not findings and not skipped,
        "complete": not skipped,
        "scanned_files": scanned,
        "findings": findings,
        "skipped": skipped,
        "limits": ["Pattern triage, not complete secret detection"],
    }


def _distributions(packages: list[str] | None) -> list[importlib.metadata.Distribution]:
    if packages is None:
        return sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"] or "")
    if len(packages) > 10000 or any(not isinstance(p, str) or not p.strip() for p in packages):
        raise ForgeError("packages must contain at most 10000 nonempty distribution names")
    try:
        return [importlib.metadata.distribution(name) for name in sorted(set(packages))]
    except importlib.metadata.PackageNotFoundError as exc:
        raise ForgeError(f"Distribution is not installed: {exc}") from exc


def sbom(packages: list[str] | None = None) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    for distribution in _distributions(packages):
        metadata = {key: distribution.metadata[key] for key in distribution.metadata}
        name = metadata.get("Name", "UNKNOWN")
        component: dict[str, Any] = {
            "type": "library",
            "name": name,
            "version": distribution.version,
        }
        license_name = metadata.get("License-Expression") or metadata.get("License")
        if license_name and len(license_name) <= 200 and "\n" not in license_name:
            component["licenses"] = [{"license": {"name": license_name}}]
        components.append(component)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "properties": [
                {
                    "name": "forge5:scope",
                    "value": "Installed Python environment inventory; not a resolved project lock "
                    "or license opinion",
                }
            ]
        },
        "components": components,
    }


def license_inventory(
    packages: list[str] | None = None, allowed: list[str] | None = None
) -> dict[str, Any]:
    items = []
    for distribution in _distributions(packages):
        metadata = {key: distribution.metadata[key] for key in distribution.metadata}
        declared = metadata.get("License-Expression") or metadata.get("License") or "UNKNOWN"
        if len(declared) > 200 or "\n" in declared:
            declared = "SEE_DISTRIBUTION_LICENSE"
        items.append(
            {
                "name": metadata.get("Name", "UNKNOWN"),
                "version": distribution.version,
                "declared_license": declared,
                "allowed": None if allowed is None else declared in allowed,
            }
        )
    violations = [item for item in items if item["allowed"] is False]
    unknown = [
        item["name"]
        for item in items
        if item["declared_license"] in ("UNKNOWN", "SEE_DISTRIBUTION_LICENSE")
    ]
    return {
        "available": True,
        "passed": not violations,
        "policy_applied": allowed is not None,
        "packages": items,
        "violations": violations,
        "requires_manual_review": unknown,
        "limits": [
            "Declared package metadata only; inspect actual license files "
            "and transitive obligations"
        ],
    }


def dependency_audit(root: Path, *, required: bool = False) -> dict[str, Any]:
    # pip-audit can contact package indexes/advisory services. The caller must opt in.
    if importlib.util.find_spec("pip_audit") is None:
        return {
            "available": False,
            "passed": False,
            "required": required,
            "status": "unavailable",
            "reason": "pip-audit is not installed",
            "network": "not_run",
        }
    result = run_process(
        (sys.executable, "-m", "pip_audit", "--format", "json"),
        root.resolve(),
        120,
        output_limit=262144,
    )
    data: Any = None
    parse_error = None
    try:
        data = json.loads(result.output)
    except ValueError:
        parse_error = "pip-audit did not return a complete JSON report"
    return {
        "available": True,
        "passed": result.exit_code == 0
        and not result.timed_out
        and not result.truncated
        and parse_error is None,
        "required": required,
        "exit_code": result.exit_code,
        "seconds": result.seconds,
        "timed_out": result.timed_out,
        "truncated": result.truncated,
        "report": data,
        "error": parse_error,
        "network": "scanner_may_contact_advisory_services",
    }


def quality_report(
    root: Path,
    *,
    dependency_audit: bool = False,
    required_audit: bool = False,
    packages: list[str] | None = None,
    allowed_licenses: list[str] | None = None,
) -> dict[str, Any]:
    ast_findings = scan(root)
    secrets = secret_scan(root)
    licenses = license_inventory(packages, allowed_licenses)
    audit = (
        _dependency_audit(root, required=required_audit)
        if dependency_audit or required_audit
        else {"available": None, "passed": None, "required": False, "status": "not_requested"}
    )
    passed = not ast_findings and secrets["passed"] and licenses["passed"]
    if dependency_audit or required_audit:
        passed = passed and audit["passed"] is True
    return {
        "available": True,
        "passed": bool(passed),
        "ast": {"available": True, "passed": not ast_findings, "findings": ast_findings},
        "secrets": secrets,
        "licenses": licenses,
        "sbom": sbom(packages),
        "dependency_audit": audit,
        "limits": [
            "No production or complete-security claim; external auditors "
            "need explicit installation",
            "SBOM and licenses describe this interpreter environment, not a project lock",
        ],
    }


# Preserve the public function name while quality_report uses a same-named flag.
_dependency_audit = dependency_audit
