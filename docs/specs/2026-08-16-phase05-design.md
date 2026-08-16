# Phase 0.5 設計書

日付: 2026-08-16

## 目的

Phase 0 完了後の開発基盤を整備する。端末実機なしでサーバ開発・閾値調整を可能にし、CI でリグレッションを防止する。

## 決定事項

| ID | 決定内容 |
|---|---|
| D-1 | 初期はこの PC でセルフホスト（CPU 推論）。拡大時にクラウド移行 |
| D-5 | APK は手動更新（Google Drive 等から端末で DL）。設定はハートビート経由でリモート配信 |

## 成果物と Exit Criteria

| タスク | 完了条件 |
|---|---|
| サーバ API 最小骨格 | ハートビート受信 + 設定配信が動作する |
| `tools/fake_device/main.py` | サーバ API にハートビートを送信し、設定を受信できる |
| `tools/replay/main.py` スケルトン | 動画ファイルを読み込んでフレームを順に表示できる |
| CI（GitHub Actions） | PR ごとに `pytest tests/` が自動実行される |

## 設計

### 1. サーバ API 最小骨格

**ファイル:** `server/api/app.py`

**エンドポイント:**

```
POST /api/v1/heartbeat
  Authorization: Bearer <token>
```

**リクエスト:**
```json
{
  "battery_pct": 69,
  "battery_temp_c": 28.0,
  "uptime_s": 3600,
  "config_etag": "abc123...",
  "clock_offset_ms": -42,
  "device_info": {
    "model": "KOB-W09",
    "android_api": 24,
    "app_version": "0.1.0"
  }
}
```

`device_info` は初回接続時またはアプリ更新後にのみ含める。

**レスポンス:**
```json
{
  "status": "ok",
  "config": null,
  "config_etag": null
}
```

設定変更がある場合のみ `config` と `config_etag` に値が入る。

**認証方式:**
- トークンベース（Bearer）
- トークンからサーバ側で device_id を解決。端末は device_id を知る必要なし
- トークンは `.env` の `DEVICE_TOKENS` に `token:device_id` のマッピングで保持

**データベース:**
- SQLite（`bearwatch.db`）
- `devices` テーブル: id, token_hash, name, device_info, created_at
- `heartbeats` テーブル: id, device_id, timestamp_utc, battery_pct, battery_temp_c, uptime_s, config_etag, clock_offset_ms
- `device_configs` テーブル: id, device_id, config_json, etag, created_at, active

**配信ロジック:**
1. トークンから device_id を特定（不正トークンは 401）
2. ハートビートを記録
3. `device_info` があればデバイス情報を更新
4. 端末の `config_etag` と最新の active config の etag を比較
5. 異なれば `ConfigValidator.validate_and_clamp()` を通した設定を返す

### 2. fake_device.py

**ファイル:** `tools/fake_device/main.py`

**機能:**
- コマンドラインから起動し、指定間隔でハートビートを送信
- バッテリー残量を緩やかに減少させる（リアルなシミュレーション）
- サーバから新設定が返ってきたら表示
- 初回接続時に `device_info` を付加
- Ctrl+C で停止

**使い方:**
```bash
python tools/fake_device/main.py \
  --server http://localhost:8000 \
  --token abc123 \
  --interval 10
```

**テスト連携:**
- `tests/test_heartbeat.py` から同じコードを import
- テスト時もサーバプロセスを実際に起動し、localhost に HTTP リクエストを送信
- FastAPI の TestClient（インプロセス）は使わない。実 TCP 接続で検証

### 3. replay.py スケルトン

**ファイル:** `tools/replay/main.py`

**Phase 0.5 の範囲:**
- 動画ファイル（mp4）を OpenCV で読み込み
- フレームを順に表示（ウィンドウ or ヘッドレス）
- フレーム番号・タイムスタンプを標準出力に表示
- `--headless` オプションでウィンドウなし（CI 用）

**将来（M3）:**
- S1（背景差分）→ S2（トラック生成）のパイプラインを通す
- `stage_trace` で各候補の棄却理由を記録

**検証用サンプル:**
- OpenCV でダミー動画（単色背景 + 矩形移動）を生成するヘルパーを含む

### 4. CI（GitHub Actions）

**ファイル:** `.github/workflows/test.yml`

**トリガー:** PR を main に向けたとき + main への push

**ジョブ:**
1. Python 3.11 セットアップ
2. `pip install -r requirements.txt`
3. `pytest tests/ -v`

**将来の拡張（M1+）:**
- `fake_device.py` による E2E テスト
- `replay.py` による回帰テスト（テストセット整備後）
