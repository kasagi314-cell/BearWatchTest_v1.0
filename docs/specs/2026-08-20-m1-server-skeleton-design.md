# M1 サーバ骨格 設計書

## 概要

Phase 1-Dev の最初のマイルストーン。既存のハートビート API（Phase 0.5）を拡張し、イベント受信・メディア保存・S4 推論・コマンド配信の骨格を構築する。

**ゴール**: `fake_device.py` からの E2E 疎通（イベント送信 → S4 判定 → コマンド受信 → 映像アップロード）

**スコープ外**: プライバシーマスキング（M6）、確認キュー UI（M6）、S5 映像判定（M10）、通報（M8）、`capture_now` / `reboot` コマンド（M3T 以降）

---

## アーキテクチャ

### ファイル構成

```
server/api/
├── app.py            ← APIRouter include。lifespan でモデルロード追加
├── auth.py           （既存・変更なし）
├── database.py       ← events, commands テーブル追加
├── models.py         ← イベント系 Pydantic モデル追加
├── events.py         ← POST /v1/events, /v1/events/{id}/still, /v1/events/{id}/video
├── inference.py      ← S4 推論（fasterrcnn_resnet50_fpn_v2）
└── commands.py       ← コマンドキュー管理

tools/fake_device/
├── main.py           ← 既存を拡張（イベント送信、コマンド受信）
└── dummy_media.py    ← ダミー画像・動画生成

tests/
├── test_events.py
├── test_inference.py
├── test_commands.py
└── test_e2e.py
```

---

## DB スキーマ拡張

### events テーブル

```sql
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'UPLOADED',
    detected_at TEXT NOT NULL,
    clock_offset_ms INTEGER,
    camera TEXT,
    roi_json TEXT,
    azimuth_deg REAL,
    elevation_deg REAL,
    estimated_distance_m REAL,
    estimated_size_m REAL,
    track_json TEXT,
    env_json TEXT,
    scores_json TEXT DEFAULT '{"s3": null, "s4": null, "s5": null}',
    still_path TEXT,
    video_path TEXT,
    review_json TEXT,
    notified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

**Wire format（`POST /v1/events` が受け取る JSON）は SPEC.md §7.1 のトップレベル構造に一致させる。** `azimuth_deg`, `elevation_deg`, `estimated_distance_m`, `estimated_size_m` は `roi` オブジェクトとは独立したトップレベルフィールド。DB カラムもこれに合わせる。

event_id は端末が採番する UUID。同じ event_id の再送は 200 を返すが上書きしない（べき等）。ただしメディア（still / video）は個別エンドポイントで後から追加・再送可能。

**状態遷移（サーバ側で管理する状態のみ）**:

SPEC.md §7.1 の全状態は `DETECTED → RECORDING → PENDING_UPLOAD → UPLOADED → ...` だが、`DETECTED` / `RECORDING` / `PENDING_UPLOAD` は端末内部の状態であり、サーバに到達した時点で `UPLOADED` 以降のみを管理する。

```
UPLOADED → SERVER_REJECTED（S4 が非動物と判定）
         → MEDIA_REQUESTED（S4 が動物と判定、映像を要求）
             → MEDIA_UPLOADED（映像受信済み）
                 → REVIEWED（人間がレビュー済み。M6 以降）
