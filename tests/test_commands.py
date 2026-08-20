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
        shutil.rmtree(self.tmp, ignore_errors=True)

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
