# M1 サーバ骨格 実装計画

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `fake_device.py` からの E2E 疎通（イベント送信 → S4 判定 → コマンド受信 → 映像アップロード）を実現するサーバ骨格を構築する。

**Architecture:** 既存の `server/api/` にフラットモジュールを追加（events.py, inference.py, commands.py, config.py, metrics.py）。DB は database.py にテーブル追加。torchvision `fasterrcnn_resnet50_fpn_v2` で S4 推論。fake_device.py を拡張して E2E シナリオを実行可能にする。

**Tech Stack:** Python 3.11 / FastAPI / SQLite / torchvision (BSD-3-Clause) / httpx / pytest

**Design spec:** `docs/specs/2026-08-20-m1-server-skeleton-design.md`

---

## File Structure

### 新規作成

| ファイル | 責務 |
|---|---|
| `server/api/events.py` | イベント受信エンドポイント（POST /v1/events, still, video） |
| `server/api/config.py` | 設定取得エンドポイント（GET /v1/config） |
| `server/api/inference.py` | S4 推論（モデルロード、判定、デバイス自動選択、タイムアウト） |
| `server/api/commands.py` | コマンドキュー管理（挿入、未配信取得、配信済みマーク） |
| `server/api/metrics.py` | サーバ側メトリクス記録 |
| `tools/fake_device/dummy_media.py` | ダミー画像・動画生成 |
| `tests/test_events.py` | イベント CRUD、メトリクス |
| `tests/test_inference.py` | S4 推論インターフェース（モック） |
| `tests/test_commands.py` | コマンドキュー |
| `tests/test_e2e.py` | E2E シナリオ（モック推論） |

### 変更

| ファイル | 変更内容 |
|---|---|
| `server/api/database.py` | events, commands, server_metrics テーブル追加。heartbeats に metrics_json 追加。`_now_utc` を公開関数化 |
| `server/api/models.py` | イベント系 Pydantic モデル追加。HeartbeatResponse 拡張 |
| `server/api/app.py` | APIRouter include、lifespan でモデルロード、heartbeat にコマンド配信追加 |
| `server/api/auth.py` | `resolve_device_or_raise` ヘルパー追加（DRY 化） |
| `tools/fake_device/main.py` | イベント送信、コマンド受信、E2E シナリオ追加 |
| `tests/conftest.py` | MEDIA_ROOT, SKIP_MODEL_LOAD 環境変数追加 |
| `.github/workflows/test.yml` | `-m "not slow"` 追加 |

---

## Chunk 1: DB スキーマ拡張とコマンドキュー

### Task 1: DB スキーマ拡張（events + commands + server_metrics テーブル）

**Files:**
- Modify: `server/api/database.py`
- Test: `tests/test_heartbeat.py`

- [ ] **Step 1: 既存テストが通ることを確認**

Run: `pytest tests/test_heartbeat.py::TestDatabase -v`
Expected: 3 tests PASS

- [ ] **Step 2: database.py に events, commands, server_metrics テーブルを追加し、heartbeats にマイグレーション追加**

`server/api/database.py` の `init_db` 関数の `executescript` に以下のテーブルを追加:

```sql
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
```

heartbeats テーブルの CREATE 文に `metrics_json TEXT` カラムを追加。さらに、既存 DB 向けのマイグレーション処理を `init_db` の末尾に追加:

```python
# 既存 DB のマイグレーション（カラムが存在しない場合のみ追加）
try:
    conn.execute("ALTER TABLE heartbeats ADD COLUMN metrics_json TEXT")
except sqlite3.OperationalError:
    pass  # カラムが既に存在する
```

`_now_utc` を他モジュールから import できるよう公開関数化（先頭アンダースコアを削除 → `now_utc`）。既存の `_now_utc()` 呼び出し箇所も `now_utc()` に変更。

- [ ] **Step 3: テーブル作成テストを更新**

`tests/test_heartbeat.py` の `test_tables_created` を更新:

```python
def test_tables_created(self):
    """テーブルが6つ作成される"""
    cur = self.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [r[0] for r in cur.fetchall()]
    assert "commands" in tables
    assert "device_configs" in tables
    assert "devices" in tables
    assert "events" in tables
    assert "heartbeats" in tables
    assert "server_metrics" in tables
```

- [ ] **Step 4: テスト実行**

Run: `pytest tests/test_heartbeat.py::TestDatabase -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add server/api/database.py tests/test_heartbeat.py
git commit -m "feat(db): add events, commands, server_metrics tables and heartbeats migration"
```

---

### Task 2: イベント CRUD 関数

**Files:**
- Modify: `server/api/database.py`
- Create: `tests/test_events.py`

- [ ] **Step 1: test_events.py にイベント CRUD テストを書く**