```

### JSON カラムの構造定義

各 `*_json` カラムに格納する構造を明示する。SPEC.md §7.1 のフィールドとのマッピング：

**`roi_json`**: ROI 座標のみ
```jsonc
{ "x": 0, "y": 0, "w": 0, "h": 0 }
```

**`track_json`**: トラック情報
```jsonc
{
  "duration_s": 0.0,
  "frames": 0,
  "speed_mps": 0.0,
  "direction_deg": 0.0,
  "straightness": 0.0
}
```

**`env_json`**: 環境情報
```jsonc
{
  "weather": null,
  "mean_luminance": 0,
  "global_luminance_delta": 0,
  "enclosure_temp_c": 0.0
}
```

**`scores_json`**: 各段の判定スコア
```jsonc
{ "s3": 0.0, "s4": null, "s5": null }
```

**`review_json`**: レビュー結果（M6 以降で使用）
```jsonc
{
  "label": null,
  "fp_cause": null,
  "reviewer_id": null,
  "reviewed_at": null
}
```

**`location` について**: SPEC.md §7.1 のイベント JSON には `location` (lat/lon) が含まれているが、CLAUDE.md の「座標をコミットしない」方針および §7.4（キャリブレーションファイルに座標を含む）を踏まえ、M1 ではイベントに `location` を含めない。端末が座標を送信すると DB に座標が蓄積され、データ漏洩リスクが高まる。位置情報が必要な場面（通報メッセージ等）では `device_id` → キャリブレーションファイルから逆引きする。**SPEC.md §7.1 の `location` フィールドにこの決定を注記として反映すること。**

### commands テーブル

```sql
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);
```

- `command_type`: M1 では "request_video" / "discard" の 2 種。`capture_now` / `reboot` は M3T 以降で追加（SPEC.md §8.1 参照）。テーブル構造は汎用的に作り、種別追加はカラム変更不要
- `delivered_at`: ハートビートで配信した日時。NULL = 未配信
- 配信済みコマンドは次回のハートビートに含めない

### heartbeats テーブル拡張

既存テーブルに `metrics_json TEXT` カラムを追加。§12 のメトリクスを丸ごと JSON 保存。

---

## API エンドポイント

全て HTTPS。認証は既存の Bearer トークン方式。

### POST /v1/heartbeat（既存拡張）

**リクエスト追加フィールド**:
- `metrics`: optional dict — §12 の端末メトリクス

**レスポンス拡張**:
```jsonc
{
  "status": "ok",
  "server_time": "2026-08-20T12:00:00Z",
  "config_etag": "abc123",
  "config": { ... },
  "commands": [
    { "type": "request_video", "event_id": "uuid" },
    { "type": "discard", "event_id": "uuid" }
  ]
}
```

- `server_time`: 常に返す。端末がドリフト補正に使う
- `commands`: 未配信コマンドの配列。空なら `[]`。配信後に `delivered_at` を記録

### POST /v1/events（新規）

multipart/form-data:
- `event` パート: JSON（イベントデータ）
- `thumbnail` パート: JPEG 画像（任意）

処理フロー:
1. event_id でべき等チェック。既存なら `200 {"event_id": "...", "status": "既存のstatus"}` を返す
2. events テーブルに INSERT（status = "UPLOADED"）
3. サムネイルを `MEDIA_ROOT/{device_id}/{event_id}/thumbnail.jpg` に保存
4. S4 推論を同期実行（CPU で 1 枚数秒。Phase 1 は端末 1 台のため許容範囲。将来的に端末台数が増えた場合はバックグラウンドワーカーへの非同期化を検討する）
5. 推論結果に応じて commands テーブルに INSERT:
   - 動物判定 → `request_video`、イベント status を `MEDIA_REQUESTED` に更新
   - 非動物判定 → `discard`、イベント status を `SERVER_REJECTED` に更新
6. scores.s4 を更新
7. レスポンス: `200 {"event_id": "...", "status": "...", "s4_result": {"is_animal": bool, "score": float}}`

端末はレスポンスの `s4_result` で即座に録画のアップロード/破棄を判断でき、ハートビート待ち（最大 60 秒）を省ける。コマンドキューは冗長な配信経路として残す（レスポンスを受け取れなかった場合のフォールバック）。

**端末側のタイムアウト**: 推論に数秒かかるため、端末のアップロードキューは接続タイムアウト 30 秒以上を推奨。

### POST /v1/events/{id}/still（新規）

- バイナリボディ（JPEG）
- `MEDIA_ROOT/{device_id}/{event_id}/still.jpg` に保存
- events.still_path を更新

### POST /v1/events/{id}/video（新規）

- バイナリボディ（MP4）
- `MEDIA_ROOT/{device_id}/{event_id}/video.mp4` に保存
- events.video_path を更新
- events.status を `MEDIA_UPLOADED` に遷移

### GET /v1/config（新規）

- `If-None-Match` ヘッダで現在の etag を送信
- 変更なし → `304 Not Modified`
- 変更あり → `200` + 設定 JSON + `ETag` ヘッダ
- ハートビートでも設定を受け取れるが、起動時の初回取得用

**設定の保存元**: 既存の `device_configs` テーブル（Phase 0.5 で作成済み）をそのまま使う。`shared/config_schema.json` はパラメータの定義と値域検証のスキーマであり、実際の端末別設定値は DB に保存する。M1 ではデフォルト値で運用し、設定変更 UI は M6 以降で実装する。設定を DB に投入するには直接 SQLite を操作するか、管理用スクリプトを使う。

---

## S4 推論

### モデル

torchvision `fasterrcnn_resnet50_fpn_v2`（COCO 事前学習済み、BSD-3-Clause）。

### デバイス選択

起動時に `torch.cuda.is_available()` で自動判定。GPU があれば CUDA、なければ CPU。将来の GPU 追加時にコード変更不要。

### COCO 動物クラス

```python
COCO_ANIMAL_IDS = {
    16: "bird", 17: "cat", 18: "dog", 19: "horse",
    20: "sheep", 21: "cow", 22: "elephant", 23: "bear",
    24: "zebra", 25: "giraffe",
}
```

初年度はこの和集合で「動物」と判定。種判定は S5（M10）で行う。

### 判定ロジック

1. サムネイルを PIL で読み込み → torchvision transforms でテンソル化
2. モデルで推論
3. 検出ボックスのうち、COCO 動物クラスに該当し信頼度 >= 閾値（デフォルト 0.3）のものがあれば「動物」
4. 最大スコアを scores.s4 に記録

### エラー処理

推論失敗時はフェイルオープン（動物扱い）。推論エラーで検知を握り潰さない。エラーログを記録し、`request_video` コマンドを発行する。

**推論タイムアウト**: 20 秒で強制フェイルオープン。端末側の接続タイムアウト（30 秒推奨）より短くし、正常なレスポンスを返せるようにする。

### インターフェース

```python
# server/api/inference.py

