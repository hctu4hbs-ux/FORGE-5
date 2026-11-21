"""Searchable engineering constraints and lessons inside council persistence."""

from __future__ import annotations

import uuid
from typing import Any

from forge5.architecture import validate_contracts
from forge5.engine import Council
from forge5.models import ForgeError, string_list, text
from forge5.store import State, now


def _decision(state: State, record: dict[str, Any]) -> None:
    # Existing review freshness hashes decisions; mirror policy/memory changes here.
    state["decisions"].append(
        {
            "id": record["id"],
            "title": f"Engineering {record['kind']}",
            "options": [
                "Apply recorded engineering context",
                "Ignore recorded engineering context",
            ],
            "choice": "Apply recorded engineering context",
            "rationale": record["content"],
            "owner": "architect",
            "timestamp": record["timestamp"],
        }
    )
    state["reviews"].clear()


def record_memory(council: Council, kind: str, content: str, tags: list[str] | None = None) -> str:
    if kind not in ("constraint", "lesson", "decision"):
        raise ForgeError("Memory kind must be constraint, lesson or decision")
    record = {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "content": text(content, "content"),
        "tags": string_list(tags or [], "tags"),
        "timestamp": now(),
    }

    def update(state: State) -> None:
        state.setdefault("memory", []).append(record)
        _decision(state, record)

    council.store.mutate("memory.record", record, update)
    return str(record["id"])


def search_memory(council: Council, query: str, kind: str | None = None) -> list[dict[str, Any]]:
    query = text(query, "query", 1000).casefold()
    council.store.verify()
    _, state = council.store.read()
    records = list(state.get("memory", []))
    if kind in (None, "decision"):
        memory_ids = {record["id"] for record in records}
        records.extend(
            {
                "id": d["id"],
                "kind": "decision",
                "content": f"{d['title']} {d['choice']} {d['rationale']}",
                "tags": [],
                "timestamp": d["timestamp"],
            }
            for d in state["decisions"]
            if d["id"] not in memory_ids
        )
    terms = query.split()
    result = [
        record
        for record in records
        if (kind is None or record["kind"] == kind)
        and all(
            term in (record["content"] + " " + " ".join(record["tags"])).casefold()
            for term in terms
        )
    ]
    council.store.mutate(
        "memory.search", {"query": query, "kind": kind, "matches": len(result)}, lambda state: None
    )
    return result


def set_contracts(council: Council, contracts: dict[str, Any]) -> None:
    policy = validate_contracts(contracts)
    record = {
        "id": uuid.uuid4().hex,
        "kind": "constraint",
        "content": "Architecture contracts updated: " + str(policy),
        "tags": ["architecture"],
        "timestamp": now(),
    }

    def update(state: State) -> None:
        state["architecture_contracts"] = policy
        state.setdefault("memory", []).append(record)
        _decision(state, record)

    council.store.mutate("architecture.contracts", {"contracts": policy}, update)


def get_contracts(council: Council) -> dict[str, Any]:
    council.store.verify()
    return dict(council.store.read()[1].get("architecture_contracts", {}))
