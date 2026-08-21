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
        import os
        project_root = str(Path(__file__).resolve().parents[1])
        env = os.environ.copy()
        env["PYTHONPATH"] = project_root
        proc = subprocess.run(
            [sys.executable, "tools/fake_device/main.py",
             "--server", server_url,
             "--token", "test-token-001",
             "--scenario", "e2e"],
            capture_output=True, text=True, timeout=60,
            cwd=project_root,
            env=env,
        )
        assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        # M1 Exit Criteria の検証
        assert "E2E Step 1" in proc.stdout          # ハートビート送信
        assert "E2E Step 2" in proc.stdout          # イベント送信
        assert "E2E Step 3" in proc.stdout          # コマンド受信
        assert "E2E Result" in proc.stdout          # 結果出力
        assert "video_uploaded: True" in proc.stdout    # 映像アップロード成功
        assert "remaining_commands: 0" in proc.stdout   # コマンドが消えている
