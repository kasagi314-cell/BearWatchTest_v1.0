# Phase 0.5 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 端末実機なしでサーバ開発・閾値調整を可能にする開発基盤を整備する

**Architecture:** FastAPI サーバにハートビート受信エンドポイントを1本立て、fake_device.py が実 HTTP で叩く。replay.py は OpenCV で動画を読み込むスケルトン。CI は GitHub Actions で pytest を自動実行。

**Tech Stack:** Python 3.11 / FastAPI / SQLite / uvicorn / httpx / OpenCV / pytest / GitHub Actions

**Spec:** `docs/specs/2026-08-16-phase05-design.md`

---

## File Structure

### 新規作成

| ファイル | 責務 |
|---|---|
| `server/api/app.py` | FastAPI アプリ本体。ルーティングとライフサイクル |
| `server/api/models.py` | Pydantic リクエスト/レスポンスモデル |
| `server/api/auth.py` | トークン認証（Bearer） |
| `server/api/database.py` | SQLite テーブル定義と接続管理 |
| `tools/fake_device/main.py` | 端末シミュレータ |
| `tools/replay/main.py` | 動画再生スケルトン |
| `tools/replay/generate_dummy_video.py` | テスト用ダミー動画生成 |
| `tests/test_heartbeat.py` | ハートビート API の統合テスト |
| `tests/test_replay.py` | replay.py のテスト |
| `tests/conftest.py` | pytest 共通フィクスチャ（サーバ起動等） |
| `.github/workflows/test.yml` | CI 定義 |

### 変更

| ファイル | 変更内容 |
|---|---|
| `.env.example` | `DEVICE_TOKENS` を追加 |
| `requirements.txt` | `httpx` を追加 |
| `docs/SPEC.md` | §2 の D-1, D-5 を「決定済み」に更新 |

---

## Chunk 1: サーバ API とハートビート

### Task 1: Pydantic モデル定義

**Files:**
- Create: `server/api/models.py`
- Test: `tests/test_heartbeat.py`

- [ ] **Step 1: テストファイルを作成し、モデルの基本テストを書く**

```python
# tests/test_heartbeat.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from server.api.models import HeartbeatRequest, HeartbeatResponse

class TestModels:
    def test_heartbeat_request_minimal(self):
        """必須フィールドのみでリクエストが作れる"""
        req = HeartbeatRequest(
            battery_pct=69,
            battery_temp_c=28.0,
            uptime_s=3600,
            config_etag="abc123",
            clock_offset_ms=-42,
        )
        assert req.battery_pct == 69
        assert req.device_info is None

    def test_heartbeat_request_with_device_info(self):
        """device_info 付きのリクエスト"""
        req = HeartbeatRequest(
            battery_pct=69,
            battery_temp_c=28.0,
            uptime_s=3600,
            config_etag="abc123",
            clock_offset_ms=-42,
            device_info={"model": "KOB-W09", "android_api": 24, "app_version": "0.1.0"},
        )
        assert req.device_info["model"] == "KOB-W09"

    def test_heartbeat_response_no_config(self):
        """設定変更なしのレスポンス"""
        resp = HeartbeatResponse(status="ok")
        assert resp.config is None
        assert resp.config_etag is None
```

- [ ] **Step 2: テストが FAIL することを確認**

```bash
pytest tests/test_heartbeat.py::TestModels -v
```
Expected: FAIL（`server.api.models` が存在しない）

- [ ] **Step 3: モデルを実装**

```python
# server/api/models.py
from __future__ import annotations
from pydantic import BaseModel

class HeartbeatRequest(BaseModel):
    battery_pct: int
    battery_temp_c: float
    uptime_s: int
    config_etag: str
    clock_offset_ms: int
    device_info: dict | None = None

class HeartbeatResponse(BaseModel):
    status: str
    config: dict | None = None
    config_etag: str | None = None
```

- [ ] **Step 4: テストが PASS することを確認**

```bash
pytest tests/test_heartbeat.py::TestModels -v
```
Expected: 3 passed

- [ ] **Step 5: コミット**

```bash
git add server/api/models.py tests/test_heartbeat.py
git commit -m "feat(api): add Pydantic models for heartbeat request/response"
```

---

