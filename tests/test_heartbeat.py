"""ハートビート API の統合テスト。

サーバを実プロセスで起動し、実 TCP 接続で検証する。
FastAPI の TestClient（インプロセス呼び出し）は使わない。
"""
import subprocess
import sys
import os
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlite3

import pytest
import httpx
from server.api.models import HeartbeatRequest, HeartbeatResponse
from server.api.auth import TokenAuth
from server.api.database import init_db, record_heartbeat, get_active_config


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


class TestDatabase:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test.db")
        init_db(self.db_path)
        self.conn = sqlite3.connect(self.db_path)

    def teardown_method(self):
        self.conn.close()
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


class TestFakeDevice:
    def test_fake_device_sends_heartbeats(self, server_url):
        """fake_device がハートビートを3回送信して正常終了する"""
        proc = subprocess.run(
            [sys.executable, "tools/fake_device/main.py",
             "--server", server_url,
             "--token", "test-token-001",
             "--interval", "0.5",
             "--count", "3"],
            capture_output=True, text=True, timeout=30,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert proc.returncode == 0
        assert "heartbeat 1" in proc.stdout.lower()
        assert "heartbeat 3" in proc.stdout.lower()
