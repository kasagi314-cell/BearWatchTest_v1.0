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
        x = int((i / max(frames - 1, 1)) * (width - rect_w))
        y = height // 2 - rect_h // 2
        cv2.rectangle(frame, (x, y), (x + rect_w, y + rect_h), (255, 255, 255), -1)
        writer.write(frame)

    writer.release()


if __name__ == "__main__":
    generate("dummy_test.mp4")
    print("Generated dummy_test.mp4")