### Task 2: トークン認証

**Files:**
- Create: `server/api/auth.py`
- Modify: `.env.example`
- Test: `tests/test_heartbeat.py`

- [ ] **Step 1: 認証のテストを追加**

```python
# tests/test_heartbeat.py に追加
import os
from server.api.auth import TokenAuth

class TestAuth:
    def test_valid_token(self):
        """正しいトークンで device_id が返る"""
        auth = TokenAuth({"token-abc": "device-001"})
        assert auth.resolve("token-abc") == "device-001"

    def test_invalid_token(self):
        """不正なトークンで None が返る"""
        auth = TokenAuth({"token-abc": "device-001"})
        assert auth.resolve("wrong-token") is None

    def test_from_env(self, monkeypatch):
        """環境変数からトークンマッピングを読む"""
        monkeypatch.setenv("DEVICE_TOKENS", "tok1:dev1,tok2:dev2")
        auth = TokenAuth.from_env()
        assert auth.resolve("tok1") == "dev1"
        assert auth.resolve("tok2") == "dev2"

    def test_empty_env(self, monkeypatch):
        """環境変数が空でもクラッシュしない"""
        monkeypatch.setenv("DEVICE_TOKENS", "")
        auth = TokenAuth.from_env()
        assert auth.resolve("anything") is None
```

- [ ] **Step 2: テストが FAIL することを確認**

```bash
pytest tests/test_heartbeat.py::TestAuth -v
```

- [ ] **Step 3: 認証モジュールを実装**

```python
# server/api/auth.py
from __future__ import annotations
import os

class TokenAuth:
    def __init__(self, token_map: dict[str, str]):
        self._map = dict(token_map)

    def resolve(self, token: str) -> str | None:
        return self._map.get(token)

    @classmethod
    def from_env(cls) -> TokenAuth:
        raw = os.environ.get("DEVICE_TOKENS", "")
        token_map = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                tok, dev = pair.split(":", 1)
                token_map[tok.strip()] = dev.strip()
        return cls(token_map)
```

- [ ] **Step 4: `.env.example` にトークン設定を追加**

`.env.example` の末尾に追加:
```
# 端末トークン（token:device_id のカンマ区切り）
DEVICE_TOKENS=probe-test-token:mediapad-t3-001
```

- [ ] **Step 5: テストが PASS することを確認**

```bash
pytest tests/test_heartbeat.py::TestAuth -v
```
Expected: 4 passed

- [ ] **Step 6: コミット**

```bash
git add server/api/auth.py .env.example tests/test_heartbeat.py
git commit -m "feat(api): add token-based device authentication"
```

---

### Task 3: SQLite データベース

**Files:**
- Create: `server/api/database.py`
- Test: `tests/test_heartbeat.py`

- [ ] **Step 1: DB のテストを追加**

```python
# tests/test_heartbeat.py に追加
import sqlite3
import tempfile
from server.api.database import init_db, record_heartbeat, get_active_config

class TestDatabase:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test.db")
        init_db(self.db_path)
        self.conn = sqlite3.connect(self.db_path)

    def teardown_method(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp)

    def test_tables_created(self):
        """テーブルが3つ作成される"""
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]
        assert "device_configs" in tables
        assert "devices" in tables
        assert "heartbeats" in tables

    def test_record_heartbeat(self):
        """ハートビートが記録される"""
        record_heartbeat(self.db_path, "dev-001", 69, 28.0, 3600, "etag1", -42)
        cur = self.conn.execute("SELECT device_id, battery_pct FROM heartbeats")
        row = cur.fetchone()
        assert row == ("dev-001", 69)

    def test_no_active_config(self):
        """設定が未登録なら None"""
        result = get_active_config(self.db_path, "dev-001")
        assert result is None
```

- [ ] **Step 2: テストが FAIL することを確認**

```bash
pytest tests/test_heartbeat.py::TestDatabase -v
```

- [ ] **Step 3: データベースモジュールを実装**

```python
# server/api/database.py
from __future__ import annotations
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
        import json
        conn.execute(
            "INSERT INTO devices (id, device_info, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (device_id, json.dumps(device_info) if device_info else None, now, now),
        )
    elif device_info is not None:
        import json
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
    import json
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT config_json, etag FROM device_configs WHERE device_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
        (device_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return json.loads(row[0]), row[1]
```