def load_model() -> None:
    """lifespan で 1 回呼ぶ。グローバルにモデルを保持"""

def run_s4(image_path: str) -> S4Result:
    """推論実行。S4Result は is_animal, score, detections を含む"""
```

---

## サーバ側メトリクス

CLAUDE.md の方針「機能を実装したら同時にメトリクスを入れる」に従い、M1 で S4 推論を実装するのと同時に以下のサーバ側メトリクスを記録する。

### 記録方法

`server_metrics` テーブルを追加。1 時間ごとのバケットで集計。

```sql
CREATE TABLE IF NOT EXISTS server_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_hour TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    created_at TEXT NOT NULL
);
```

### M1 で記録するメトリクス

| メトリクス名 | 説明 | SPEC.md §12 対応 |
|---|---|---|
| `s4_input_count` | S4 に入力された画像数 | S4 入出力件数 |
| `s4_animal_count` | S4 が動物と判定した数 | 同上 |
| `s4_non_animal_count` | S4 が非動物と判定した数 | 同上 |
| `s4_error_count` | S4 推論エラー数 | 同上 |
| `s4_score_sum` / `s4_score_count` | スコア分布計算用 | S4 スコア分布 |
| `events_received` | 受信イベント総数 | ラベル別の件数（M6 以降で分化） |
| `media_bytes_received` | 受信メディアの合計バイト数 | 通信量 |

メトリクスの記録は各エンドポイントの処理内で同期的に行う。方式は `INSERT OR REPLACE` による UPSERT で、同一バケット（`bucket_hour` + `metric_name`）に対してインクリメントする。行数は「メトリクス種別数 × 経過時間数」に留まり、際限なく増えることはない。ダッシュボード表示は M7 で実装。

---

## メディア保存

```
MEDIA_ROOT/          ← .env で指定（デフォルト ./media）
└── {device_id}/
    └── {event_id}/
        ├── thumbnail.jpg
        ├── still.jpg
        └── video.mp4
```

- ディレクトリは受信時に自動作成（`os.makedirs(exist_ok=True)`）
- `media/` は `.gitignore` に含める

---

## fake_device.py 拡張

### 追加機能

| 機能 | 説明 |
|---|---|
| ダミーイベント送信 | ランダムな ROI・トラック情報 + テスト画像で `POST /v1/events` |
| コマンド受信 | ハートビートレスポンスの `commands` を読み取り |
| 映像アップロード | `request_video` 受信時に `POST /v1/events/{id}/video` にダミー動画送信 |
| E2E シナリオ | `--scenario e2e` で上記一連の流れを自動実行 |

### ダミーメディア生成

`tools/fake_device/dummy_media.py`:
- `generate_dummy_image(width, height) -> bytes`: 背景 + ランダムな矩形の JPEG
- `generate_dummy_video(duration_s, fps) -> bytes`: 同様の MP4

---

## テスト戦略

| テストファイル | 対象 | CI |
|---|---|---|
| `tests/test_events.py` | イベント CRUD、べき等性、状態遷移 | 通常実行 |
| `tests/test_inference.py` | S4 推論インターフェース（モック）+ フェイルオープン検証 | **通常実行**（モック推論） |
| `tests/test_inference_slow.py` | 実モデルロード + 実推論 | `@pytest.mark.slow`（モデル DL が重い） |
| `tests/test_commands.py` | コマンドキュー、配信済みマーク | 通常実行 |
| `tests/test_e2e.py` | fake_device の E2E シナリオ（モック推論） | **通常実行** |
| `tests/test_e2e_slow.py` | 実モデルでの E2E | `@pytest.mark.slow` |

**モック推論の方針**: `inference.py` の `run_s4` をモンキーパッチまたは DI で差し替え、固定の `S4Result` を返す。これにより CI で推論インターフェースの型チェック、フェイルオープン、コマンド発行ロジックを検証できる。

CI の `.github/workflows/test.yml` に `-m "not slow"` を追加。

---

## 完了条件

M1 の Exit Criteria（SPEC.md §14）:

> `fake_device.py` からの E2E 疎通

具体的には:
1. `fake_device.py --scenario e2e` を実行
2. ダミーイベント + サムネイルがサーバに送信される
3. S4 推論が実行され、scores.s4 が記録される
4. ハートビートで `request_video` または `discard` コマンドを受信する
5. `request_video` の場合、ダミー動画がアップロードされる
6. イベントの状態が正しく遷移する

全てが `fake_device.py` 単体で確認でき、pytest でも自動検証できる状態。
