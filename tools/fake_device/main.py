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