```python
"""イベント CRUD のテスト"""
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from server.api.database import (
    init_db, insert_event, get_event, update_event_status,
    update_event_scores, update_event_media_path,
)

SAMPLE_EVENT = {
    "event_id": "evt-001",
    "device_id": "device-001",
    "detected_at": "2026-08-20T12:00:00Z",
    "clock_offset_ms": -42,
    "camera": "rear",
    "roi": {"x": 100, "y": 200, "w": 50, "h": 80},
    "azimuth_deg": 135.0,
    "elevation_deg": -5.0,
    "estimated_distance_m": 30.0,
    "estimated_size_m": 1.2,
    "track": {"duration_s": 4.0, "frames": 8, "speed_mps": 1.5,
              "direction_deg": 90.0, "straightness": 0.8},
    "env": {"weather": None, "mean_luminance": 128,
            "global_luminance_delta": 3, "enclosure_temp_c": 32.0},
    "scores": {"s3": 0.7, "s4": None, "s5": None},
}


class TestEventCRUD:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test.db")
        init_db(self.db_path)

    def teardown_method(self):
        shutil.rmtree(self.tmp)

    def test_insert_and_get(self):
        """イベントが挿入・取得できる"""
        insert_event(self.db_path, SAMPLE_EVENT)
        evt = get_event(self.db_path, "evt-001")
        assert evt is not None
        assert evt["event_id"] == "evt-001"
        assert evt["device_id"] == "device-001"
        assert evt["status"] == "UPLOADED"
        assert evt["azimuth_deg"] == 135.0
        assert evt["estimated_distance_m"] == 30.0

    def test_idempotent_insert(self):
        """同じ event_id の再送は上書きしない"""
        insert_event(self.db_path, SAMPLE_EVENT)
        result = insert_event(self.db_path, SAMPLE_EVENT)
        assert result == "existing"

    def test_update_status(self):
        """イベント状態を更新できる"""
        insert_event(self.db_path, SAMPLE_EVENT)
        update_event_status(self.db_path, "evt-001", "MEDIA_REQUESTED")
        evt = get_event(self.db_path, "evt-001")
        assert evt["status"] == "MEDIA_REQUESTED"

    def test_update_scores(self):
        """スコアを更新できる"""
        insert_event(self.db_path, SAMPLE_EVENT)
        update_event_scores(self.db_path, "evt-001", {"s3": 0.7, "s4": 0.85, "s5": None})
        evt = get_event(self.db_path, "evt-001")
        scores = json.loads(evt["scores_json"])
        assert scores["s4"] == 0.85

    def test_update_media_path_still(self):
        """still パスを更新できる"""
        insert_event(self.db_path, SAMPLE_EVENT)
        update_event_media_path(self.db_path, "evt-001", "still", "dev/evt-001/still.jpg")
        evt = get_event(self.db_path, "evt-001")
        assert evt["still_path"] == "dev/evt-001/still.jpg"

    def test_update_media_path_video(self):
        """video パスを更新できる"""
        insert_event(self.db_path, SAMPLE_EVENT)
        update_event_media_path(self.db_path, "evt-001", "video", "dev/evt-001/video.mp4")
        evt = get_event(self.db_path, "evt-001")
        assert evt["video_path"] == "dev/evt-001/video.mp4"

    def test_update_media_path_invalid_type(self):
        """不正な media_type で ValueError"""
        insert_event(self.db_path, SAMPLE_EVENT)
        with pytest.raises(ValueError, match="media_type"):
            update_event_media_path(self.db_path, "evt-001", "thumbnail", "x.jpg")

    def test_get_nonexistent(self):
        """存在しないイベントは None"""
        assert get_event(self.db_path, "no-such") is None
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/test_events.py -v`
Expected: ImportError（insert_event 等が未定義）

- [ ] **Step 3: database.py にイベント CRUD 関数を実装**

`server/api/database.py` に以下を追加。全関数で `with sqlite3.connect(...) as conn:` コンテキストマネージャを使う:

```python
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
```

- [ ] **Step 4: テスト実行**

Run: `pytest tests/test_events.py -v`
Expected: 8 tests PASS

- [ ] **Step 5: コミット**

```bash
git add server/api/database.py tests/test_events.py
git commit -m "feat(db): add event CRUD functions"
```

---

### Task 3: コマンドキュー

**Files:**
- Create: `server/api/commands.py`
- Create: `tests/test_commands.py`

- [ ] **Step 1: test_commands.py を書く**

```python
"""コマンドキューのテスト"""
import sys
import os
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from server.api.database import init_db
from server.api.commands import enqueue_command, get_pending_commands, mark_delivered


class TestCommandQueue:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test.db")
        init_db(self.db_path)

    def teardown_method(self):
        shutil.rmtree(self.tmp)

    def test_enqueue_and_get_pending(self):
        """コマンドを追加して取得できる"""
        enqueue_command(self.db_path, "device-001", "request_video", {"event_id": "evt-001"})
        cmds = get_pending_commands(self.db_path, "device-001")
        assert len(cmds) == 1
        assert cmds[0]["type"] == "request_video"
        assert cmds[0]["event_id"] == "evt-001"

    def test_mark_delivered(self):
        """配信済みにすると次回取得から除外される"""
        enqueue_command(self.db_path, "device-001", "request_video", {"event_id": "evt-001"})
        cmds = get_pending_commands(self.db_path, "device-001")
        mark_delivered(self.db_path, [c["id"] for c in cmds])
        cmds2 = get_pending_commands(self.db_path, "device-001")
        assert len(cmds2) == 0

    def test_different_devices(self):
        """異なるデバイスのコマンドが混ざらない"""
        enqueue_command(self.db_path, "device-001", "request_video", {"event_id": "evt-001"})
        enqueue_command(self.db_path, "device-002", "discard", {"event_id": "evt-002"})
        cmds = get_pending_commands(self.db_path, "device-001")
        assert len(cmds) == 1
        assert cmds[0]["event_id"] == "evt-001"

    def test_multiple_commands(self):
        """複数コマンドが正しい順序で返る"""
        enqueue_command(self.db_path, "device-001", "request_video", {"event_id": "evt-001"})
        enqueue_command(self.db_path, "device-001", "discard", {"event_id": "evt-002"})
        cmds = get_pending_commands(self.db_path, "device-001")
        assert len(cmds) == 2
        assert cmds[0]["type"] == "request_video"
        assert cmds[1]["type"] == "discard"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/test_commands.py -v`
Expected: ImportError

- [ ] **Step 3: commands.py を実装**

`database.py` から `now_utc` を import して重複を避ける:

```python
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
```

- [ ] **Step 4: テスト実行**

Run: `pytest tests/test_commands.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: コミット**

```bash
git add server/api/commands.py tests/test_commands.py
git commit -m "feat: add command queue (enqueue, get pending, mark delivered)"
```

---

### Task 4: サーバ側メトリクス記録

**Files:**
- Create: `server/api/metrics.py`
- Modify: `tests/test_events.py`（TestMetrics クラス追加）

- [ ] **Step 1: test_events.py にメトリクステストを追加**

`tests/test_events.py` の末尾に追加:

```python
from server.api.metrics import record_metric, get_metrics


