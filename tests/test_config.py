"""値域チェックとロールバック状態機械の検証。

実際に起こりうる事故を机上で再現する。
  - 不正な設定が来た
  - サーバに繋がらなくなる設定を掴んだ
  - 試用中に再起動した / クラッシュループになった
  - 同じ壊れた設定が再送された
  - 試用期間が短すぎて正しい設定でも必ず戻ってしまう
"""

import os
import shutil
import tempfile

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.config.validator import ConfigValidator
from reference.config_rollback_reference import ConfigManager, STABLE, TRIAL, ROLLED_BACK

OK, NG = 0, 0


def check(cond, label):
    global OK, NG
    if cond:
        OK += 1
        print(f"  PASS  {label}")
    else:
        NG += 1
        print(f"  FAIL  {label}")


class Clock:
    def __init__(self):
        self.t = 1_700_000_000_000

    def __call__(self):
        return self.t

    def advance(self, sec):
        self.t += int(sec * 1000)


# ==================================================================== 値域チェック

def test_validator():
    print("\n[1] 値域チェック")
    v = ConfigValidator()

    r = v.validate_and_clamp(v.defaults())
    check(r.ok, "既定値はそのまま通る")

    cfg = v.defaults(); cfg["capture.interval_ms"] = 0
    check(not v.validate(cfg).ok, "下限を下回る値を弾く")

    cfg = v.defaults(); cfg["s1.fg_threshold"] = 999
    check(not v.validate(cfg).ok, "上限を超える値を弾く")

    cfg = v.defaults(); cfg["s3.enabled"] = "true"
    check(not v.validate(cfg).ok, "型違い(文字列を真偽値に)を弾く")

    cfg = v.defaults()
    cfg["s2.speed_min_mps"], cfg["s2.speed_max_mps"] = 5.0, 1.0
    r = v.validate(cfg)
    check(not r.ok and any("C002" in e for e in r.errors), "速度の下限>上限を弾く")

    cfg = v.defaults()
    cfg["safety.temp_resume_c"], cfg["safety.temp_shutdown_c"] = 55.0, 50.0
    check(not v.validate(cfg).ok, "再開温度>停止温度を弾く")

    cfg = v.defaults()
    cfg["schedule.start_min"], cfg["schedule.end_min"] = 1140, 300
    check(not v.validate(cfg).ok, "監視開始>終了を弾く")

    # これが最も事故りやすい制約
    cfg = v.defaults()
    cfg["config.probation_s"] = 120
    cfg["heartbeat.interval_s"] = 300
    cfg["config.required_heartbeats"] = 3
    r = v.validate(cfg)
    check(not r.ok and any("C006" in e for e in r.errors),
          "試用期間がハートビート周期に対して短すぎる設定を弾く(必ずロールバックする罠)")

    # 強制クランプ: 検証を通ってもここで丸める
    cfg = v.defaults()
    r = v.validate_and_clamp(cfg)
    r.config["safety.temp_shutdown_c"] = 200.0          # 検証後に改竄されたと仮定
    clamped, ch = v.apply_hard_clamps(r.config)
    check(clamped["safety.temp_shutdown_c"] == 60.0,
          "安全停止温度は範囲外なら強制的に丸める(無効化させない)")

    cfg = v.defaults(); del cfg["safety.temp_shutdown_c"]
    clamped, ch = v.apply_hard_clamps(cfg)
    check(clamped["safety.temp_shutdown_c"] == 55.0,
          "安全パラメータが欠落していたら既定値を強制的に入れる")

    cfg = v.defaults(); cfg["upload.thumbnail_long_edge"] = 1920
    r = v.validate(cfg)
    check(r.ok and any("W001" in w for w in r.warnings),
          "通信量が上限を超えそうな設定は警告する(拒否はしない)")

    cfg = v.defaults(); cfg["capture.preview_long_edge"] = 640
    r = v.validate(cfg)
    check(r.ok and any("W002" in w for w in r.warnings),
          "プレビュー解像度が低い設定は警告する")

    cfg = v.defaults(); cfg["test_mode.max_duration_s"] = 3600
    r = v.validate(cfg)
    check(r.ok and any("W003" in w for w in r.warnings),
          "試験モードが長時間の設定は警告する")

    cfg = v.defaults()
    r = v.validate_and_clamp(cfg)
    r.config["test_mode.max_duration_s"] = 999999      # 無限に走らせようとした場合
    clamped, _ = v.apply_hard_clamps(r.config)
    check(clamped["test_mode.max_duration_s"] == 7200,
          "試験モードの継続時間は強制的に上限で丸める(走りっぱなしを防ぐ)")

    a = v.defaults(); b = dict(a)
    check(ConfigValidator.etag(a) == ConfigValidator.etag(b), "同一内容なら etag が一致する")
    b["s1.fg_threshold"] = 26
    check(ConfigValidator.etag(a) != ConfigValidator.etag(b), "内容が変われば etag も変わる")


