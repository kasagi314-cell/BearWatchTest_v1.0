# bear-watch

タブレット端末とサーバによる、ツキノワグマの監視・検知システム。

春から秋、早朝から夕方にかけて、住宅街および里山地域の田畑・道路脇を監視し、
**猟友会および警察へ通報する**。住民向けの即時警報は行わない。

## 最初に読むもの

1. **`docs/SPEC.md`** — 要件定義・実装仕様・検証計画。単一の正
2. **`CLAUDE.md`** — Claude Code への指示。守るべき原則

## セットアップ

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env               # SMTP 等を記入

python tests/test_config.py        # 40件通れば OK
```

### Windows（PowerShell）

```powershell
python -m venv .venv

# 実行ポリシーが Restricted の場合、Activate.ps1 が実行できない。
# 以下のどちらかで一時的に許可する（管理者権限不要）。
Set-ExecutionPolicy -Scope Process RemoteSigned
# または: .venv\Scripts\Activate.ps1 を右クリック→「プロパティ」→「ブロックの解除」

.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env        # SMTP 等を記入

python tests/test_config.py        # 40件通れば OK
```

> **注意: torch のサイズ**
> `requirements.txt` に含まれる `torch` + `torchvision` は CUDA 付きで **2 GB 超**になる。
> GPU 推論が不要な開発環境では CPU 版を先に入れるとダウンロードが軽くなる。
>
> ```powershell
> # CPU 版を明示的にインストール（約 200 MB）
> pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
> pip install -r requirements.txt   # 残りの依存を入れる（torch は既に入っているのでスキップされる）
> ```

## 実装済みのもの

| パス | 内容 | 状態 |
|---|---|---|
| `shared/config_schema.json` | 設定パラメータの定義（29項目） | 完成 |
| `server/config/validator.py` | 値域チェック・パラメータ間制約・強制クランプ | 完成 |
| `reference/config_rollback_reference.py` | 設定ロールバック状態機械の参照実装 | 完成（Kotlin へ移植する） |
| `tests/test_config.py` | 上記2つの検証 40件 | 完成 |
| `server/eval/metrics.py` | Accuracy / Precision / Recall、運用点探索 | 完成 |
| `server/notify/email_notifier.py` | メール通報と取消通知 | 完成 |

## これから作るもの

`docs/SPEC.md` §14 のマイルストーン参照。**M0（`tools/device_probe`）が最優先。**
端末の素性が確定するまで、他の設計判断はすべて仮のもの。

## 使い方

```bash
# 設定の検証
python server/config/validator.py                    # 既定値を検証
python server/config/validator.py my_config.json     # 任意の設定を検証

# 評価
python server/eval/make_test_events.py -n 2000 -o /tmp/e.jsonl
python server/eval/metrics.py /tmp/e.jsonl --s5 0.75
python server/eval/metrics.py /tmp/e.jsonl --known-positives 120   # 真の再現率
```

## 注意

- **撮影データと座標を含む校正ファイルはコミットしない。** 通行人が写り込むため個人情報にあたる
- **推論は torchvision（BSD-3-Clause）のみ。** Ultralytics YOLO は AGPL-3.0 のため使わない
- コミット前に `git status` を確認する