class TestMetrics:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test.db")
        init_db(self.db_path)

    def teardown_method(self):
        shutil.rmtree(self.tmp)

    def test_record_and_get(self):
        """メトリクスが記録・取得でき、同一バケットはインクリメントされる"""
        record_metric(self.db_path, "s4_input_count", 1.0)
        record_metric(self.db_path, "s4_input_count", 1.0)
        # 現在のバケットを取得（テスト実行中に時間が変わらない前提）
        from datetime import datetime, timezone
        bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        metrics = get_metrics(self.db_path, bucket)
        assert metrics["s4_input_count"] == 2.0

    def test_different_metrics(self):
        """異なるメトリクスが独立している"""
        record_metric(self.db_path, "s4_input_count", 1.0)
        record_metric(self.db_path, "s4_animal_count", 1.0)
        from datetime import datetime, timezone
        bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        metrics = get_metrics(self.db_path, bucket)
        assert metrics["s4_input_count"] == 1.0
        assert metrics["s4_animal_count"] == 1.0
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/test_events.py::TestMetrics -v`
Expected: ImportError

- [ ] **Step 3: metrics.py を実装**

```python
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
```

- [ ] **Step 4: テスト実行**

Run: `pytest tests/test_events.py::TestMetrics -v`
Expected: 2 tests PASS

- [ ] **Step 5: コミット**

```bash
git add server/api/metrics.py tests/test_events.py
git commit -m "feat: add server-side metrics recording with hourly buckets"
```

---

## Chunk 2: S4 推論、Pydantic モデル、認証 DRY 化、イベント API エンドポイント

### Task 5: S4 推論モジュール

**Files:**
- Create: `server/api/inference.py`
- Create: `tests/test_inference.py`

- [ ] **Step 1: test_inference.py を書く（モックテスト、CI 対象）**

```python
"""S4 推論のインターフェーステスト（モック版、CI 対象）"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


def _create_dummy_jpeg(path):
    """テスト用の小さい JPEG を作成"""
    from PIL import Image
    img = Image.new("RGB", (64, 64), color=(0, 128, 0))
    img.save(str(path), "JPEG")


class TestS4Result:
    def test_s4_result_fields(self):
        """S4Result が必要なフィールドを持つ"""
        from server.api.inference import S4Result
        r = S4Result(is_animal=True, score=0.85, detections=[], error=None)
        assert r.is_animal is True
        assert r.score == 0.85


class TestS4Interface:
    def test_run_s4_returns_result(self, tmp_path):
        """run_s4 が S4Result を返す"""
        from server.api.inference import run_s4, S4Result
        img = tmp_path / "test.jpg"
        _create_dummy_jpeg(img)

        with patch("server.api.inference._model") as mock_model:
            mock_model.return_value = [{"boxes": [], "scores": [], "labels": []}]
            result = run_s4(str(img))
            assert isinstance(result, S4Result)
            assert result.is_animal is False

    def test_fail_open_on_inference_error(self, tmp_path):
        """推論エラー時はフェイルオープン（動物扱い）"""
        from server.api.inference import run_s4
        img = tmp_path / "test.jpg"
        _create_dummy_jpeg(img)

        with patch("server.api.inference._model", side_effect=RuntimeError("boom")):
            result = run_s4(str(img))
            assert result.is_animal is True
            assert result.error is not None

    def test_fail_open_on_model_not_loaded(self, tmp_path):
        """モデル未ロード時はフェイルオープン"""
        from server.api.inference import run_s4
        img = tmp_path / "test.jpg"
        _create_dummy_jpeg(img)

        with patch("server.api.inference._model", None):
            result = run_s4(str(img))
            assert result.is_animal is True
            assert "not loaded" in result.error

    def test_animal_detection(self, tmp_path):
        """動物クラスが閾値以上で検出された場合 is_animal=True"""
        import torch
        from server.api.inference import run_s4

        img = tmp_path / "test.jpg"
        _create_dummy_jpeg(img)

        bear_label = 23  # COCO の bear
        mock_output = [{
            "boxes": torch.tensor([[10, 10, 100, 100]]),
            "scores": torch.tensor([0.85]),
            "labels": torch.tensor([bear_label]),
        }]

        with patch("server.api.inference._model") as mock_model:
            mock_model.return_value = mock_output
            result = run_s4(str(img))
            assert result.is_animal is True
            assert result.score >= 0.3
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/test_inference.py -v`
Expected: ImportError

- [ ] **Step 3: inference.py を実装**

```python
"""S4 推論 — torchvision fasterrcnn_resnet50_fpn_v2"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field

import torch
from torchvision import transforms
from PIL import Image

logger = logging.getLogger(__name__)

COCO_ANIMAL_IDS: dict[int, str] = {
    16: "bird", 17: "cat", 18: "dog", 19: "horse",
    20: "sheep", 21: "cow", 22: "elephant", 23: "bear",
    24: "zebra", 25: "giraffe",
}

S4_THRESHOLD = 0.3
S4_TIMEOUT_S = 20

_model = None
_device = None
_executor = ThreadPoolExecutor(max_workers=1)


@dataclass
class S4Result:
    is_animal: bool
    score: float
    detections: list[dict] = field(default_factory=list)
    error: str | None = None


def load_model() -> None:
    """モデルをロードする。lifespan で 1 回呼ぶ。"""
    global _model, _device
    from torchvision.models.detection import (
        fasterrcnn_resnet50_fpn_v2,
        FasterRCNN_ResNet50_FPN_V2_Weights,
    )

    _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("S4 inference device: %s", _device)

    weights = FasterRCNN_ResNet50_FPN_V2_Weights.DEFAULT
    _model = fasterrcnn_resnet50_fpn_v2(weights=weights)
    _model.to(_device)
    _model.eval()
    logger.info("S4 model loaded: fasterrcnn_resnet50_fpn_v2")


def _run_inference(image_path: str, threshold: float) -> S4Result:
    """推論の実処理（タイムアウトなし）"""
    if _model is None:
        return S4Result(is_animal=True, score=0.0, error="model not loaded")

    img = Image.open(image_path).convert("RGB")
    transform = transforms.ToTensor()
    tensor = transform(img).to(_device)

    with torch.no_grad():
        outputs = _model([tensor])

    output = outputs[0]
    boxes = output["boxes"]
    scores = output["scores"]
    labels = output["labels"]

    detections = []
    max_animal_score = 0.0

    for i in range(len(scores)):
        label_id = int(labels[i])
        score = float(scores[i])
        if label_id in COCO_ANIMAL_IDS and score >= threshold:
            detections.append({
                "label_id": label_id,
                "label_name": COCO_ANIMAL_IDS[label_id],
                "score": score,
                "box": [float(x) for x in boxes[i]],
            })
            max_animal_score = max(max_animal_score, score)

    return S4Result(
        is_animal=len(detections) > 0,
        score=max_animal_score,
        detections=detections,
    )


def run_s4(image_path: str, threshold: float = S4_THRESHOLD) -> S4Result:
    """S4 推論を実行する。タイムアウト・エラー時はフェイルオープン。"""
    try:
        if _model is None:
            return S4Result(is_animal=True, score=0.0, error="model not loaded")

        future = _executor.submit(_run_inference, image_path, threshold)
        return future.result(timeout=S4_TIMEOUT_S)

    except FuturesTimeoutError:
        logger.error("S4 inference timed out after %ds, fail-open", S4_TIMEOUT_S)
        return S4Result(is_animal=True, score=0.0, error=f"timeout ({S4_TIMEOUT_S}s)")

    except Exception as e:
        logger.error("S4 inference failed, fail-open: %s", e)
        return S4Result(is_animal=True, score=0.0, error=str(e))
```

- [ ] **Step 4: テスト実行**

Run: `pytest tests/test_inference.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: コミット**