# ==================================================================== ロールバック

def new_mgr(tmp, clock, apply_fn=None):
    return ConfigManager(os.path.join(tmp, "cfg.json"), apply_fn=apply_fn, clock=clock)


def test_rollback():
    print("\n[2] ロールバック状態機械")
    v = ConfigValidator()
    tmp = tempfile.mkdtemp()
    clk = Clock()

    # --- 正常系: 試用 -> 確定
    m = new_mgr(tmp, clk)
    base = m.config["s1.fg_threshold"]
    good = dict(m.config); good["s1.fg_threshold"] = base + 5
    et = ConfigValidator.etag(good)
    r = m.on_config_received(good, et)
    check(r["result"] == "TRIAL_STARTED" and m.st.state == TRIAL, "正しい設定で試用が始まる")
    check(m.config["s1.fg_threshold"] == base + 5, "試用中は新しい設定が有効になっている")
    for _ in range(3):
        m.on_heartbeat_success()
    check(m.st.state == STABLE, "必要回数のハートビート成功で確定する")
    check(m.st.lkg_etag == et, "確定時に LKG が更新される")

    # --- 不正な設定は試用に入らない
    bad = dict(m.config); bad["capture.interval_ms"] = -1
    r = m.on_config_received(bad, "badetag01")
    check(r["result"] == "INVALID" and m.st.state == STABLE, "不正な設定は拒否し試用に入らない")
    check(m.config["capture.interval_ms"] > 0, "拒否時は現在の設定が保たれる")

    # --- 通信不能になる設定: 期限切れで巻き戻る
    shutil.rmtree(tmp); tmp = tempfile.mkdtemp(); clk = Clock()
    m = new_mgr(tmp, clk)
    stable_etag = m.st.active_etag
    killer = dict(m.config); killer["s1.fg_threshold"] = 200
    ket = ConfigValidator.etag(killer)
    m.on_config_received(killer, ket)
    check(m.st.state == TRIAL, "試用開始")
    clk.advance(m.config["config.probation_s"] + 1)   # サーバに繋がらないまま時間経過
    m.tick()
    check(m.st.state == ROLLED_BACK, "期限内に確認が取れず巻き戻る")
    check(m.st.active_etag == stable_etag, "直前の正常な設定に戻っている")
    check(ket in m.st.rejected_etags, "壊れた設定の etag を記憶している")

    # --- 同じ壊れた設定が再送されても受け付けない
    r = m.on_config_received(killer, ket)
    check(r["result"] == "REJECTED_BEFORE", "再送された同一設定を拒否する(無限ループ防止)")

    # --- 巻き戻し報告が届いたら STABLE へ
    m.on_report_delivered()
    check(m.st.state == STABLE, "巻き戻しの報告後に STABLE へ戻る")

    # --- 再起動を跨いでも期限は生き残る
    shutil.rmtree(tmp); tmp = tempfile.mkdtemp(); clk = Clock()
    m = new_mgr(tmp, clk)
    stable_etag = m.st.active_etag
    c2 = dict(m.config); c2["s2.min_track_frames"] = 8
    m.on_config_received(c2, ConfigValidator.etag(c2))
    clk.advance(m.config["config.probation_s"] + 1)
    m2 = new_mgr(tmp, clk)                     # プロセス再起動を模す
    check(m2.st.state == TRIAL, "再起動後も試用状態が復元される")
    m2.on_boot()
    check(m2.st.state == ROLLED_BACK and m2.st.active_etag == stable_etag,
          "期限を過ぎた状態で起動したら巻き戻る")

    # --- クラッシュループ
    shutil.rmtree(tmp); tmp = tempfile.mkdtemp(); clk = Clock()
    m = new_mgr(tmp, clk)
    stable_etag = m.st.active_etag
    c3 = dict(m.config); c3["record.max_duration_s"] = 90
    m.on_config_received(c3, ConfigValidator.etag(c3))
    limit = m.config["config.max_boots_in_trial"]
    for i in range(limit + 1):
        m = new_mgr(tmp, clk)
        m.on_boot()
    check(m.st.state == ROLLED_BACK, f"試用中の再起動が {limit} 回を超えたら巻き戻る")
    check(m.st.active_etag == stable_etag, "クラッシュループでも正しい設定に戻る")

    # --- 適用そのものが失敗する設定
    shutil.rmtree(tmp); tmp = tempfile.mkdtemp(); clk = Clock()
    boom = {"on": False}

    def apply_fn(cfg):
        if boom["on"] and cfg["s3.threshold"] > 0.9:
            raise RuntimeError("パイプラインの初期化に失敗")

    m = new_mgr(tmp, clk, apply_fn)
    stable_etag = m.st.active_etag
    boom["on"] = True
    c4 = dict(m.config); c4["s3.threshold"] = 0.95
    r = m.on_config_received(c4, ConfigValidator.etag(c4))
    check(r["result"] == "APPLY_FAILED", "適用時に例外が出たら失敗として扱う")
    check(m.st.state == ROLLED_BACK and m.st.active_etag == stable_etag,
          "適用失敗は即座に巻き戻る(期限を待たない)")

    # --- パイプライン停止の検出
    shutil.rmtree(tmp); tmp = tempfile.mkdtemp(); clk = Clock()
    m = new_mgr(tmp, clk)
    stable_etag = m.st.active_etag
    c5 = dict(m.config); c5["capture.interval_ms"] = 500
    m.on_config_received(c5, ConfigValidator.etag(c5))
    check(m.on_pipeline_stall() is True, "試用中のパイプライン停止を巻き戻し契機とみなす")
    check(m.st.active_etag == stable_etag, "停止検出で正しい設定に戻る")
    m.on_report_delivered()
    check(m.on_pipeline_stall() is False,
          "STABLE 時の停止は設定の問題ではないので巻き戻さない")

    # --- 試用中は新しい設定を受け付けない
    shutil.rmtree(tmp); tmp = tempfile.mkdtemp(); clk = Clock()
    m = new_mgr(tmp, clk)
    first = dict(m.config); first["s1.fg_threshold"] = 30
    m.on_config_received(first, ConfigValidator.etag(first))
    second = dict(m.config); second["s1.fg_threshold"] = 35
    r = m.on_config_received(second, ConfigValidator.etag(second))
    check(r["result"] == "BUSY", "試用中は次の設定を受け付けない(試用の入れ子を防ぐ)")

    # --- LKG が試用中の設定で上書きされない
    lkg_before = m.st.lkg_etag
    m.on_heartbeat_success()      # まだ確定していない
    check(m.st.lkg_etag == lkg_before, "確定前は LKG が更新されない")

    # --- 同一 etag は無変更
    m2 = new_mgr(tmp, clk)
    r = m2.on_config_received(m2.config, m2.st.active_etag)
    check(r["result"] == "NO_CHANGE", "同じ etag なら何もしない")

    shutil.rmtree(tmp)


if __name__ == "__main__":
    test_validator()
    test_rollback()
    print(f"\n{'='*50}\n  PASS {OK} / FAIL {NG}\n{'='*50}")
    raise SystemExit(1 if NG else 0)
