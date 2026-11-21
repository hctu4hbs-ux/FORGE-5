"""SQLite queue. Every transition is transactional and every completion is fenced."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterator
import uuid

from .model import Claim, Workflow, encode, positive

TERMINAL = frozenset({"succeeded", "failed", "blocked"})


class IdempotencyConflict(ValueError):
    """A submission key already names a different workflow definition."""


class Store:
    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        self.path = str(path)
        if self.path in ("", ":memory:"):
            raise ValueError("use a filesystem database for durable workflows")
        self.clock = clock
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY, submission_key TEXT NOT NULL UNIQUE,
                    fingerprint TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    workflow_id TEXT NOT NULL REFERENCES workflows(id),
                    id TEXT NOT NULL, handler TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','running','succeeded','failed','blocked')),
                    attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                    backoff REAL NOT NULL, available_at REAL NOT NULL,
                    token TEXT, lease_until REAL, result TEXT, error TEXT,
                    PRIMARY KEY(workflow_id,id)
                );
                CREATE TABLE IF NOT EXISTS dependencies (
                    workflow_id TEXT NOT NULL, job_id TEXT NOT NULL, dependency_id TEXT NOT NULL,
                    PRIMARY KEY(workflow_id,job_id,dependency_id),
                    FOREIGN KEY(workflow_id,job_id) REFERENCES jobs(workflow_id,id),
                    FOREIGN KEY(workflow_id,dependency_id) REFERENCES jobs(workflow_id,id)
                );
                CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state,available_at);
                CREATE INDEX IF NOT EXISTS dependencies_reverse ON dependencies(workflow_id,dependency_id);
            """)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def submit(self, workflow: Workflow, *, key: str) -> str:
        if not isinstance(key, str) or not key or len(key) > 500:
            raise ValueError("key must contain 1..500 characters")
        # Snapshot mutable caller payloads once so stored data and digest agree.
        canonical = encode(workflow.document())
        document = json.loads(canonical)
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        with self._transaction() as db:
            previous = db.execute("SELECT id,fingerprint FROM workflows WHERE submission_key=?", (key,)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise IdempotencyConflict(f"submission key {key!r} has different content")
                return previous["id"]
            id = uuid.uuid4().hex
            now = self.clock()
            db.execute("INSERT INTO workflows VALUES (?,?,?,?)", (id, key, fingerprint, now))
            for job in document["jobs"]:
                db.execute("""INSERT INTO jobs
                    (workflow_id,id,handler,payload,state,max_attempts,backoff,available_at)
                    VALUES (?,?,?,?,'pending',?,?,?)""",
                           (id, job["id"], job["handler"], encode(job["payload"]),
                            job["max_attempts"], job["backoff"], now))
            for job in document["jobs"]:
                db.executemany("INSERT INTO dependencies VALUES (?,?,?)",
                               [(id, job["id"], dep) for dep in job["depends_on"]])
            return id

    @staticmethod
    def _delay(row: sqlite3.Row) -> float:
        return min(86400.0, row["backoff"] * 2 ** (row["attempts"] - 1))

    def _recover(self, db: sqlite3.Connection, now: float) -> None:
        for row in db.execute("SELECT * FROM jobs WHERE state='running' AND lease_until<=?", (now,)).fetchall():
            state = "failed" if row["attempts"] >= row["max_attempts"] else "pending"
            db.execute("""UPDATE jobs SET state=?,available_at=?,token=NULL,lease_until=NULL,error=?
                          WHERE workflow_id=? AND id=?""",
                       (state, now + self._delay(row), "worker lease expired", row["workflow_id"], row["id"]))
        # Propagate through arbitrarily deep chains in the same transaction.
        while True:
            updated = db.execute("""UPDATE jobs SET state='blocked',error='dependency failed'
                WHERE state='pending' AND EXISTS (
                    SELECT 1 FROM dependencies d JOIN jobs parent
                    ON parent.workflow_id=d.workflow_id AND parent.id=d.dependency_id
                    WHERE d.workflow_id=jobs.workflow_id AND d.job_id=jobs.id
                    AND parent.state IN ('failed','blocked'))""").rowcount
            if not updated:
                return

    def recover(self) -> None:
        with self._transaction() as db:
            self._recover(db, self.clock())

    def claim(self, *, lease_seconds: float = 30, workflow_id: str | None = None) -> Claim | None:
        positive(lease_seconds, "lease_seconds")
        with self._transaction() as db:
            now = self.clock()
            self._recover(db, now)
            row = db.execute("""SELECT * FROM jobs WHERE state='pending' AND available_at<=?
                AND (? IS NULL OR workflow_id=?) AND NOT EXISTS (
                    SELECT 1 FROM dependencies d JOIN jobs parent
                    ON parent.workflow_id=d.workflow_id AND parent.id=d.dependency_id
                    WHERE d.workflow_id=jobs.workflow_id AND d.job_id=jobs.id AND parent.state!='succeeded')
                ORDER BY available_at,workflow_id,id LIMIT 1""", (now, workflow_id, workflow_id)).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            expires = now + lease_seconds
            db.execute("""UPDATE jobs SET state='running',attempts=attempts+1,token=?,lease_until=?
                WHERE workflow_id=? AND id=?""", (token, expires, row["workflow_id"], row["id"]))
            deps = db.execute("""SELECT parent.id,parent.result FROM dependencies d JOIN jobs parent
                ON parent.workflow_id=d.workflow_id AND parent.id=d.dependency_id
                WHERE d.workflow_id=? AND d.job_id=?""", (row["workflow_id"], row["id"])).fetchall()
            return Claim(row["workflow_id"], row["id"], row["handler"], json.loads(row["payload"]),
                         {dep["id"]: json.loads(dep["result"]) for dep in deps}, token,
                         row["attempts"] + 1, expires)

    def renew(self, claim: Claim, *, lease_seconds: float = 30) -> bool:
        positive(lease_seconds, "lease_seconds")
        with self._transaction() as db:
            now = self.clock()
            return bool(db.execute("""UPDATE jobs SET lease_until=? WHERE workflow_id=? AND id=?
                AND state='running' AND token=? AND lease_until>?""",
                (now + lease_seconds, claim.workflow_id, claim.job_id, claim.token, now)).rowcount)

    def succeed(self, claim: Claim, result: Any) -> bool:
        serialized = encode(result)
        with self._transaction() as db:
            return bool(db.execute("""UPDATE jobs SET state='succeeded',result=?,error=NULL,token=NULL,lease_until=NULL
                WHERE workflow_id=? AND id=? AND state='running' AND token=? AND lease_until>?""",
                (serialized, claim.workflow_id, claim.job_id, claim.token, self.clock())).rowcount)

    def fail(self, claim: Claim, error: str) -> bool:
        with self._transaction() as db:
            now = self.clock()
            row = db.execute("""SELECT * FROM jobs WHERE workflow_id=? AND id=?
                AND state='running' AND token=? AND lease_until>?""",
                (claim.workflow_id, claim.job_id, claim.token, now)).fetchone()
            if row is None:
                return False
            state = "failed" if row["attempts"] >= row["max_attempts"] else "pending"
            db.execute("""UPDATE jobs SET state=?,error=?,available_at=?,token=NULL,lease_until=NULL
                WHERE workflow_id=? AND id=?""",
                (state, str(error)[:4000], now + self._delay(row), claim.workflow_id, claim.job_id))
            self._recover(db, now)
            return True

    def snapshot(self, workflow_id: str) -> dict[str, Any]:
        with self._connection() as db:
            # One snapshot transaction keeps workflow status and job states consistent.
            db.execute("BEGIN")
            workflow = db.execute("SELECT id,submission_key,created_at FROM workflows WHERE id=?", (workflow_id,)).fetchone()
            if workflow is None:
                raise KeyError(workflow_id)
            jobs = [dict(row) for row in db.execute("""SELECT id,handler,state,attempts,max_attempts,
                available_at,lease_until,result,error FROM jobs WHERE workflow_id=? ORDER BY id""", (workflow_id,))]
            for job in jobs:
                if job["result"] is not None:
                    job["result"] = json.loads(job["result"])
            states = {job["state"] for job in jobs}
            state = "running" if not states <= TERMINAL else ("failed" if states & {"failed", "blocked"} else "succeeded")
            return {**dict(workflow), "state": state, "jobs": jobs}

    def unfinished(self, workflow_id: str | None = None) -> int:
        if workflow_id is not None:
            self.snapshot(workflow_id)  # Reject typos rather than reporting success.
        with self._connection() as db:
            return db.execute("""SELECT count(*) FROM jobs WHERE state IN ('pending','running')
                AND (? IS NULL OR workflow_id=?)""", (workflow_id, workflow_id)).fetchone()[0]