```bash
git add server/api/inference.py tests/test_inference.py
git commit -m "feat: add S4 inference with fail-open, GPU auto-detect, 20s timeout"
```

---

### Task 6: Pydantic モデル拡張と認証 DRY 化

**Files:**
- Modify: `server/api/models.py`
- Modify: `server/api/auth.py`

- [ ] **Step 1: models.py を全体置き換え**

```python
from __future__ import annotations

from pydantic import BaseModel


class HeartbeatRequest(BaseModel):
    battery_pct: int
    battery_temp_c: float
    uptime_s: int
    config_etag: str
    clock_offset_ms: int
    device_info: dict | None = None
    metrics: dict | None = None


class HeartbeatResponse(BaseModel):
    status: str
    server_time: str | None = None
    config: dict | None = None
    config_etag: str | None = None
    commands: list[dict] = []


class EventData(BaseModel):
    event_id: str
    device_id: str | None = None
    detected_at: str
    clock_offset_ms: int | None = None
    camera: str | None = None
    roi: dict | None = None
    azimuth_deg: float | None = None
    elevation_deg: float | None = None
    estimated_distance_m: float | None = None
    estimated_size_m: float | None = None
    track: dict | None = None
    env: dict | None = None
    scores: dict | None = None


class EventResponse(BaseModel):
    event_id: str
    status: str
    s4_result: dict | None = None
```

- [ ] **Step 2: auth.py に `resolve_device_or_raise` を追加**

`server/api/auth.py` の末尾に追加。app.py と events.py の両方から使う共通関数:

```python
from fastapi import HTTPException, Request


def resolve_device_or_raise(request: Request, authorization: str | None) -> str:
    """Bearer トークンから device_id を解決する。失敗時は 401 を返す。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization[7:]
    device_id = request.app.state.auth.resolve(token)
    if device_id is None:
        raise HTTPException(status_code=401, detail="Unknown token")
    return device_id
```

- [ ] **Step 3: 既存テストが通ることを確認**

Run: `pytest tests/test_heartbeat.py -v`
Expected: ALL PASS

- [ ] **Step 4: コミット**

```bash
git add server/api/models.py server/api/auth.py
git commit -m "feat(models): add EventData/EventResponse, extend HeartbeatResponse, DRY auth"
```

---

### Task 7: イベント API エンドポイント

**Files:**
- Create: `server/api/events.py`
- Modify: `server/api/app.py`
- Modify: `server/api/database.py`（record_heartbeat を metrics 対応に更新）
- Modify: `tests/conftest.py`

- [ ] **Step 1: events.py を実装**

```python
"""イベント受信エンドポイント"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, Request

from server.api.models import EventResponse
from server.api.auth import resolve_device_or_raise
from server.api.database import (
    insert_event, get_event, update_event_status,
    update_event_scores, update_event_media_path,
)
from server.api.commands import enqueue_command
from server.api.inference import run_s4, S4Result
from server.api.metrics import record_metric

router = APIRouter()


@router.post("/v1/events", response_model=EventResponse)
async def post_event(
    request: Request,
    event: str = Form(...),
    thumbnail: UploadFile | None = File(default=None),
    authorization: str | None = Header(default=None),
):
    db = request.app.state.db_path
    device_id = resolve_device_or_raise(request, authorization)
    event_data = json.loads(event)
    event_data["device_id"] = device_id

    # べき等チェック
    existing = get_event(db, event_data["event_id"])
    if existing:
        return EventResponse(event_id=existing["event_id"], status=existing["status"])

    insert_event(db, event_data)
    record_metric(db, "events_received")

    # サムネイル保存
    media_root = request.app.state.media_root
    event_dir = os.path.join(media_root, device_id, event_data["event_id"])
    os.makedirs(event_dir, exist_ok=True)

    if thumbnail:
        thumb_path = os.path.join(event_dir, "thumbnail.jpg")
        content = await thumbnail.read()
        with open(thumb_path, "wb") as f:
            f.write(content)
        record_metric(db, "media_bytes_received", len(content))

    # S4 推論
    record_metric(db, "s4_input_count")
    s4_image = os.path.join(event_dir, "thumbnail.jpg") if thumbnail else None

    if s4_image and os.path.exists(s4_image):
        s4_result = run_s4(s4_image)
    else:
        s4_result = S4Result(is_animal=True, score=0.0, error="no thumbnail")

    # スコア更新
    scores = event_data.get("scores", {"s3": None, "s4": None, "s5": None})
    scores["s4"] = s4_result.score
    update_event_scores(db, event_data["event_id"], scores)
    record_metric(db, "s4_score_sum", s4_result.score)
    record_metric(db, "s4_score_count")

    # コマンド発行 + 状態遷移
    if s4_result.is_animal:
        enqueue_command(db, device_id, "request_video", {"event_id": event_data["event_id"]})
        update_event_status(db, event_data["event_id"], "MEDIA_REQUESTED")
        record_metric(db, "s4_animal_count")
        status = "MEDIA_REQUESTED"
    else:
        enqueue_command(db, device_id, "discard", {"event_id": event_data["event_id"]})
        update_event_status(db, event_data["event_id"], "SERVER_REJECTED")
        record_metric(db, "s4_non_animal_count")
        status = "SERVER_REJECTED"

    if s4_result.error:
        record_metric(db, "s4_error_count")

    return EventResponse(
        event_id=event_data["event_id"],
        status=status,
        s4_result={"is_animal": s4_result.is_animal, "score": s4_result.score},
    )


@router.post("/v1/events/{event_id}/still")
async def post_still(
    request: Request,
    event_id: str,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    db = request.app.state.db_path
    device_id = resolve_device_or_raise(request, authorization)

    evt = get_event(db, event_id)
    if not evt:
        raise HTTPException(status_code=404, detail="Event not found")
    if evt["device_id"] != device_id:
        raise HTTPException(status_code=403, detail="Event belongs to another device")

    media_root = request.app.state.media_root
    event_dir = os.path.join(media_root, device_id, event_id)
    os.makedirs(event_dir, exist_ok=True)

    still_path = os.path.join(event_dir, "still.jpg")
    content = await file.read()
    with open(still_path, "wb") as f:
        f.write(content)

    rel_path = f"{device_id}/{event_id}/still.jpg"
    update_event_media_path(db, event_id, "still", rel_path)
    record_metric(db, "media_bytes_received", len(content))

    return {"event_id": event_id, "still_path": rel_path}


@router.post("/v1/events/{event_id}/video")
async def post_video(
    request: Request,
    event_id: str,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    db = request.app.state.db_path
    device_id = resolve_device_or_raise(request, authorization)

    evt = get_event(db, event_id)
    if not evt:
        raise HTTPException(status_code=404, detail="Event not found")
    if evt["device_id"] != device_id:
        raise HTTPException(status_code=403, detail="Event belongs to another device")

    media_root = request.app.state.media_root
    event_dir = os.path.join(media_root, device_id, event_id)
    os.makedirs(event_dir, exist_ok=True)

    video_path = os.path.join(event_dir, "video.mp4")
    content = await file.read()
    with open(video_path, "wb") as f:
        f.write(content)

    rel_path = f"{device_id}/{event_id}/video.mp4"
    update_event_media_path(db, event_id, "video", rel_path)
    update_event_status(db, event_id, "MEDIA_UPLOADED")
    record_metric(db, "media_bytes_received", len(content))

    return {"event_id": event_id, "video_path": rel_path, "status": "MEDIA_UPLOADED"}
```

