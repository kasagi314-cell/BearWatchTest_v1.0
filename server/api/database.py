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
            clock_offset_ms INTEGER,
            metrics_json TEXT
        );
        CREATE TABLE IF NOT EXISTS device_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            config_json TEXT NOT NULL,
            etag TEXT NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'UPLOADED',
            detected_at TEXT NOT NULL,
            clock_offset_ms INTEGER,
            camera TEXT,
            roi_json TEXT,
            azimuth_deg REAL,
            elevation_deg REAL,
            estimated_distance_m REAL,
            estimated_size_m REAL,
            track_json TEXT,
            env_json TEXT,
            scores_json TEXT DEFAULT '{"s3": null, "s4": null, "s5": null}',
            still_path TEXT,
            video_path TEXT,
            review_json TEXT,
            notified_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            command_type TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL,
            delivered_at TEXT
        );
        CREATE TABLE IF NOT EXISTS server_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket_hour TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(bucket_hour, metric_name)
        );
    """)
    # 既存 DB のマイグレーション（カラムが存在しない場合のみ追加）
    try:
        conn.execute("ALTER TABLE heartbeats ADD COLUMN metrics_json TEXT")
    except sqlite3.OperationalError:
        pass  # カラムが既に存在する
    conn.commit()
    conn.close()


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_device(db_path: str, device_id: str, device_info: dict | None = None) -> None:
    conn = sqlite3.connect(db_path)
    now = now_utc()
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
    metrics: dict | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO heartbeats
           (device_id, timestamp_utc, battery_pct, battery_temp_c, uptime_s,
            config_etag, clock_offset_ms, metrics_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (device_id, now_utc(), battery_pct, battery_temp_c, uptime_s,
         config_etag, clock_offset_ms,
         json.dumps(metrics) if metrics else None),
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


def insert_event(db_path: str, event_data: dict) -> str:
    """イベントを挿入する。既存なら 'existing'、新規なら 'created'。"""
    with sqlite3.connect(db_path) as conn:
        existing = conn.execute(
            "SELECT event_id FROM events WHERE event_id = ?",
            (event_data["event_id"],)
        ).fetchone()
        if existing:
            return "existing"

        now = now_utc()
        conn.execute(
            """INSERT INTO events
               (event_id, device_id, status, detected_at, clock_offset_ms,
                camera, roi_json, azimuth_deg, elevation_deg,
                estimated_distance_m, estimated_size_m,
                track_json, env_json, scores_json,
                created_at, updated_at)
               VALUES (?, ?, 'UPLOADED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_data["event_id"],
                event_data["device_id"],
                event_data["detected_at"],
                event_data.get("clock_offset_ms"),
                event_data.get("camera"),
                json.dumps(event_data.get("roi")),
                event_data.get("azimuth_deg"),
                event_data.get("elevation_deg"),
                event_data.get("estimated_distance_m"),
                event_data.get("estimated_size_m"),
                json.dumps(event_data.get("track")),
                json.dumps(event_data.get("env")),
                json.dumps(event_data.get("scores", {"s3": None, "s4": None, "s5": None})),
                now, now,
            ),
        )
        return "created"


def get_event(db_path: str, event_id: str) -> dict | None:
    """イベントを取得する。存在しなければ None。"""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        if row is None:
            return None
        return dict(row)


def update_event_status(db_path: str, event_id: str, status: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE events SET status = ?, updated_at = ? WHERE event_id = ?",
            (status, now_utc(), event_id),
        )


def update_event_scores(db_path: str, event_id: str, scores: dict) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE events SET scores_json = ?, updated_at = ? WHERE event_id = ?",
            (json.dumps(scores), now_utc(), event_id),
        )


def update_event_media_path(db_path: str, event_id: str, media_type: str, path: str) -> None:
    if media_type not in ("still", "video"):
        raise ValueError(f"Invalid media_type: {media_type!r}. Must be 'still' or 'video'.")
    col = "still_path" if media_type == "still" else "video_path"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE events SET {col} = ?, updated_at = ? WHERE event_id = ?",
            (path, now_utc(), event_id),
        )
