"""Transactional private evidence storage and conservative per-account quota reservations."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Mapping

from ..errors import InputError
from .imports import load_evidence, snapshot_id
from .validation import WATCH_SCHEMA, canonical, digest, now_stamp, read_json, watch

APPLICATION_ID = 0x4B574D31
DDL = """
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE acknowledged(id TEXT PRIMARY KEY, acknowledged_at TEXT NOT NULL);
CREATE TABLE watches(id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE watch_versions(hash TEXT PRIMARY KEY, watch_id TEXT NOT NULL,
                            payload TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TABLE snapshots(id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, path TEXT NOT NULL,
                       raw BLOB, imported_at TEXT NOT NULL, context TEXT NOT NULL);
CREATE TABLE observations(id TEXT PRIMARY KEY, identity TEXT NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX observations_identity_date ON observations(identity, observed_at);
CREATE TABLE plan_runs(plan_sha TEXT NOT NULL, watch_id TEXT NOT NULL,
    attempt_id INTEGER NOT NULL, PRIMARY KEY(plan_sha,watch_id));
CREATE TABLE attempts(id INTEGER PRIMARY KEY, watch_id TEXT NOT NULL,
    account TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
    state TEXT NOT NULL, reserved_requests INTEGER NOT NULL, error_kind TEXT);
CREATE INDEX attempts_watch_time ON attempts(watch_id, started_at);
CREATE TABLE quotas(account TEXT NOT NULL, day TEXT NOT NULL, runs INTEGER NOT NULL,
    requests INTEGER NOT NULL, run_cap INTEGER NOT NULL, request_cap INTEGER NOT NULL,
    PRIMARY KEY(account, day));
"""


class MonitorStore:
    def __init__(self, path: Path, *, create: bool = False) -> None:
        self.path = Path(path)
        if str(self.path) == ":memory:":
            raise InputError("Monitoring requires a persistent file, not an in-memory store.")
        if not self.path.exists() and not create:
            raise InputError("Monitor store does not exist; use monitor init first.")
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        app = self.db.execute("PRAGMA application_id").fetchone()[0]
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if app == 0 and version == 0 and not tables and create:
            with self.db:
                self.db.executescript(DDL)
                self.db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                self.db.execute("PRAGMA user_version=1")
                self.db.execute("INSERT INTO settings VALUES ('store_id',?)", (str(uuid.uuid4()),))
        elif app != APPLICATION_ID or version != 1:
            self.db.close()
            raise InputError("Unsupported monitor database; no migration or overwrite was performed.")

    @property
    def identifier(self) -> str:
        return self.db.execute("SELECT value FROM settings WHERE key='store_id'").fetchone()[0]

    def __enter__(self) -> MonitorStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.db.close()

    def add_watchlist(self, path: Path, *, now: str | None = None) -> dict[str, Any]:
        _, payload = read_json(path)
        if (not isinstance(payload, dict) or payload.get("schema") != WATCH_SCHEMA
                or not isinstance(payload.get("watches"), list)
                or not 1 <= len(payload["watches"]) <= 50000):
            raise InputError("Use keywordmoves-watchlist/v1 with 1..50000 watches.")
        rows = [watch(row) for row in payload["watches"]]
        if len({row["id"] for row in rows}) != len(rows):
            raise InputError("Watch IDs must be unique within a manifest.")
        changed = 0
        timestamp = now_stamp(now)
        with self.db:
            for row in rows:
                text = canonical(row)
                previous = self.db.execute("SELECT payload FROM watches WHERE id=?",
                                           (row["id"],)).fetchone()
                if previous is not None and previous["payload"] == text:
                    continue
                self.db.execute("INSERT OR REPLACE INTO watches VALUES (?,?,?)",
                                (row["id"], text, timestamp))
                self.db.execute("INSERT OR IGNORE INTO watch_versions VALUES (?,?,?,?)",
                                (digest(row), row["id"], text, timestamp))
                changed += 1
        return {"watches_received": len(rows), "watches_changed": changed,
                "history_preserved": True}

    def watches(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        rows = [json.loads(row["payload"]) for row in
                self.db.execute("SELECT payload FROM watches ORDER BY id")]
        return [row for row in rows if row["active"] or not active_only]

    def observations(self, *, as_of: str | None = None) -> list[dict[str, Any]]:
        return [json.loads(row["payload"]) for row in self.db.execute(
            "SELECT payload FROM observations WHERE observed_at<=? ORDER BY observed_at,id",
            (now_stamp(as_of),))]

    def ingest(self, path: Path, defaults: Mapping[str, Any] | None = None,
               *, now: str | None = None) -> dict[str, Any]:
        raw, sha, rows = load_evidence(path, defaults, now=now)
        return self.save_snapshot(raw, sha, rows, str(path), defaults, now=now)

    def save_snapshot(self, raw: bytes, sha: str, rows: list[dict[str, Any]], path: str,
                      defaults: Mapping[str, Any] | None = None,
                      *, now: str | None = None) -> dict[str, Any]:
        if hashlib.sha256(raw).hexdigest() != sha:
            raise InputError("Snapshot bytes do not match their SHA-256.")
        identifier = snapshot_id(sha, defaults)
        inserted = 0
        with self.db:
            exists = self.db.execute("SELECT id FROM snapshots WHERE id=?", (identifier,)).fetchone()
            if exists:
                return {"snapshot": identifier, "sha256": sha, "inserted": 0,
                        "received": len(rows), "duplicate_snapshot": True}
            self.db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?)",
                            (identifier, sha, path, raw, now_stamp(now),
                             canonical(dict(defaults or {}))))
            for row in rows:
                cur = self.db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?)",
                                     (row["id"], row["identity"], identifier,
                                      row["observed_at"], canonical(row)))
                inserted += cur.rowcount
        return {"snapshot": identifier, "sha256": sha, "inserted": inserted,
                "received": len(rows), "duplicate_snapshot": False}

    def attempts(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM attempts ORDER BY id")]

    def reserve(self, watch_id: str, account: str, *, request_count: int,
                daily_runs: int, daily_requests: int, now: str | None = None,
                plan_sha: str | None = None) -> int:
        """Reserve the worst case before a call. Failed/uncertain calls never refund."""
        for value in (request_count, daily_runs, daily_requests):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100000:
                raise InputError("Quota bounds must be positive finite integers.")
        timestamp = now_stamp(now)
        day = timestamp[:10]
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if plan_sha and self.db.execute(
                    "SELECT attempt_id FROM plan_runs WHERE plan_sha=? AND watch_id=?",
                    (plan_sha, watch_id)).fetchone():
                raise InputError("This exact plan entry was already attempted; inspect health before replanning.")
            if self.db.execute(
                    "SELECT id FROM attempts WHERE watch_id=? AND state='running'",
                    (watch_id,)).fetchone():
                raise InputError("A collection for this watch is already running; recover it explicitly.")
            quota = self.db.execute("SELECT * FROM quotas WHERE account=? AND day=?",
                                    (account, day)).fetchone()
            if quota:
                daily_runs = min(daily_runs, quota["run_cap"])
                daily_requests = min(daily_requests, quota["request_cap"])
                runs, requests = quota["runs"], quota["requests"]
            else:
                runs = requests = 0
            if runs + 1 > daily_runs or requests + request_count > daily_requests:
                raise InputError("The shared account's UTC-day monitoring quota is exhausted.")
            self.db.execute("INSERT OR REPLACE INTO quotas VALUES (?,?,?,?,?,?)",
                            (account, day, runs + 1, requests + request_count,
                             daily_runs, daily_requests))
            cur = self.db.execute(
                "INSERT INTO attempts(watch_id,account,started_at,state,reserved_requests) "
                "VALUES (?,?,?,'running',?)", (watch_id, account, timestamp, request_count))
            identifier = int(cur.lastrowid)
            if plan_sha:
                self.db.execute("INSERT INTO plan_runs VALUES (?,?,?)",
                                (plan_sha, watch_id, identifier))
            return identifier

    def finish(self, attempt: int, state: str, *, error_kind: str | None = None,
               now: str | None = None) -> None:
        if state not in {"success", "no-data", "error", "interrupted"}:
            raise InputError("Unknown collection completion state.")
        with self.db:
            cur = self.db.execute(
                "UPDATE attempts SET state=?,ended_at=?,error_kind=? WHERE id=? AND state='running'",
                (state, now_stamp(now), error_kind, attempt))
            if cur.rowcount != 1:
                raise InputError("Only an existing running collection can be finished.")

    def health(self) -> dict[str, Any]:
        return {
            "schema": "keywordmoves-monitor-health/v1", "attempts": self.attempts(),
            "quotas": [dict(row) for row in self.db.execute("SELECT * FROM quotas ORDER BY day,account")],
            "limits": "Reservations cover this store and account label only; other clients are external.",
        }

    def verify(self) -> dict[str, Any]:
        failures = []
        for row in self.db.execute("SELECT id,sha256,raw FROM snapshots WHERE raw IS NOT NULL"):
            if hashlib.sha256(row["raw"]).hexdigest() != row["sha256"]:
                failures.append(row["id"])
        payload_failures = []
        for row in self.db.execute("SELECT id,payload FROM observations"):
            try:
                value = json.loads(row["payload"])
                valid = (isinstance(value, dict) and value.get("id") == row["id"] and digest(
                    {key: item for key, item in value.items() if key != "id"}) == row["id"])
            except (ValueError, TypeError):
                valid = False
            if not valid:
                payload_failures.append(row["id"])
        check = self.db.execute("PRAGMA quick_check").fetchone()[0]
        return {"sqlite_check": check, "snapshot_hash_failures": failures,
                "observation_hash_failures": payload_failures,
                "raw_snapshots_checked": self.db.execute(
                    "SELECT count(*) FROM snapshots WHERE raw IS NOT NULL").fetchone()[0],
                "raw_snapshots_pruned": self.db.execute(
                    "SELECT count(*) FROM snapshots WHERE raw IS NULL").fetchone()[0],
                "verified": check == "ok" and not failures and not payload_failures}

    def prune(self, before: str, *, platform: str | None = None) -> dict[str, int]:
        cutoff = now_stamp(before)
        ids = [row["id"] for row in self.observations()
               if row["observed_at"] < cutoff
               and (platform is None or row["platform"].casefold() == platform.casefold())]
        self.db.execute("PRAGMA secure_delete=ON")
        with self.db:
            snapshots = [self.db.execute("SELECT snapshot_id FROM observations WHERE id=?",
                                         (item,)).fetchone()[0] for item in ids]
            # Mixed snapshots cannot keep deleted rows in their original bytes.
            self.db.executemany("UPDATE snapshots SET raw=NULL WHERE id=?", [(s,) for s in snapshots])
            self.db.executemany("DELETE FROM observations WHERE id=?", [(item,) for item in ids])
            deleted = self.db.execute(
                "DELETE FROM snapshots WHERE id NOT IN (SELECT snapshot_id FROM observations)").rowcount
        return {"observations_deleted": len(ids), "orphan_snapshots_deleted": deleted,
                "source_snapshots_redacted": len(set(snapshots))}