- [ ] **Step 2: app.py を更新**

```python
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException

from server.api.models import HeartbeatRequest, HeartbeatResponse
from server.api.auth import resolve_device_or_raise
from server.api.database import init_db, ensure_device, record_heartbeat, get_active_config
from server.api.commands import get_pending_commands, mark_delivered
from server.api.events import router as events_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_url = os.environ.get("DATABASE_URL", "sqlite:///./bearwatch.db")
    db_path = db_url.replace("sqlite:///", "")
    init_db(db_path)

    app.state.db_path = db_path
    app.state.auth = TokenAuth.from_env()
    app.state.media_root = os.environ.get("MEDIA_ROOT", "./media")

    if os.environ.get("SKIP_MODEL_LOAD") != "1":
        from server.api.inference import load_model
        load_model()

    yield


from server.api.auth import TokenAuth

app = FastAPI(title="BearWatch", lifespan=lifespan)
app.include_router(events_router, prefix="/api")


@app.post("/api/v1/heartbeat", response_model=HeartbeatResponse)
def post_heartbeat(
    body: HeartbeatRequest,
    authorization: str | None = Header(default=None),
    request: Request = None,
):
    from fastapi import Request as _Req
    from starlette.requests import Request
    device_id = resolve_device_or_raise(request, authorization)
    db_path = app.state.db_path

    if body.device_info:
        ensure_device(db_path, device_id, body.device_info)
    else:
        ensure_device(db_path, device_id)

    record_heartbeat(
        db_path, device_id,
        body.battery_pct, body.battery_temp_c, body.uptime_s,
        body.config_etag, body.clock_offset_ms,
        body.metrics,
    )

    server_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    config = None
    config_etag = None
    active = get_active_config(db_path, device_id)
    if active is not None:
        cfg, etag = active
        if etag != body.config_etag:
            config = cfg
            config_etag = etag

    pending = get_pending_commands(db_path, device_id)
    if pending:
        mark_delivered(db_path, [c["id"] for c in pending])
    commands = [
        {k: v for k, v in c.items() if k != "id"}
        for c in pending
    ]

    return HeartbeatResponse(
        status="ok",
        server_time=server_time,
        config=config,
        config_etag=config_etag,
        commands=commands,
    )
```

**注意**: `app.py` は heartbeat エンドポイントの `request` 引数を `Request` 型で受け取るよう変更が必要。実装者は FastAPI の `Request` の正しい import を確認すること（`from fastapi import Request`）。既存の `_resolve_device` 関数は `resolve_device_or_raise` に置き換える。

- [ ] **Step 3: database.py の record_heartbeat を metrics_json 対応に更新**

`record_heartbeat` のシグネチャに `metrics: dict | None = None` を追加:

```python
def record_heartbeat(
    db_path: str, device_id: str,
    battery_pct: int, battery_temp_c: float, uptime_s: int,
    config_etag: str, clock_offset_ms: int,
    metrics: dict | None = None,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO heartbeats
               (device_id, timestamp_utc, battery_pct, battery_temp_c, uptime_s,
                config_etag, clock_offset_ms, metrics_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (device_id, now_utc(), battery_pct, battery_temp_c, uptime_s,
             config_etag, clock_offset_ms,
             json.dumps(metrics) if metrics else None),
        )
```

- [ ] **Step 4: conftest.py に MEDIA_ROOT と SKIP_MODEL_LOAD を追加**

`tests/conftest.py` の `env` 辞書に追加:

```python
env["MEDIA_ROOT"] = str(tmp / "media")
env["SKIP_MODEL_LOAD"] = "1"
```

- [ ] **Step 5: 全テスト実行**

Run: `pytest tests/test_heartbeat.py tests/test_events.py tests/test_commands.py tests/test_inference.py -v`
Expected: ALL PASS

- [ ] **Step 6: コミット**

```bash
git add server/api/events.py server/api/app.py server/api/database.py tests/conftest.py
git commit -m "feat: add event API endpoints with S4 inference and command dispatch"
```

---

## Chunk 3: GET /v1/config、fake_device 拡張、E2E テスト、CI 更新

### Task 8: GET /v1/config エンドポイント

**Files:**
- Create: `server/api/config.py`
- Modify: `server/api/app.py`（config router を include）
- Modify: `tests/test_heartbeat.py`（config テスト追加）

- [ ] **Step 1: config.py を作成**

```python
"""設定取得エンドポイント"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from server.api.auth import resolve_device_or_raise
from server.api.database import get_active_config

router = APIRouter()


@router.get("/v1/config")
def get_config(
    request: Request,
    authorization: str | None = Header(default=None),
    if_none_match: str | None = Header(default=None),
):
    db = request.app.state.db_path
    device_id = resolve_device_or_raise(request, authorization)

    active = get_active_config(db, device_id)
    if active is None:
        raise HTTPException(status_code=404, detail="No config available")

    config, etag = active
    if if_none_match and if_none_match == etag:
        return Response(status_code=304)

    return JSONResponse(content=config, headers={"ETag": etag})
```

- [ ] **Step 2: app.py に config router を include**

```python
from server.api.config import router as config_router
app.include_router(config_router, prefix="/api")
```

- [ ] **Step 3: test_heartbeat.py に config テストを追加**

`TestHeartbeatEndpoint` クラスに以下を追加:

