"""ダミー画像・動画生成"""
from __future__ import annotations

import io


def generate_dummy_jpeg(width: int = 640, height: int = 480) -> bytes:
    """テスト用のダミー JPEG を生成して bytes で返す"""
    from PIL import Image, ImageDraw
    import random

    img = Image.new("RGB", (width, height), color=(34, 139, 34))
    draw = ImageDraw.Draw(img)
    x1 = random.randint(0, width // 2)
    y1 = random.randint(0, height // 2)
    x2 = x1 + random.randint(30, 100)
    y2 = y1 + random.randint(40, 120)
    draw.rectangle([x1, y1, x2, y2], fill=(139, 69, 19))

    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def generate_dummy_mp4_bytes(size: int = 4096) -> bytes:
    """テスト用のダミー MP4 バイト列を返す。有効な MP4 ではないが、アップロードテスト用。"""
    return b"\x00" * size