- [ ] **Step 4: テストが PASS することを確認**

```bash
pytest tests/test_heartbeat.py::TestDatabase -v
```
Expected: 3 passed

- [ ] **Step 5: コミット**

```bash
git add server/api/database.py tests/test_heartbeat.py
git commit -m "feat(api): add SQLite database layer for heartbeats and configs"
```

---

### Task 4: FastAPI アプリとハートビートエンドポイント

**Files:**
- Create: `server/api/app.py`
- Test: `tests/test_heartbeat.py`

- [ ] **Step 1: エンドポイントの統合テストを追加**

この統合テストは実際にサーバプロセスを起動し、HTTP で通信する。
`conftest.py` にサーバ起動フィクスチャを用意する。

```python
# tests/conftest.py
import os
import sys
import tempfile
import time
import subprocess
import socket
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]

@pytest.fixture(scope="session")
def server_url(tmp_path_factory):
    """テスト用サーバを別プロセスで起動し、URL を返す"""
    tmp = tmp_path_factory.mktemp("server")
    db_path = str(tmp / "test.db")
    port = _free_port()

    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["DEVICE_TOKENS"] = "test-token-001:device-001,test-token-002:device-002"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.api.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    url = f"http://127.0.0.1:{port}"

    # サーバ起動を待つ
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("Server did not start")

    yield url

    proc.terminate()
    proc.wait(timeout=5)
```

```python
# tests/test_heartbeat.py に追加
import httpx

class TestHeartbeatEndpoint:
    def test_valid_heartbeat(self, server_url):
        """正常なハートビートが 200 を返す"""
        resp = httpx.post(
            f"{server_url}/api/v1/heartbeat",
            json={
                "battery_pct": 69,
                "battery_temp_c": 28.0,
                "uptime_s": 3600,
                "config_etag": "none",
                "clock_offset_ms": -42,
            },
            headers={"Authorization": "Bearer test-token-001"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    def test_no_token_returns_401(self, server_url):
        """トークンなしは 401"""
        resp = httpx.post(
            f"{server_url}/api/v1/heartbeat",
            json={
                "battery_pct": 69,
                "battery_temp_c": 28.0,
                "uptime_s": 3600,
                "config_etag": "none",
                "clock_offset_ms": 0,
            },
        )
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, server_url):
        """不正トークンは 401"""
        resp = httpx.post(
            f"{server_url}/api/v1/heartbeat",
            json={
                "battery_pct": 69,
                "battery_temp_c": 28.0,
                "uptime_s": 3600,
                "config_etag": "none",
                "clock_offset_ms": 0,
            },
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_heartbeat_with_device_info(self, server_url):
        """device_info 付きのハートビート"""
        resp = httpx.post(
            f"{server_url}/api/v1/heartbeat",
            json={
                "battery_pct": 92,
                "battery_temp_c": 26.0,
                "uptime_s": 0,
                "config_etag": "none",
                "clock_offset_ms": 10,
                "device_info": {
                    "model": "KOB-W09",
                    "android_api": 24,
                    "app_version": "0.1.0",
                },
            },
            headers={"Authorization": "Bearer test-token-001"},
        )
        assert resp.status_code == 200
```

- [ ] **Step 2: テストが FAIL することを確認**

```bash
pytest tests/test_heartbeat.py::TestHeartbeatEndpoint -v
```

- [ ] **Step 3: FastAPI アプリを実装**

