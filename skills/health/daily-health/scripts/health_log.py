#!/usr/bin/env python3
"""SQLite-backed health event logging and queries.

This is a library module — import it from wrapper scripts or tests.
DB location: HEALTH_DB_PATH env var, or ~/.hermes/health/health.db
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

_lock = threading.Lock()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS health_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    type TEXT NOT NULL,
    subtype TEXT,
    value REAL,
    unit TEXT,
    data TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_ts ON health_events(ts);
CREATE INDEX IF NOT EXISTS idx_type ON health_events(type);
CREATE INDEX IF NOT EXISTS idx_type_ts ON health_events(type, ts);
CREATE INDEX IF NOT EXISTS idx_source_ts ON health_events(source, ts);
CREATE UNIQUE INDEX IF NOT EXISTS idx_system_daily ON health_events(date(ts), type, source)
    WHERE source = 'system' AND type IN ('checkin', 'evening', 'nudge');
"""


def _db_path() -> Path:
    override = os.environ.get("HEALTH_DB_PATH")
    if override:
        return Path(override)
    hermes_home = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
    return hermes_home / "health" / "health.db"


def _get_conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        check_same_thread=False,
        timeout=5.0,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA_SQL)
    return conn


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_utc_start() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")


def log_event(
    type: str,
    subtype: str = None,
    source: str = "user",
    value: float = None,
    unit: str = None,
    data: dict = None,
    note: str = None,
) -> None:
    """Insert a health event. System events are deduplicated per day."""
    ts = _utc_now()
    data_str = json.dumps(data) if data else None
    conn = _get_conn()
    with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO health_events (ts, source, type, subtype, value, unit, data, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, source, type, subtype, value, unit, data_str, note),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            if source == "system":
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE health_events SET ts=?, data=?, note=? "
                    "WHERE date(ts)=date(?) AND type=? AND source='system'",
                    (ts, data_str, note, ts, type),
                )
                conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


def last_user_interaction() -> str | None:
    """Return UTC ISO timestamp of the most recent source='user' event, or None."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT ts FROM health_events WHERE source='user' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        return row["ts"] if row else None
    finally:
        conn.close()


def today_events() -> list[dict]:
    """Return all events for today (UTC day boundary)."""
    start = _today_utc_start()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM health_events WHERE ts >= ? ORDER BY ts", (start,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def check_morning_response() -> bool:
    """Return True if today's system checkin has a subsequent user response."""
    start = _today_utc_start()
    conn = _get_conn()
    try:
        checkin = conn.execute(
            "SELECT id FROM health_events WHERE ts >= ? AND type='checkin' AND source='system' LIMIT 1",
            (start,),
        ).fetchone()
        if not checkin:
            return False
        response = conn.execute(
            "SELECT 1 FROM health_events WHERE id > ? AND source='user' LIMIT 1",
            (checkin["id"],),
        ).fetchone()
        return response is not None
    finally:
        conn.close()


def events_range(days: int) -> list[dict]:
    """Return all events from the last N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT00:00:00Z"
    )
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM health_events WHERE ts >= ? ORDER BY ts", (cutoff,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
