from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import DecisionCandidate, FactInput, PropertyIdentity


NAMESPACE = uuid.UUID("f02b47f8-0d61-4da4-a338-cfba0f4271d4")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_property_id(canonical_key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, canonical_key))


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class AssetCeoStore:
    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            uri = f"file:{self.path}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if not read_only:
            self.conn.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            self.conn.execute("BEGIN")
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def initialize_schema(self) -> None:
        if self.read_only:
            raise RuntimeError("Cannot initialize schema in read-only mode")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS properties (
                property_id TEXT PRIMARY KEY,
                canonical_key TEXT NOT NULL UNIQUE,
                address TEXT NOT NULL,
                city TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT '',
                zip_code TEXT NOT NULL DEFAULT '',
                llc TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                source_system TEXT NOT NULL DEFAULT '',
                source_ref TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS property_facts (
                fact_id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL REFERENCES properties(property_id),
                fact_name TEXT NOT NULL,
                value_json TEXT NOT NULL,
                effective_at TEXT,
                observed_at TEXT NOT NULL,
                source_system TEXT NOT NULL,
                source_ref TEXT NOT NULL DEFAULT '',
                confidence REAL,
                fact_hash TEXT NOT NULL,
                UNIQUE(property_id, fact_name, source_system, fact_hash)
            );
            CREATE INDEX IF NOT EXISTS ix_property_facts_latest
                ON property_facts(property_id, fact_name, observed_at DESC);

            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL REFERENCES properties(property_id),
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                source_system TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                dedupe_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'NEW'
            );

            CREATE TABLE IF NOT EXISTS decisions (
                decision_id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL REFERENCES properties(property_id),
                decision_type TEXT NOT NULL,
                title TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                authority_level TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                expected_annual_value REAL,
                expected_one_time_value REAL,
                confidence REAL,
                rationale TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                due_at TEXT,
                approved_at TEXT,
                executed_at TEXT,
                parent_event_id TEXT REFERENCES events(event_id),
                action_payload_json TEXT NOT NULL DEFAULT '{}',
                dedupe_key TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS decision_evidence (
                evidence_id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
                evidence_type TEXT NOT NULL,
                source TEXT NOT NULL,
                data_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS decision_outcomes (
                outcome_id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
                measured_at TEXT NOT NULL,
                realized_annual_value REAL,
                realized_one_time_value REAL,
                outcome TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS metric_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL REFERENCES properties(property_id),
                as_of TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL,
                source TEXT NOT NULL,
                UNIQUE(property_id, as_of, metric_name, source)
            );

            CREATE TABLE IF NOT EXISTS policies (
                policy_key TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT PRIMARY KEY,
                trigger_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                property_count INTEGER NOT NULL DEFAULT 0,
                decisions_created INTEGER NOT NULL DEFAULT 0,
                errors_json TEXT NOT NULL DEFAULT '[]'
            );
            """
        )
        self.conn.commit()

    def upsert_property(
        self,
        *,
        canonical_key: str,
        address: str,
        city: str = "",
        state: str = "",
        zip_code: str = "",
        llc: str = "",
        active: bool = True,
        source_system: str = "",
        source_ref: str = "",
    ) -> PropertyIdentity:
        if self.read_only:
            raise RuntimeError("Cannot write in read-only mode")
        property_id = stable_property_id(canonical_key)
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO properties (
                property_id, canonical_key, address, city, state, zip_code, llc,
                active, source_system, source_ref, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET
                address=excluded.address,
                city=excluded.city,
                state=excluded.state,
                zip_code=excluded.zip_code,
                llc=excluded.llc,
                active=excluded.active,
                source_system=excluded.source_system,
                source_ref=excluded.source_ref,
                updated_at=excluded.updated_at
            """,
            (property_id, canonical_key, address, city, state, zip_code, llc,
             int(active), source_system, source_ref, now, now),
        )
        self.conn.commit()
        return self.get_property(property_id)

    def get_property(self, property_id: str) -> PropertyIdentity:
        row = self.conn.execute("SELECT * FROM properties WHERE property_id=?", (property_id,)).fetchone()
        if not row:
            raise KeyError(property_id)
        return PropertyIdentity(
            property_id=row["property_id"], canonical_key=row["canonical_key"], address=row["address"],
            city=row["city"], state=row["state"], zip_code=row["zip_code"], llc=row["llc"],
            active=bool(row["active"]), source_system=row["source_system"], source_ref=row["source_ref"],
        )

    def list_properties(self, *, active_only: bool = True) -> list[PropertyIdentity]:
        sql = "SELECT property_id FROM properties"
        args: tuple[Any, ...] = ()
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY address, city, state"
        return [self.get_property(row[0]) for row in self.conn.execute(sql, args).fetchall()]

    def record_fact(self, property_id: str, fact: FactInput) -> bool:
        if self.read_only:
            raise RuntimeError("Cannot write in read-only mode")
        value_json = _json(fact.value)
        fact_hash = hashlib.sha256(
            f"{fact.fact_name}|{fact.source_system}|{fact.source_ref}|{fact.effective_at or ''}|{value_json}".encode()
        ).hexdigest()
        fact_id = str(uuid.uuid4())
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO property_facts (
                fact_id, property_id, fact_name, value_json, effective_at, observed_at,
                source_system, source_ref, confidence, fact_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (fact_id, property_id, fact.fact_name, value_json, fact.effective_at, utc_now(),
             fact.source_system, fact.source_ref, fact.confidence, fact_hash),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def latest_facts(self, property_id: str) -> dict[str, Any]:
        rows = self.conn.execute(
            """
            SELECT pf.fact_name, pf.value_json
            FROM property_facts pf
            JOIN (
                SELECT fact_name, MAX(observed_at) AS max_observed
                FROM property_facts
                WHERE property_id=?
                GROUP BY fact_name
            ) x ON x.fact_name=pf.fact_name AND x.max_observed=pf.observed_at
            WHERE pf.property_id=?
            """,
            (property_id, property_id),
        ).fetchall()
        result: dict[str, Any] = {}
        for row in rows:
            result[row["fact_name"]] = json.loads(row["value_json"])
        return result

    def record_metric(self, property_id: str, *, as_of: str, metric_name: str, value: float, unit: str, source: str) -> bool:
        if self.read_only:
            raise RuntimeError("Cannot write in read-only mode")
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO metric_snapshots
                (snapshot_id, property_id, as_of, metric_name, value, unit, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), property_id, as_of, metric_name, float(value), unit, source),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def create_decision(self, property_id: str, candidate: DecisionCandidate) -> str | None:
        if self.read_only:
            raise RuntimeError("Cannot write in read-only mode")
        decision_id = str(uuid.uuid4())
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO decisions (
                decision_id, property_id, decision_type, title, recommendation,
                authority_level, status, expected_annual_value, expected_one_time_value,
                confidence, rationale, created_at, due_at, parent_event_id,
                action_payload_json, dedupe_key
            ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id, property_id, candidate.decision_type, candidate.title,
                candidate.recommendation, candidate.authority_level,
                candidate.expected_annual_value, candidate.expected_one_time_value,
                candidate.confidence, candidate.rationale, utc_now(), candidate.due_at,
                candidate.parent_event_id, _json(candidate.action_payload), candidate.dedupe_key,
            ),
        )
        if cur.rowcount == 0:
            self.conn.commit()
            return None
        for evidence in candidate.evidence:
            self.conn.execute(
                """
                INSERT INTO decision_evidence (evidence_id, decision_id, evidence_type, source, data_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), decision_id,
                    str(evidence.get("evidence_type") or "fact"),
                    str(evidence.get("source") or "asset_ceo"),
                    _json(evidence.get("data") or evidence),
                ),
            )
        self.conn.commit()
        return decision_id

    def open_decisions(self, property_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM decisions WHERE property_id=? AND status='OPEN' ORDER BY created_at",
            (property_id,),
        ).fetchall()
