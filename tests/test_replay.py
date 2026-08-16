import sys
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
