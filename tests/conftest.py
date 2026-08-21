import os
import sys
import socket
import subprocess
import time
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
    env["MEDIA_ROOT"] = str(tmp / "media")
    env["SKIP_MODEL_LOAD"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.api.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    url = f"http://127.0.0.1:{port}"

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