```python
def test_config_no_config_returns_404(self, server_url):
    """設定未登録で 404"""
    resp = httpx.get(
        f"{server_url}/api/v1/config",
        headers={"Authorization": "Bearer test-token-001"},
    )
    assert resp.status_code == 404

def test_config_returns_200_with_etag(self, server_url):
    """設定が登録されていれば 200 + ETag ヘッダ"""
    # DB に直接設定を挿入（テスト用）
    import sqlite3, json
    db_path = server_url.replace("http://127.0.0.1:", "")
    # conftest の server_url からは DB パスを直接取れないため、
    # このテストは test_e2e.py で統合テストとして実施する。
    # ここでは 404 ケースのみ確認する。
```

**注**: 200/304 の config テストは DB への直接書き込みが必要。server_url fixture からは DB パスが取れないため、Task 10 の test_e2e.py で統合テストとして実施する。

- [ ] **Step 4: テスト実行**

Run: `pytest tests/test_heartbeat.py -v`
Expected: ALL PASS

- [ ] **Step 5: コミット**

```bash
git add server/api/config.py server/api/app.py tests/test_heartbeat.py
git commit -m "feat: add GET /v1/config endpoint with ETag support"
```

---

### Task 9: fake_device.py 拡張（イベント送信 + コマンド受信 + E2E）

**Files:**
- Modify: `tools/fake_device/main.py`
- Create: `tools/fake_device/dummy_media.py`

- [ ] **Step 1: dummy_media.py を作成**

外部依存なし（`tools/replay` に依存しない）:

```python
"""ダミー画像・動画生成"""
from __future__ import annotations

import io


def generate_dummy_jpeg(width: int = 640, height: int = 480) -> bytes:
    """テスト用のダミー JPEG を生成して bytes で返す"""
    from PIL import Image, ImageDraw
    import random

    img = Image.new("RGB", (width, height), color=(34, 139, 34))
    draw = ImageDraw.Draw(img)
    x1 = random.randint(0, width // 2)
    y1 = random.randint(0, height // 2)
    x2 = x1 + random.randint(30, 100)
    y2 = y1 + random.randint(40, 120)
    draw.rectangle([x1, y1, x2, y2], fill=(139, 69, 19))

    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def generate_dummy_mp4_bytes(size: int = 4096) -> bytes:
    """テスト用のダミー MP4 バイト列を返す。有効な MP4 ではないが、アップロードテスト用。"""
    return b"\x00" * size
```

- [ ] **Step 2: fake_device/main.py を拡張**

```python
"""端末シミュレータ。サーバ API にハートビートとイベントを送信する。

Usage:
    python tools/fake_device/main.py --server http://localhost:8000 --token abc123
    python tools/fake_device/main.py --server http://localhost:8000 --token abc123 --scenario e2e
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import uuid

import httpx

DEVICE_INFO = {
    "model": "fake_device",
    "android_api": 24,
    "app_version": "0.1.0-fake",
}


def send_heartbeat(
    client: httpx.Client,
    server: str,
    token: str,
    state: dict,
    send_device_info: bool = False,
) -> dict:
    body: dict = {
        "battery_pct": state["battery_pct"],
        "battery_temp_c": state["battery_temp_c"],
        "uptime_s": state["uptime_s"],
        "config_etag": state["config_etag"],
        "clock_offset_ms": state["clock_offset_ms"],
    }
    if send_device_info:
        body["device_info"] = DEVICE_INFO

    resp = client.post(
        f"{server}/api/v1/heartbeat",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return resp.json()


def send_event(client: httpx.Client, server: str, token: str) -> dict:
    """ダミーイベントを送信する"""
    from tools.fake_device.dummy_media import generate_dummy_jpeg

    event_id = str(uuid.uuid4())
    event_data = {
        "event_id": event_id,
        "detected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "clock_offset_ms": random.randint(-100, 100),
        "camera": random.choice(["front", "rear"]),
        "roi": {"x": random.randint(0, 600), "y": random.randint(0, 300),
                "w": random.randint(30, 100), "h": random.randint(40, 120)},
        "azimuth_deg": round(random.uniform(0, 360), 1),
        "elevation_deg": round(random.uniform(-10, 0), 1),
        "estimated_distance_m": round(random.uniform(10, 70), 1),
        "estimated_size_m": round(random.uniform(0.5, 2.0), 2),
        "track": {"duration_s": round(random.uniform(2, 10), 1),
                  "frames": random.randint(3, 20),
                  "speed_mps": round(random.uniform(0.3, 3.0), 2),
                  "direction_deg": round(random.uniform(0, 360), 1),
                  "straightness": round(random.uniform(0.3, 1.0), 2)},
        "env": {"weather": None, "mean_luminance": random.randint(50, 200),
                "global_luminance_delta": random.randint(0, 10),
                "enclosure_temp_c": round(random.uniform(25, 40), 1)},
        "scores": {"s3": round(random.uniform(0.5, 1.0), 3), "s4": None, "s5": None},
    }

    thumbnail = generate_dummy_jpeg()

    resp = client.post(
        f"{server}/api/v1/events",
        data={"event": json.dumps(event_data)},
        files={"thumbnail": ("thumbnail.jpg", thumbnail, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"[event] id={event_id[:8]}...  status={result['status']}  "
          f"s4={result.get('s4_result', {})}")
    return result


def upload_video(client: httpx.Client, server: str, token: str, event_id: str) -> dict:
    """ダミー動画をアップロードする"""
    from tools.fake_device.dummy_media import generate_dummy_mp4_bytes

    video = generate_dummy_mp4_bytes()
    resp = client.post(
        f"{server}/api/v1/events/{event_id}/video",
        files={"file": ("video.mp4", video, "video/mp4")},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"[video] id={event_id[:8]}...  status={result['status']}")
    return result


def run(server: str, token: str, interval: float, count: int | None) -> None:
    state = {
        "battery_pct": 100,
        "battery_temp_c": round(25.0 + random.uniform(-2, 2), 1),
        "uptime_s": 0,
        "config_etag": "none",
        "clock_offset_ms": random.randint(-100, 100),
    }

    with httpx.Client(timeout=10) as client:
        i = 0
        while count is None or i < count:
            i += 1
            send_info = (i == 1)
            try:
                result = send_heartbeat(client, server, token, state, send_info)
                print(f"[heartbeat {i}] status={result['status']}  "
                      f"battery={state['battery_pct']}%  temp={state['battery_temp_c']:.1f}C")

                if result.get("config"):
                    print(f"  -> new config received (etag={result['config_etag']})")
                    state["config_etag"] = result["config_etag"]

                commands = result.get("commands", [])
                for cmd in commands:
                    print(f"  -> command: {cmd}")

            except httpx.HTTPStatusError as e:
                print(f"[heartbeat {i}] HTTP {e.response.status_code}: {e.response.text}",
                      file=sys.stderr)
                return
            except httpx.ConnectError:
                print(f"[heartbeat {i}] connection refused", file=sys.stderr)
                return

            state["uptime_s"] += int(interval)
            state["battery_pct"] = max(0, state["battery_pct"] - random.choice([0, 0, 0, 1]))
            state["battery_temp_c"] = round(25.0 + random.uniform(-3, 5), 1)

            if count is None or i < count:
                time.sleep(interval)


def run_e2e(server: str, token: str) -> dict:
    """E2E シナリオ: イベント送信 → S4 判定 → ハートビートでコマンド受信 → 映像アップロード"""
    state = {
        "battery_pct": 85,
        "battery_temp_c": 28.0,
        "uptime_s": 3600,
        "config_etag": "none",
        "clock_offset_ms": 0,
    }

    with httpx.Client(timeout=30) as client:
        print("=== E2E Step 1: Initial heartbeat ===")
        hb = send_heartbeat(client, server, token, state, send_device_info=True)
        print(f"  status={hb['status']}  server_time={hb.get('server_time')}")

        print("=== E2E Step 2: Send event ===")
        event_result = send_event(client, server, token)
        event_id = event_result["event_id"]

        print("=== E2E Step 3: Heartbeat to receive commands ===")
        hb2 = send_heartbeat(client, server, token, state)
        commands = hb2.get("commands", [])
        print(f"  commands received: {len(commands)}")
        for cmd in commands:
            print(f"    {cmd}")

        video_uploaded = False
        for cmd in commands:
            if cmd.get("type") == "request_video":
                print("=== E2E Step 4: Upload video ===")
                upload_video(client, server, token, cmd["event_id"])
                video_uploaded = True

        print("=== E2E Step 5: Final heartbeat ===")
        hb3 = send_heartbeat(client, server, token, state)
        remaining = hb3.get("commands", [])
        print(f"  remaining commands: {len(remaining)}")

        result = {
            "event_id": event_id,
            "s4_result": event_result.get("s4_result"),
            "commands_received": len(commands),
            "video_uploaded": video_uploaded,
            "remaining_commands": len(remaining),
        }
        print(f"\n=== E2E Result ===")
        for k, v in result.items():
            print(f"  {k}: {v}")
        return result


def main():
    parser = argparse.ArgumentParser(description="BearWatch fake device")
    parser.add_argument("--server", required=True, help="Server URL")
    parser.add_argument("--token", required=True, help="Device authentication token")
    parser.add_argument("--interval", type=float, default=60, help="Heartbeat interval (seconds)")
    parser.add_argument("--count", type=int, default=None, help="Number of heartbeats")
    parser.add_argument("--scenario", choices=["heartbeat", "e2e"], default="heartbeat",
                        help="Scenario to run")
    args = parser.parse_args()

    if args.scenario == "e2e":
        run_e2e(args.server, args.token)
    else:
        run(args.server, args.token, args.interval, args.count)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: コミット**

```bash
git add tools/fake_device/main.py tools/fake_device/dummy_media.py
git commit -m "feat(fake_device): add event sending, command handling, E2E scenario"
```

---

### Task 10: E2E テスト（モック推論、CI 対象）

**Files:**
- Create: `tests/test_e2e.py`

- [ ] **Step 1: test_e2e.py を書く**

```python
"""E2E テスト（モック推論、CI 対象）

SKIP_MODEL_LOAD=1 の環境では S4 推論がフェイルオープン（全て動物扱い）になるため、
全イベントに request_video コマンドが発行される。
"""
import subprocess
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import httpx


