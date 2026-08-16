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
