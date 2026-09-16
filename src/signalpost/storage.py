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
                    recorded_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_fact_history_lookup
                ON fact_history(orgnr, field_name, as_of);

                CREATE INDEX IF NOT EXISTS idx_fact_history_recorded
                ON fact_history(orgnr, recorded_at);
                """
            )

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
                e.recorded_at.isoformat(),
            )
            for e in entries
        ]

        with conn:
            conn.executemany(
                """
                INSERT INTO fact_history (
                    orgnr, field_name, as_of, value_json, unit, source_name, source_url,
                    confidence, status, old_value_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )

    def get_fact_history(self, orgnr: str, limit: int = 100) -> list[FactHistoryEntry]:
        """Fetch audit log history for an organisation in descending chronological order."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT id, orgnr, field_name, as_of, value_json, unit, source_name,
                   source_url, confidence, status, old_value_json, recorded_at
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
                    recorded_at=datetime.fromisoformat(r["recorded_at"]),
                )
            )
        return results

    def close(self) -> None:
        """Close database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
