"""Transactional snapshots with an append-only, hash-linked audit trail.

Hash linking detects accidental edits. It is not a signature or a security boundary
against an actor who can rewrite the local database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from forge5.models import ForgeError

T = TypeVar("T")
State = dict[str, Any]


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, root: Path, *, create: bool = False) -> None:
        self.path = root / ".forge5" / "council.sqlite3"
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise ForgeError("Council database paths must not be symlinks")
        if create:
            self.path.parent.mkdir(mode=0o700, exist_ok=True)
        elif not self.path.is_file():
            raise ForgeError("No council found; run forge5 init first")
        with closing(self.connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ForgeError(f"Unsupported council database schema: {version}")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS snapshot (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    revision INTEGER NOT NULL, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL,
                    previous_hash TEXT NOT NULL, state_digest TEXT NOT NULL, hash TEXT NOT NULL
                );
                PRAGMA user_version = 1;
            """)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA synchronous = FULL")
        return db

    def initialize(self, goal: str) -> None:
        def init(state: State) -> None:
            if state:
                raise ForgeError("Council is already initialized")
            state.update(
                schema=1,
                goal=goal,
                tasks={},
                findings={},
                reviews={},
                evidence={},
                decisions=[],
                created_at=now(),
            )

        self.mutate("initialize", {"goal": goal}, init)

    def read(self) -> tuple[int, State]:
        with closing(self.connect()) as db:
            row = db.execute("SELECT revision, body FROM snapshot WHERE singleton = 1").fetchone()
        if row is None:
            raise ForgeError("Council initialization is incomplete")
        return row["revision"], json.loads(row["body"])

    def mutate(
        self,
        action: str,
        payload: Any,
        fn: Callable[[State], T],
        expected_revision: int | None = None,
    ) -> T:
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision, body FROM snapshot WHERE singleton = 1").fetchone()
            revision, state = (row["revision"], json.loads(row["body"])) if row else (0, {})
            last = db.execute(
                "SELECT state_digest FROM events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if row and (not last or last[0] != digest(state)):
                raise ForgeError("Snapshot digest does not match audit trail; refusing mutation")
            if expected_revision is not None and revision != expected_revision:
                raise ForgeError(
                    f"Stale council revision: expected {expected_revision}, got {revision}"
                )
            result = fn(state)
            body = canonical(state)
            if len(body.encode()) > 16 * 1024 * 1024:
                raise ForgeError("Council metadata exceeds 16 MiB; archive completed work")
            previous = db.execute(
                "SELECT hash FROM events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            event = {
                "sequence": revision + 1,
                "timestamp": now(),
                "action": action,
                "payload": canonical(payload),
                "previous_hash": previous[0] if previous else "",
                "state_digest": digest(state),
            }
            event_hash = digest(event)
            db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)", (*event.values(), event_hash)
            )
            db.execute("INSERT OR REPLACE INTO snapshot VALUES (1, ?, ?)", (revision + 1, body))
            db.commit()
            return result
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def events(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute("SELECT * FROM events ORDER BY sequence")]

    def verify(self) -> None:
        # One read transaction prevents concurrent commits from splitting this view.
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            previous = ""
            count = 0
            last_state = ""
            for index, record in enumerate(db.execute("SELECT * FROM events ORDER BY sequence"), 1):
                event = dict(record)
                claimed = event.pop("hash")
                if event["sequence"] != index or event["previous_hash"] != previous:
                    raise ForgeError("Audit trail sequence is broken")
                if digest(event) != claimed:
                    raise ForgeError("Audit trail hash mismatch")
                previous, count, last_state = claimed, index, event["state_digest"]
            row = db.execute("SELECT revision, body FROM snapshot WHERE singleton = 1").fetchone()
        if row is None or not count or row["revision"] != count:
            raise ForgeError("Snapshot revision does not match audit trail")
        if digest(json.loads(row["body"])) != last_state:
            raise ForgeError("Snapshot digest does not match audit trail")