```python
# server/api/app.py
from __future__ import annotations
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException

from server.api.models import HeartbeatRequest, HeartbeatResponse
from server.api.auth import TokenAuth
from server.api.database import init_db, ensure_device, record_heartbeat, get_active_config

_db_path: str = ""
_auth: TokenAuth = TokenAuth({})

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_path, _auth
    db_url = os.environ.get("DATABASE_URL", "sqlite:///./bearwatch.db")
    # "sqlite:///./bearwatch.db" -> "./bearwatch.db"
    _db_path = db_url.replace("sqlite:///", "")
    init_db(_db_path)
    _auth = TokenAuth.from_env()
    yield

app = FastAPI(title="BearWatch", lifespan=lifespan)

def _resolve_device(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization[7:]
    device_id = _auth.resolve(token)
    if device_id is None:
        raise HTTPException(status_code=401, detail="Unknown token")
    return device_id

@app.post("/api/v1/heartbeat", response_model=HeartbeatResponse)
def post_heartbeat(
    body: HeartbeatRequest,
    authorization: str | None = Header(default=None),
):
    device_id = _resolve_device(authorization)

    if body.device_info:
        ensure_device(_db_path, device_id, body.device_info)
    else:
        ensure_device(_db_path, device_id)

    record_heartbeat(
        _db_path, device_id,
        body.battery_pct, body.battery_temp_c, body.uptime_s,
        body.config_etag, body.clock_offset_ms,
    )

    # 設定配信: 端末の etag と最新の active config を比較
    active = get_active_config(_db_path, device_id)
    if active is not None:
        config, etag = active
        if etag != body.config_etag:
            return HeartbeatResponse(status="ok", config=config, config_etag=etag)

    return HeartbeatResponse(status="ok")
```

- [ ] **Step 4: `requirements.txt` に httpx を追加**

`requirements.txt` の `pytest>=8.0` の後に追加:
```
httpx>=0.27
```

- [ ] **Step 5: テストが PASS することを確認**

```bash
pip install httpx
pytest tests/test_heartbeat.py::TestHeartbeatEndpoint -v
```
Expected: 4 passed

- [ ] **Step 6: 全テストが PASS することを確認**

```bash
pytest tests/test_heartbeat.py -v
```
Expected: 11 passed (Models 3 + Auth 4 + Database 3 + Endpoint 4)

加えて既存テストも壊れていないことを確認:
```bash
python tests/test_config.py
```
Expected: PASS 38 / FAIL 0

- [ ] **Step 7: コミット**

```bash
git add server/api/app.py tests/conftest.py tests/test_heartbeat.py requirements.txt
git commit -m "feat(api): add heartbeat endpoint with token auth and SQLite storage"
```

---

## Chunk 2: fake_device, replay, CI

### Task 5: fake_device.py

**Files:**
- Create: `tools/fake_device/main.py`
- Test: `tests/test_heartbeat.py`（統合テストを追加）

- [ ] **Step 1: fake_device を使った統合テストを追加**

```python
# tests/test_heartbeat.py に追加
import subprocess
import time

class TestFakeDevice:
    def test_fake_device_sends_heartbeats(self, server_url):
        """fake_device がハートビートを3回送信して正常終了する"""
        proc = subprocess.run(
            [sys.executable, "tools/fake_device/main.py",
             "--server", server_url,
             "--token", "test-token-001",
             "--interval", "1",
             "--count", "3"],
            capture_output=True, text=True, timeout=30,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert proc.returncode == 0
        assert "heartbeat 1" in proc.stdout.lower() or "ok" in proc.stdout.lower()
```

- [ ] **Step 2: テストが FAIL することを確認**

```bash
pytest tests/test_heartbeat.py::TestFakeDevice -v
```

- [ ] **Step 3: fake_device.py を実装**

