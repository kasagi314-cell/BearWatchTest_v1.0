# M1 サーバ骨格 完了レポート

**日付**: 2026-08-22
**ブランチ**: `feat/m1-server-skeleton` (PR #1)
**計画書**: `docs/plans/2026-08-20-m1-server-skeleton.md`
**設計書**: `docs/specs/2026-08-20-m1-server-skeleton-design.md`

---

## Exit Criteria の達成状況

> `fake_device.py` からの E2E 疎通（イベント送信 → S4 判定 → コマンド受信 → 映像アップロード）

**達成。** `fake_device.py --scenario e2e` で以下のフローが自動検証される:

1. 初回ハートビート送信（device_info 登録）
2. ダミーイベント + サムネイル送信 → S4 推論（フェイルオープン）→ `request_video` コマンド発行
3. ハートビートでコマンド受信（`server_time` 含む）
4. ダミー動画アップロード → ステータス `MEDIA_UPLOADED` に遷移
5. 最終ハートビートで配信済みコマンドが消えていることを確認

E2E テスト（`tests/test_e2e.py`）で自動検証済み。

---

## 実装サマリ

### コミット一覧（12 件）

| # | コミット | 内容 |
|---|---|---|
| 1 | `cbbea02` | DB スキーマ拡張（events, commands, server_metrics テーブル + heartbeats マイグレーション） |
| 2 | `5b84820` | イベント CRUD 関数 |
| 3 | `9c02e4d` | コマンドキュー（enqueue, get pending, mark delivered） |
| 4 | `7b449df` | サーバ側メトリクス記録（時間バケット UPSERT） |
| 5 | `3745dca` | S4 推論モジュール（フェイルオープン, GPU 自動検出, 20 秒タイムアウト） |
| 6 | `c5380f3` | Pydantic モデル拡張 + 認証 DRY 化 |
| 7 | `fa39d7b` | イベント API エンドポイント + app.py 刷新 |
| 8 | `77ab8da` | GET /v1/config（ETag 対応） |
| 9 | `db4c81b` | fake_device.py 拡張（E2E シナリオ） |
| 10 | `ebf03d9` | E2E テスト（6 件） |
| 11 | `90e07fa` | CI 更新（SKIP_MODEL_LOAD + slow marker） |
| 12 | `a766e06` | テスト起動タイムアウト修正 |

### 新規ファイル

| ファイル | 責務 |
|---|---|
| `server/api/events.py` | POST /v1/events, /v1/events/{id}/still, /v1/events/{id}/video |
| `server/api/config.py` | GET /v1/config（ETag / 304 対応） |
| `server/api/inference.py` | S4 推論（torchvision fasterrcnn_resnet50_fpn_v2） |
| `server/api/commands.py` | コマンドキュー管理 |
| `server/api/metrics.py` | サーバ側メトリクス記録 |
| `tools/fake_device/dummy_media.py` | ダミー JPEG / MP4 生成 |
| `tests/test_events.py` | イベント CRUD + メトリクスのユニットテスト |
| `tests/test_inference.py` | S4 推論インターフェーステスト（モック） |
| `tests/test_commands.py` | コマンドキューのユニットテスト |
| `tests/test_e2e.py` | E2E 統合テスト |

### 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `server/api/database.py` | events/commands/server_metrics テーブル追加、イベント CRUD、record_heartbeat に metrics_json 追加 |
| `server/api/models.py` | EventData/EventResponse 追加、HeartbeatResponse に server_time/commands 追加 |
| `server/api/auth.py` | resolve_device_or_raise 追加 |
| `server/api/app.py` | app.state ベースに刷新、events/config router、lifespan でモデルロード、HB にコマンド配信 |
| `tools/fake_device/main.py` | イベント送信・コマンド受信・E2E シナリオ追加 |
| `tests/conftest.py` | MEDIA_ROOT / SKIP_MODEL_LOAD 追加、起動タイムアウト延長 |
| `.github/workflows/test.yml` | SKIP_MODEL_LOAD=1 / -m "not slow" 追加 |

---

## テスト結果

```
43 passed in 14s
```

| テストファイル | テスト数 | 内容 |
|---|---|---|
| test_heartbeat.py | 16 | モデル検証、認証、DB、ハートビート API、config 404、fake_device |
| test_events.py | 10 | イベント CRUD (8) + メトリクス (2) |
| test_commands.py | 4 | コマンドキュー |
| test_inference.py | 5 | S4 推論インターフェース（モック） |
| test_e2e.py | 6 | イベント送信、べき等性、映像アップロード、コマンド配信、E2E シナリオ |
| test_replay.py | 2 | replay.py（既存） |

---

## 実装時の判断と注意点

### S4 推論のフェイルオープン

推論エラー・タイムアウト（20 秒）・モデル未ロード時は `is_animal=True`（動物扱い）を返す。SPEC.md の「適合率がすべてに優先する」方針により、推論失敗で検知を握り潰さない設計。

### SKIP_MODEL_LOAD 環境変数

CI やテストではモデルロード（数百 MB のダウンロード + GPU 初期化）をスキップ。`_model = None` のため全てフェイルオープン → 全イベントに `request_video` が発行される。E2E テストはこの挙動を前提に書かれている。

### app.state への移行

Phase 0.5 の app.py はモジュールレベル変数（`_db_path`, `_auth`）を使っていたが、M1 で `app.state` ベースに移行。events.py / config.py のルーターから `request.app.state` 経由でアクセスできるようになった。

### python-multipart 依存の追加

FastAPI の `Form` / `File` アップロードには `python-multipart` パッケージが必要。M1 実装中に追加。

### conftest.py の起動タイムアウト

torch/torchvision の import が遅い環境（初回起動時）に対応するため、サーバ起動待ちを 5 秒 → 30 秒に延長。

---

## Next Actions

1. **PR #1 のマージ** — CI 緑確認後に main へマージ
2. **M2: 端末の撮影と常駐** — Kotlin で Camera2 API + Foreground Service。全端末で 72 時間連続稼働
3. **M3: 端末 S1+S2** — 背景差分 + トラック生成。replay.py で録画から再現
4. **M3T: 試験モード** — stage_trace で各段のフィルタ棄却を記録。Phase 1-Test の前提
