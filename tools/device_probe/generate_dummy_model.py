"""
TFLite ベンチマーク用のダミーモデルを生成する。

入力: [1, 96, 96, 3] float32
出力: [1, 2] float32（softmax）

生成されたファイルを assets/ に配置する:
  app/src/main/assets/dummy_model.tflite

依存: pip install tensorflow  (または tf-nightly)

tensorflow が重い場合は ai-edge-torch や tflite-support でも可。
"""

import os
import numpy as np

def generate_with_tf():
    """TensorFlow を使ってダミー TFLite モデルを生成する。"""
    import tensorflow as tf

    # 最小限の分類モデル
    model = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(96, 96, 3)),
        tf.keras.layers.GlobalAveragePooling2D(),
        tf.keras.layers.Dense(2, activation='softmax'),
    ])
    model.compile(optimizer='adam', loss='sparse_categorical_crossentropy')

    # 変換
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    tflite_model = converter.convert()

    return tflite_model


def generate_with_torch():
    """PyTorch + ai-edge-torch を使って生成する（tensorflow が入っていない場合）。"""
    import torch
    import torch.nn as nn

    class DummyClassifier(nn.Module):
        def __init__(self):
            super().__init__()
            self.pool = nn.AdaptiveAvgPool2d(1)
            self.fc = nn.Linear(3, 2)

        def forward(self, x):
            # x: [1, 96, 96, 3] → [1, 3, 96, 96]
            x = x.permute(0, 3, 1, 2)
            x = self.pool(x).flatten(1)
            return torch.softmax(self.fc(x), dim=1)

    model = DummyClassifier().eval()

    # ONNX 経由で TFLite に変換する方法もあるが、
    # ここでは ai-edge-torch を試す
    try:
        import ai_edge_torch
        sample = torch.randn(1, 96, 96, 3)
        edge_model = ai_edge_torch.convert(model, (sample,))
        edge_model.export("dummy_model.tflite")
        with open("dummy_model.tflite", "rb") as f:
            return f.read()
    except ImportError:
        raise RuntimeError(
            "ai-edge-torch が見つかりません。\n"
            "pip install tensorflow または pip install ai-edge-torch を実行してください。"
        )


def main():
    out_dir = os.path.join(
        os.path.dirname(__file__),
        "app", "src", "main", "assets"
    )
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "dummy_model.tflite")

    try:
        tflite_bytes = generate_with_tf()
    except ImportError:
        print("tensorflow が見つかりません。PyTorch で試みます...")
        tflite_bytes = generate_with_torch()

    with open(out_path, "wb") as f:
        f.write(tflite_bytes)

    size_kb = len(tflite_bytes) / 1024
    print(f"生成完了: {out_path} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
