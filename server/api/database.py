from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY,
            name TEXT,
            device_info TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS heartbeats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            timestamp_utc TEXT NOT NULL,
            battery_pct INTEGER,
            battery_temp_c REAL,
            uptime_s INTEGER,
            config_etag TEXT,
            clock_offset_ms INTEGER
        );
        CREATE TABLE IF NOT EXISTS device_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            config_json TEXT NOT NULL,
            etag TEXT NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
    """)
    conn.close()


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_device(db_path: str, device_id: str, device_info: dict | None = None) -> None:
    conn = sqlite3.connect(db_path)
    now = _now_utc()
    existing = conn.execute("SELECT id FROM devices WHERE id = ?", (device_id,)).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO devices (id, device_info, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (device_id, json.dumps(device_info) if device_info else None, now, now),
        )
    elif device_info is not None:
        conn.execute(
            "UPDATE devices SET device_info = ?, updated_at = ? WHERE id = ?",
            (json.dumps(device_info), now, device_id),
        )
    conn.commit()
    conn.close()


def record_heartbeat(
    db_path: str, device_id: str,
    battery_pct: int, battery_temp_c: float, uptime_s: int,
    config_etag: str, clock_offset_ms: int,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO heartbeats
           (device_id, timestamp_utc, battery_pct, battery_temp_c, uptime_s, config_etag, clock_offset_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (device_id, _now_utc(), battery_pct, battery_temp_c, uptime_s, config_etag, clock_offset_ms),
    )
    conn.commit()
    conn.close()


def get_active_config(db_path: str, device_id: str) -> tuple[dict, str] | None:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT config_json, etag FROM device_configs WHERE device_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
        (device_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return json.loads(row[0]), row[1]
