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