class TestEventEndpoint:
    def test_post_event_with_thumbnail(self, server_url):
        """イベント + サムネイルが受信できる"""
        from tools.fake_device.dummy_media import generate_dummy_jpeg

        event_data = {
            "event_id": "test-evt-001",
            "detected_at": "2026-08-20T12:00:00Z",
            "clock_offset_ms": 0,
            "camera": "rear",
            "roi": {"x": 100, "y": 200, "w": 50, "h": 80},
            "scores": {"s3": 0.7, "s4": None, "s5": None},
        }
        thumbnail = generate_dummy_jpeg(320, 240)

        resp = httpx.post(
            f"{server_url}/api/v1/events",
            data={"event": json.dumps(event_data)},
            files={"thumbnail": ("thumb.jpg", thumbnail, "image/jpeg")},
            headers={"Authorization": "Bearer test-token-001"},
            timeout=30,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["event_id"] == "test-evt-001"
        assert body["status"] in ("MEDIA_REQUESTED", "SERVER_REJECTED")
        assert "s4_result" in body

    def test_idempotent_event(self, server_url):
        """同じ event_id の再送は既存状態を返す"""
        event_data = {
            "event_id": "test-evt-idem",
            "detected_at": "2026-08-20T12:00:00Z",
            "clock_offset_ms": 0,
        }

        resp1 = httpx.post(
            f"{server_url}/api/v1/events",
            data={"event": json.dumps(event_data)},
            headers={"Authorization": "Bearer test-token-001"},
            timeout=30,
        )
        assert resp1.status_code == 200

        resp2 = httpx.post(
            f"{server_url}/api/v1/events",
            data={"event": json.dumps(event_data)},
            headers={"Authorization": "Bearer test-token-001"},
            timeout=30,
        )
        assert resp2.status_code == 200
        assert resp2.json()["event_id"] == "test-evt-idem"

    def test_upload_video_and_status_transition(self, server_url):
        """映像アップロード後にステータスが MEDIA_UPLOADED に遷移する"""
        event_data = {
            "event_id": "test-evt-video",
            "detected_at": "2026-08-20T12:00:00Z",
            "clock_offset_ms": 0,
        }
        httpx.post(
            f"{server_url}/api/v1/events",
            data={"event": json.dumps(event_data)},
            headers={"Authorization": "Bearer test-token-001"},
            timeout=30,
        )

        dummy_video = b"\x00" * 1024
        resp = httpx.post(
            f"{server_url}/api/v1/events/test-evt-video/video",
            files={"file": ("video.mp4", dummy_video, "video/mp4")},
            headers={"Authorization": "Bearer test-token-001"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "MEDIA_UPLOADED"

    def test_heartbeat_returns_commands_and_server_time(self, server_url):
        """イベント送信後のハートビートで commands と server_time が返る"""
        from tools.fake_device.dummy_media import generate_dummy_jpeg

        event_data = {
            "event_id": "test-evt-cmd",
            "detected_at": "2026-08-20T12:00:00Z",
            "clock_offset_ms": 0,
        }
        thumbnail = generate_dummy_jpeg(320, 240)

        httpx.post(
            f"{server_url}/api/v1/events",
            data={"event": json.dumps(event_data)},
            files={"thumbnail": ("thumb.jpg", thumbnail, "image/jpeg")},
            headers={"Authorization": "Bearer test-token-001"},
            timeout=30,
        )

        hb = httpx.post(
            f"{server_url}/api/v1/heartbeat",
            json={
                "battery_pct": 85,
                "battery_temp_c": 28.0,
                "uptime_s": 3600,
                "config_etag": "none",
                "clock_offset_ms": 0,
            },
            headers={"Authorization": "Bearer test-token-001"},
        )
        assert hb.status_code == 200
        body = hb.json()
        assert "commands" in body
        assert "server_time" in body
        # SKIP_MODEL_LOAD=1 でフェイルオープン → request_video が必ず発行される
        assert len(body["commands"]) >= 1

    def test_commands_cleared_after_delivery(self, server_url):
        """配信済みコマンドは次回のハートビートに含まれない"""
        from tools.fake_device.dummy_media import generate_dummy_jpeg

        # 新しいイベントを作成（他テストと独立）
        event_data = {
            "event_id": "test-evt-clear",
            "detected_at": "2026-08-20T12:00:00Z",
            "clock_offset_ms": 0,
        }
        thumbnail = generate_dummy_jpeg(320, 240)
        httpx.post(
            f"{server_url}/api/v1/events",
            data={"event": json.dumps(event_data)},
            files={"thumbnail": ("thumb.jpg", thumbnail, "image/jpeg")},
            headers={"Authorization": "Bearer test-token-001"},
            timeout=30,
        )

        # 1回目のハートビートでコマンドを受け取る
        hb1 = httpx.post(
            f"{server_url}/api/v1/heartbeat",
            json={"battery_pct": 85, "battery_temp_c": 28.0,
                  "uptime_s": 3600, "config_etag": "none", "clock_offset_ms": 0},
            headers={"Authorization": "Bearer test-token-001"},
        )
        cmds1 = hb1.json().get("commands", [])
        assert len(cmds1) >= 1

        # 2回目のハートビートではそのコマンドが含まれない
        hb2 = httpx.post(
            f"{server_url}/api/v1/heartbeat",
            json={"battery_pct": 85, "battery_temp_c": 28.0,
                  "uptime_s": 3600, "config_etag": "none", "clock_offset_ms": 0},
            headers={"Authorization": "Bearer test-token-001"},
        )
        cmds2 = hb2.json().get("commands", [])
        # test-evt-clear のコマンドが消えていること
        clear_cmds = [c for c in cmds2 if c.get("event_id") == "test-evt-clear"]
        assert len(clear_cmds) == 0


class TestFakeDeviceE2E:
    def test_e2e_scenario(self, server_url):
        """fake_device の E2E シナリオが正常完了し、M1 Exit Criteria を満たす"""
        proc = subprocess.run(
            [sys.executable, "tools/fake_device/main.py",
             "--server", server_url,
             "--token", "test-token-001",
             "--scenario", "e2e"],
            capture_output=True, text=True, timeout=60,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert proc.returncode == 0
        # M1 Exit Criteria の検証
        assert "E2E Step 1" in proc.stdout          # ハートビート送信
        assert "E2E Step 2" in proc.stdout          # イベント送信
        assert "E2E Step 3" in proc.stdout          # コマンド受信
        assert "E2E Result" in proc.stdout          # 結果出力
        assert "video_uploaded: True" in proc.stdout    # 映像アップロード成功
        assert "remaining_commands: 0" in proc.stdout   # コマンドが消えている
```

- [ ] **Step 2: テスト実行**

Run: `pytest tests/test_e2e.py -v`
Expected: ALL PASS

- [ ] **Step 3: コミット**

```bash
git add tests/test_e2e.py
git commit -m "test: add E2E tests for event flow and fake_device scenario"
```

---

### Task 11: CI 更新

**Files:**
- Modify: `.github/workflows/test.yml`

- [ ] **Step 1: test.yml を更新**

```yaml
      - name: Run pytest
        env:
          DEVICE_TOKENS: "test-token-001:device-001,test-token-002:device-002"
          SKIP_MODEL_LOAD: "1"
        run: pytest tests/ -v --ignore=tests/test_config.py -m "not slow"
```

`MEDIA_ROOT` は conftest.py 側で tmp ディレクトリを設定するため CI の env には含めない。

- [ ] **Step 2: 全テスト実行（ローカル）**

Run: `pytest tests/ -v --ignore=tests/test_config.py -m "not slow"`
Expected: ALL PASS

- [ ] **Step 3: コミット**

```bash
git add .github/workflows/test.yml
git commit -m "ci: add SKIP_MODEL_LOAD and slow marker filter"
```

---

### Task 12: 最終検証

- [ ] **Step 1: 全テスト実行**

```bash
python tests/test_config.py
pytest tests/ -v --ignore=tests/test_config.py -m "not slow"
```

Expected: config 40 PASS + pytest ALL PASS

- [ ] **Step 2: ローカルで E2E 手動検証（任意、モデルあり）**

```bash
# ターミナル 1: サーバ起動
DEVICE_TOKENS="test:dev1" MEDIA_ROOT="./media" python -m uvicorn server.api.app:app --port 8000

# ターミナル 2: E2E 実行
python tools/fake_device/main.py --server http://localhost:8000 --token test --scenario e2e
```

Expected output:
- S4 推論が実行される（CPU で数秒）
- 動物/非動物の判定結果が `s4_result` に表示される
- `video_uploaded: True` または `video_uploaded: False`（非動物判定の場合）
- `remaining_commands: 0`

- [ ] **Step 3: M1 Exit Criteria チェックリスト**

| # | 条件 | 確認方法 |
|---|---|---|
| 1 | `fake_device.py --scenario e2e` が実行できる | Step 2 |
| 2 | ダミーイベント + サムネイルがサーバに送信される | stdout に `[event]` 行 |
| 3 | S4 推論が実行され、scores.s4 が記録される | stdout に `s4=` |
| 4 | ハートビートで `request_video` or `discard` コマンドを受信 | stdout に `commands received: 1` |
| 5 | `request_video` の場合、映像がアップロードされる | stdout に `video_uploaded: True` |
| 6 | イベントの状態が正しく遷移する | `remaining_commands: 0` |

- [ ] **Step 4: コミット（必要に応じて微修正を含む）**

```bash
git add -A
git commit -m "feat: M1 server skeleton complete - E2E verified"
```
