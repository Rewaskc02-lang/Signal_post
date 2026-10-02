"""Persistence layer for Signalpost profiles and historical fact logging."""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from signalpost.config import settings as default_settings
from signalpost.models import CompanyProfile


class FactHistoryEntry(BaseModel):
    """Represents an audit entry in the fact_history table."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    orgnr: str
    field_name: str
    as_of: str | None = None
    value_json: str
    unit: str | None = None
    source_name: str
    source_url: str
    confidence: str
    status: str  # "new", "changed", "confirmed"
    old_value_json: str | None = None
    content_hash: str | None = None
    extraction_method: str | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Storage:
    """SQLite persistence manager for profiles and fact change audit logs."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or default_settings.sqlite_db_path
        self._conn: sqlite3.Connection | None = None
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def init_db(self) -> None:
        """Run migrations and ensure required tables and indexes exist."""
        conn = self._get_connection()
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    orgnr TEXT PRIMARY KEY,
                    profile_json TEXT NOT NULL,
                    last_checked TEXT NOT NULL,
                    last_changed TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fact_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    orgnr TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    as_of TEXT,
                    value_json TEXT NOT NULL,
                    unit TEXT,
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    status TEXT NOT NULL,
                    old_value_json TEXT,
                    content_hash TEXT,
                    extraction_method TEXT,
                    recorded_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_fact_history_lookup
                ON fact_history(orgnr, field_name, as_of);

                CREATE INDEX IF NOT EXISTS idx_fact_history_recorded
                ON fact_history(orgnr, recorded_at);

                CREATE TABLE IF NOT EXISTS source_snapshots (
                    url TEXT NOT NULL,
                    content_hash TEXT PRIMARY KEY,
                    status_code INTEGER NOT NULL,
                    content_type TEXT,
                    response_body TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_source_snapshots_url
                ON source_snapshots(url);
                """
            )

            # Check and migrate columns if upgrading from earlier version
            cursor = conn.execute("PRAGMA table_info(fact_history)")
            columns = {row["name"] for row in cursor.fetchall()}
            if "content_hash" not in columns:
                conn.execute("ALTER TABLE fact_history ADD COLUMN content_hash TEXT")
            if "extraction_method" not in columns:
                conn.execute("ALTER TABLE fact_history ADD COLUMN extraction_method TEXT")

    def get_profile(self, orgnr: str) -> CompanyProfile | None:
        """Retrieve the latest stored CompanyProfile for an organisation."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT profile_json FROM profiles WHERE orgnr = ?",
            (orgnr,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        data = json.loads(row["profile_json"])
        return CompanyProfile.model_validate(data)

    def save_profile(self, profile: CompanyProfile) -> None:
        """Upsert a CompanyProfile into the profiles table."""
        conn = self._get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        last_checked_iso = (
            profile.last_checked.isoformat()
            if profile.last_checked
            else now_iso
        )
        last_changed_iso = (
            profile.last_changed.isoformat()
            if profile.last_changed
            else None
        )
        profile_json = profile.model_dump_json()

        with conn:
            conn.execute(
                """
                INSERT INTO profiles (orgnr, profile_json, last_checked, last_changed, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(orgnr) DO UPDATE SET
                    profile_json = excluded.profile_json,
                    last_checked = excluded.last_checked,
                    last_changed = excluded.last_changed,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.orgnr,
                    profile_json,
                    last_checked_iso,
                    last_changed_iso,
                    now_iso,
                    now_iso,
                ),
            )

    def record_fact_history(self, entries: list[FactHistoryEntry]) -> None:
        """Batch-insert fact history entries for auditability and diff tracking."""
        if not entries:
            return

        conn = self._get_connection()
        params = [
            (
                e.orgnr,
                e.field_name,
                e.as_of,
                e.value_json,
                e.unit,
                e.source_name,
                e.source_url,
                e.confidence,
                e.status,
                e.old_value_json,
                e.content_hash,
                e.extraction_method,
                e.recorded_at.isoformat(),
            )
            for e in entries
        ]

        with conn:
            conn.executemany(
                """
                INSERT INTO fact_history (
                    orgnr, field_name, as_of, value_json, unit,
                    source_name, source_url, confidence, status,
                    old_value_json, content_hash, extraction_method, recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )

    def get_fact_history(self, orgnr: str, limit: int = 100) -> list[FactHistoryEntry]:
        """Fetch audit log history for an organisation in descending chronological order."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT id, orgnr, field_name, as_of, value_json, unit, source_name,
                   source_url, confidence, status, old_value_json, content_hash,
                   extraction_method, recorded_at
            FROM fact_history
            WHERE orgnr = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (orgnr, limit),
        )
        results: list[FactHistoryEntry] = []
        for r in cursor.fetchall():
            results.append(
                FactHistoryEntry(
                    id=r["id"],
                    orgnr=r["orgnr"],
                    field_name=r["field_name"],
                    as_of=r["as_of"],
                    value_json=r["value_json"],
                    unit=r["unit"],
                    source_name=r["source_name"],
                    source_url=r["source_url"],
                    confidence=r["confidence"],
                    status=r["status"],
                    old_value_json=r["old_value_json"],
                    content_hash=r["content_hash"],
                    extraction_method=r["extraction_method"],
                    recorded_at=datetime.fromisoformat(r["recorded_at"]),
                )
            )
        return results

    def save_snapshot(
        self,
        url: str,
        content_hash: str,
        status_code: int,
        content_type: str | None,
        response_body: str,
        retrieved_at: str,
    ) -> None:
        """Persist a raw source HTTP response snapshot for auditability and verification."""
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO source_snapshots (url, content_hash, status_code, content_type, response_body, retrieved_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    url = excluded.url,
                    response_body = excluded.response_body,
                    retrieved_at = excluded.retrieved_at
                """,
                (url, content_hash, status_code, content_type or "application/json", response_body, retrieved_at),
            )

    def get_snapshot(self, content_hash: str) -> dict[str, Any] | None:
        """Retrieve a raw source snapshot by its content hash."""
        conn = self._get_connection()
        cur = conn.execute("SELECT * FROM source_snapshots WHERE content_hash = ?", (content_hash,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_snapshot_by_url(self, url: str) -> dict[str, Any] | None:
        """Retrieve the latest raw source snapshot by its URL."""
        conn = self._get_connection()
        cur = conn.execute("SELECT * FROM source_snapshots WHERE url = ? ORDER BY retrieved_at DESC LIMIT 1", (url,))
        row = cur.fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        """Close database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
