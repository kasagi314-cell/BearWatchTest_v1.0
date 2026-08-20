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
        # Windows では SQLite の WAL ファイルがロックを保持し続けることがあるため
        # ignore_errors=True で teardown エラーを回避する
        shutil.rmtree(self.tmp, ignore_errors=True)

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
