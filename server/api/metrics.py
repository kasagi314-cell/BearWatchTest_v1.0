"""サーバ側メトリクス記録"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from server.api.database import now_utc


def _current_bucket() -> str:
    """現在の 1 時間バケットを返す（例: '2026-08-20T12'）"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")


def record_metric(db_path: str, metric_name: str, increment: float = 1.0) -> None:
    """メトリクスを UPSERT でインクリメントする"""
    bucket = _current_bucket()
    now = now_utc()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO server_metrics (bucket_hour, metric_name, metric_value, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(bucket_hour, metric_name)
               DO UPDATE SET metric_value = metric_value + excluded.metric_value""",
            (bucket, metric_name, increment, now),
        )


def get_metrics(db_path: str, bucket_hour: str) -> dict[str, float]:
    """指定バケットの全メトリクスを取得する"""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT metric_name, metric_value FROM server_metrics WHERE bucket_hour = ?",
            (bucket_hour,),
        ).fetchall()
    return {name: value for name, value in rows}
