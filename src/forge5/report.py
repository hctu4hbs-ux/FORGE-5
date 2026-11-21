"""Portable reports derived from current policy, never from marketing claims."""

from __future__ import annotations

from typing import Any


def markdown(status: dict[str, Any]) -> str:
    state = status["state"]
    lines = [
        "# FORGE-5 Engineering Report",
        "",
        f"Goal: {state['goal']}",
        "",
        f"Release ready: **{status['release_ready']}**",
        "",
        f"Source SHA-256: `{status['source_digest']}`",
        "",
        f"Council revision: {status['revision']}",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- {b}" for b in status["blockers"])
    if not status["blockers"]:
        lines.append(
            "No policy blockers. Human and deployment approvals remain host responsibilities."
        )
    lines.extend(
        [
            "",
            "## Expert reviews",
            "",
            "| Expert | Verdict | Mode | Current |",
            "| --- | --- | --- | --- |",
        ]
    )
    for role, review in sorted(state["reviews"].items()):
        current = review["source_digest"] == status["source_digest"]
        lines.append(f"| {role} | {review['verdict']} | {review['mode']} | {current} |")
    lines.extend(
        ["", "## Checks", "", "| Check | Passed | Seconds | Current |", "| --- | --- | --- | --- |"]
    )
    for name, evidence in sorted(state["evidence"].items()):
        current = evidence["source_digest"] == status["source_digest"]
        lines.append(f"| {name} | {evidence['passed']} | {evidence['seconds']:.3f} | {current} |")
    lines.extend(["", "## Findings", ""])
    for fid, finding in sorted(state["findings"].items()):
        lines.extend(
            [
                f"### {fid}: {finding['title']}",
                "",
                f"Severity: {finding['severity']}; status: {finding['status']}",
                "",
                finding["detail"],
                "",
                f"Evidence: {finding['evidence']}",
                "",
            ]
        )
        if finding["resolution"]:
            lines.extend(
                [
                    f"Resolution: {finding['resolution']['disposition']} — "
                    f"{finding['resolution']['rationale']}",
                    "",
                ]
            )
    lines.extend(
        [
            "## Limits",
            "",
            "This report records local evidence and declared expert roles. "
            "It does not prove reviewer independence, completeness, authenticity, or security. "
            "The AST scanner is triage. Execution isolation is recorded per check/provider; "
            "legacy local commands run with host permissions without a sandbox.",
            "",
        ]
    )
    return "\n".join(lines)
