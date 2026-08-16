"""録画ファイルから検知パイプラインを再実行する。

Phase 0.5: フレーム読み込みと表示のスケルトンのみ。
Phase 1-Dev (M3): S1+S2 パイプラインを通す。
"""
from __future__ import annotations

import argparse

import cv2


def replay(video_path: str, headless: bool = False) -> dict:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frames_read = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frames_read += 1

        if not headless:
            ts = frames_read / fps if fps > 0 else 0
            cv2.imshow("replay", frame)
            print(f"frame {frames_read}  time={ts:.2f}s", end="\r")
            if cv2.waitKey(int(1000 / fps)) & 0xFF == ord("q"):
                break

    cap.release()
    if not headless:
        cv2.destroyAllWindows()
        print()

    return {"frames_read": frames_read, "width": width, "height": height, "fps": fps}


def main():
    parser = argparse.ArgumentParser(description="BearWatch video replay")
    parser.add_argument("video", help="Path to video file")
    parser.add_argument("--headless", action="store_true", help="No GUI (CI mode)")
    args = parser.parse_args()

    result = replay(args.video, headless=args.headless)
    print(f"\nDone: {result['frames_read']} frames, "
          f"{result['width']}x{result['height']} @ {result['fps']:.1f} fps")


if __name__ == "__main__":
    main()