```python
# tools/fake_device/main.py
"""端末シミュレータ。サーバ API にハートビートを送信する。

Usage:
    python tools/fake_device/main.py --server http://localhost:8000 --token abc123
    python tools/fake_device/main.py --server http://localhost:8000 --token abc123 --interval 10 --count 5
"""
from __future__ import annotations
import argparse
import random
import sys
import time

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
    body = {
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

def run(server: str, token: str, interval: float, count: int | None) -> None:
    state = {
        "battery_pct": 100,
        "battery_temp_c": 25.0 + random.uniform(-2, 2),
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

            except httpx.HTTPStatusError as e:
                print(f"[heartbeat {i}] HTTP {e.response.status_code}: {e.response.text}",
                      file=sys.stderr)
                return
            except httpx.ConnectError:
                print(f"[heartbeat {i}] connection refused", file=sys.stderr)
                return

            # 状態を更新
            state["uptime_s"] += int(interval)
            state["battery_pct"] = max(0, state["battery_pct"] - random.choice([0, 0, 0, 1]))
            state["battery_temp_c"] = round(25.0 + random.uniform(-3, 5), 1)

            if count is None or i < count:
                time.sleep(interval)

def main():
    parser = argparse.ArgumentParser(description="BearWatch fake device")
    parser.add_argument("--server", required=True, help="Server URL (e.g. http://localhost:8000)")
    parser.add_argument("--token", required=True, help="Device authentication token")
    parser.add_argument("--interval", type=float, default=60, help="Heartbeat interval in seconds")
    parser.add_argument("--count", type=int, default=None, help="Number of heartbeats (None=infinite)")
    args = parser.parse_args()
    run(args.server, args.token, args.interval, args.count)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが PASS することを確認**

```bash
pytest tests/test_heartbeat.py::TestFakeDevice -v
```
Expected: 1 passed

- [ ] **Step 5: 全テストが PASS することを確認**

```bash
pytest tests/test_heartbeat.py -v
python tests/test_config.py
```
Expected: 12 passed / PASS 38

- [ ] **Step 6: コミット**

```bash
git add tools/fake_device/main.py tests/test_heartbeat.py
git commit -m "feat: add fake_device simulator for server API testing"
```

---

### Task 6: replay.py スケルトン

**Files:**
- Create: `tools/replay/main.py`
- Create: `tools/replay/generate_dummy_video.py`
- Test: `tests/test_replay.py`

- [ ] **Step 1: ダミー動画生成とreplayのテストを書く**

```python
# tests/test_replay.py
import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

class TestReplay:
    def test_generate_dummy_video(self, tmp_path):
        """ダミー動画が生成される"""
        from tools.replay.generate_dummy_video import generate
        out = str(tmp_path / "dummy.mp4")
        generate(out, frames=30, fps=10, width=320, height=240)
        assert os.path.exists(out)
        assert os.path.getsize(out) > 0

    def test_replay_headless(self, tmp_path):
        """ヘッドレスモードでフレームを読み込める"""
        from tools.replay.generate_dummy_video import generate
        from tools.replay.main import replay

        video = str(tmp_path / "dummy.mp4")
        generate(video, frames=15, fps=10, width=320, height=240)

        result = replay(video, headless=True)
        assert result["frames_read"] == 15
        assert result["width"] == 320
        assert result["height"] == 240
```

- [ ] **Step 2: テストが FAIL することを確認**

```bash
pytest tests/test_replay.py -v
```

- [ ] **Step 3: ダミー動画生成を実装**

```python
# tools/replay/generate_dummy_video.py
"""テスト用のダミー動画を生成する。

単色背景に白い矩形が水平移動する動画。外部素材不要。
"""
from __future__ import annotations
import cv2
import numpy as np

