"""コマンドキュー管理"""
from __future__ import annotations

import json
import sqlite3

from server.api.database import now_utc


def enqueue_command(db_path: str, device_id: str, command_type: str, payload: dict) -> int:
    """コマンドをキューに追加し、ID を返す"""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO commands (device_id, command_type, payload_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (device_id, command_type, json.dumps(payload), now_utc()),
        )
        return cur.lastrowid


def get_pending_commands(db_path: str, device_id: str) -> list[dict]:
    """未配信コマンドを取得する。ハートビート応答に含める形式で返す。"""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, command_type, payload_json FROM commands
               WHERE device_id = ? AND delivered_at IS NULL
               ORDER BY id""",
            (device_id,),
        ).fetchall()

    result = []
    for cmd_id, cmd_type, payload_json in rows:
        payload = json.loads(payload_json) if payload_json else {}
        result.append({"id": cmd_id, "type": cmd_type, **payload})
    return result


def mark_delivered(db_path: str, command_ids: list[int]) -> None:
    """コマンドを配信済みにする"""
    if not command_ids:
        return
    now = now_utc()
    placeholders = ",".join("?" for _ in command_ids)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE commands SET delivered_at = ? WHERE id IN ({placeholders})",
            [now, *command_ids],
        )
