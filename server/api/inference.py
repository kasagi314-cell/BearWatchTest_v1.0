"""S4 推論 — torchvision fasterrcnn_resnet50_fpn_v2"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field

import torch
from torchvision import transforms
from PIL import Image

logger = logging.getLogger(__name__)

COCO_ANIMAL_IDS: dict[int, str] = {
    16: "bird", 17: "cat", 18: "dog", 19: "horse",
    20: "sheep", 21: "cow", 22: "elephant", 23: "bear",
    24: "zebra", 25: "giraffe",
}

S4_THRESHOLD = 0.3
S4_TIMEOUT_S = 20

_model = None
_device = None
_executor = ThreadPoolExecutor(max_workers=1)


@dataclass
class S4Result:
    is_animal: bool
    score: float
    detections: list[dict] = field(default_factory=list)
    error: str | None = None


def load_model() -> None:
    """モデルをロードする。lifespan で 1 回呼ぶ。"""
    global _model, _device
    from torchvision.models.detection import (
        fasterrcnn_resnet50_fpn_v2,
        FasterRCNN_ResNet50_FPN_V2_Weights,
    )

    _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("S4 inference device: %s", _device)

    weights = FasterRCNN_ResNet50_FPN_V2_Weights.DEFAULT
    _model = fasterrcnn_resnet50_fpn_v2(weights=weights)
    _model.to(_device)
    _model.eval()
    logger.info("S4 model loaded: fasterrcnn_resnet50_fpn_v2")


def _run_inference(image_path: str, threshold: float) -> S4Result:
    """推論の実処理（タイムアウトなし）"""
    if _model is None:
        return S4Result(is_animal=True, score=0.0, error="model not loaded")

    img = Image.open(image_path).convert("RGB")
    transform = transforms.ToTensor()
    tensor = transform(img).to(_device)

    with torch.no_grad():
        outputs = _model([tensor])

    output = outputs[0]
    boxes = output["boxes"]
    scores = output["scores"]
    labels = output["labels"]

    detections = []
    max_animal_score = 0.0

    for i in range(len(scores)):
        label_id = int(labels[i])
        score = float(scores[i])
        if label_id in COCO_ANIMAL_IDS and score >= threshold:
            detections.append({
                "label_id": label_id,
                "label_name": COCO_ANIMAL_IDS[label_id],
                "score": score,
                "box": [float(x) for x in boxes[i]],
            })
            max_animal_score = max(max_animal_score, score)

    return S4Result(
        is_animal=len(detections) > 0,
        score=max_animal_score,
        detections=detections,
    )


def run_s4(image_path: str, threshold: float = S4_THRESHOLD) -> S4Result:
    """S4 推論を実行する。タイムアウト・エラー時はフェイルオープン。"""
    try:
        if _model is None:
            return S4Result(is_animal=True, score=0.0, error="model not loaded")

        future = _executor.submit(_run_inference, image_path, threshold)
        return future.result(timeout=S4_TIMEOUT_S)

    except FuturesTimeoutError:
        logger.error("S4 inference timed out after %ds, fail-open", S4_TIMEOUT_S)
        return S4Result(is_animal=True, score=0.0, error=f"timeout ({S4_TIMEOUT_S}s)")

    except Exception as e:
        logger.error("S4 inference failed, fail-open: %s", e)
        return S4Result(is_animal=True, score=0.0, error=str(e))