def generate(
    output_path: str,
    frames: int = 150,
    fps: int = 10,
    width: int = 640,
    height: int = 480,
) -> None:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

    rect_w, rect_h = 40, 40
    for i in range(frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (34, 120, 50)  # 暗い緑（森っぽい背景）
        x = int((i / frames) * (width - rect_w))
        y = height // 2 - rect_h // 2
        cv2.rectangle(frame, (x, y), (x + rect_w, y + rect_h), (255, 255, 255), -1)
        writer.write(frame)

    writer.release()

if __name__ == "__main__":
    generate("dummy_test.mp4")
    print("Generated dummy_test.mp4")
```

- [ ] **Step 4: replay.py を実装**

```python
# tools/replay/main.py
"""録画ファイルから検知パイプラインを再実行する。

Phase 0.5: フレーム読み込みと表示のスケルトンのみ。
Phase 1-Dev (M3): S1+S2 パイプラインを通す。
"""
from __future__ import annotations
import argparse

import cv2

def replay(video_path: str, headless: bool = False) -> dict:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frames_read = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frames_read += 1

        if not headless:
            ts = frames_read / fps if fps > 0 else 0
            cv2.imshow("replay", frame)
            print(f"frame {frames_read}  time={ts:.2f}s", end="\r")
            if cv2.waitKey(int(1000 / fps)) & 0xFF == ord("q"):
                break

    cap.release()
    if not headless:
        cv2.destroyAllWindows()
        print()

    return {"frames_read": frames_read, "width": width, "height": height, "fps": fps}

def main():
    parser = argparse.ArgumentParser(description="BearWatch video replay")
    parser.add_argument("video", help="Path to video file")
    parser.add_argument("--headless", action="store_true", help="No GUI (CI mode)")
    args = parser.parse_args()

    result = replay(args.video, headless=args.headless)
    print(f"\nDone: {result['frames_read']} frames, "
          f"{result['width']}x{result['height']} @ {result['fps']:.1f} fps")

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: テストが PASS することを確認**

```bash
pytest tests/test_replay.py -v
```
Expected: 2 passed

- [ ] **Step 6: コミット**

```bash
git add tools/replay/main.py tools/replay/generate_dummy_video.py tests/test_replay.py
git commit -m "feat: add replay.py skeleton and dummy video generator"
```

---

### Task 7: CI（GitHub Actions）

**Files:**
- Create: `.github/workflows/test.yml`
- Create: `tests/conftest.py`（Task 4 で作成済み）

- [ ] **Step 1: GitHub Actions ワークフローを作成**

```yaml
# .github/workflows/test.yml
name: Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run config tests
        run: python tests/test_config.py

      - name: Run pytest
        env:
          DEVICE_TOKENS: "test-token-001:device-001,test-token-002:device-002"
        run: pytest tests/ -v --ignore=tests/test_config.py
```

注: `test_config.py` は独自テストフレームワーク（`check()` 関数）を使っているため、
`python` で直接実行し、pytest からは除外する。

- [ ] **Step 2: ローカルで全テストが通ることを確認**

```bash
python tests/test_config.py
pytest tests/ -v --ignore=tests/test_config.py
```
Expected: PASS 38 / pytest 14 passed

- [ ] **Step 3: コミット**

```bash
mkdir -p .github/workflows
git add .github/workflows/test.yml
git commit -m "ci: add GitHub Actions workflow for pytest and config tests"
```

---

### Task 8: SPEC.md の D-1, D-5 更新

**Files:**
- Modify: `docs/SPEC.md`

- [ ] **Step 1: SPEC.md §2 の D-1 を決定済みに更新**

D-1 のセクションに以下を記録:
- 決定: 初期はセルフホスト（開発用 PC: Ryzen 7 7735HS / 32GB RAM / AMD 内蔵 GPU）
- CPU 推論（torchvision）で Phase 1 を実施
- 台数増加時にクラウド移行または NVIDIA GPU 追加

- [ ] **Step 2: SPEC.md §2 の D-5 を決定済みに更新**

D-5 のセクションに以下を記録:
- 決定: APK は手動更新（Google Drive 等から端末で DL）
- 設定パラメータはハートビート応答でリモート配信

- [ ] **Step 3: コミット**

```bash
git add docs/SPEC.md
git commit -m "docs: record D-1 (self-host) and D-5 (manual APK update) decisions"
```

---

### Task 9: 最終確認とプッシュ

- [ ] **Step 1: 全テスト実行**

```bash
python tests/test_config.py
pytest tests/ -v --ignore=tests/test_config.py
```

- [ ] **Step 2: fake_device の手動動作確認**

ターミナル 1:
```bash
DEVICE_TOKENS="test-token:dev-001" DATABASE_URL="sqlite:///./test_manual.db" \
  python -m uvicorn server.api.app:app --port 8000
```

ターミナル 2:
```bash
python tools/fake_device/main.py --server http://localhost:8000 --token test-token --interval 2 --count 5
```

- [ ] **Step 3: プッシュ**

```bash
git push origin main
```

- [ ] **Step 4: GitHub Actions が緑になることを確認**

https://github.com/kasagi314-cell/BearWatchTest_v1.0/actions で結果を確認

---

## Exit Criteria チェック

| タスク | 完了条件 | 検証方法 |
|---|---|---|
| サーバ API 最小骨格 | ハートビート受信 + 設定配信 | `TestHeartbeatEndpoint` が全 PASS |
| `tools/fake_device/main.py` | サーバ API にハートビート送信 | `TestFakeDevice` が PASS + 手動確認 |
| `tools/replay/main.py` | 動画読み込み + フレーム表示 | `TestReplay` が PASS |
| CI | PR ごとに pytest 自動実行 | GitHub Actions が緑 |
| D-1, D-5 | SPEC.md に記録 | 目視確認 |
