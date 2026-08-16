"""端末側の設定適用とロールバックの状態機械（参照実装）。

Kotlin での実装はこのロジックをそのまま移植する。ここでは再起動・通信断・
クラッシュループを机上で再現して検証できるよう Python で書いてある。

--------------------------------------------------------------------------
状態
--------------------------------------------------------------------------
  STABLE       現在の設定は確認済み。新しい設定を受け付けられる
  TRIAL        新しい設定を試用中。期限内に確認が取れなければ戻す
  ROLLED_BACK  戻した直後。サーバへ報告できたら STABLE に戻る

--------------------------------------------------------------------------
設計上の要点
--------------------------------------------------------------------------
1. ロールバックの判定は完全にローカルで完結する。
   サーバに繋がらなくなる設定を掴んだ場合こそロールバックが必要なので、
   判定にサーバを必要としてはならない。

2. 期限は壁時計で保存する。
   Android の elapsedRealtime は再起動でゼロに戻るため、試用中に再起動すると
   期限が失われる。壁時計を保存し、起動時に「もう過ぎているか」を見る。

3. LKG(最後に成功した設定)は STABLE のときしか更新しない。
   試用中の設定で LKG を上書きすると、戻る先が壊れた設定になる。

4. 一度戻した設定の etag は記憶し、再送されても受け付けない。
   これがないと「配る → 死ぬ → 戻る → また配られる」の無限ループになる。

5. 試用中は新しい設定を受け付けない。
   試用を入れ子にすると、どこへ戻ればよいか分からなくなる。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Callable

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.config.validator import ConfigValidator

STABLE = "STABLE"
TRIAL = "TRIAL"
ROLLED_BACK = "ROLLED_BACK"

MAX_REJECTED = 20


@dataclass
class ConfigState:
    active: dict = field(default_factory=dict)
    active_etag: str = ""
    lkg: dict = field(default_factory=dict)
    lkg_etag: str = ""
    state: str = STABLE
    trial_deadline_ms: int = 0
    trial_ok_heartbeats: int = 0
    boots_in_trial: int = 0
    rejected_etags: list[str] = field(default_factory=list)
    pending_report: dict | None = None


class ConfigManager:
    """設定の受け入れ・試用・確定・巻き戻しを司る。

    apply_fn には、設定を実際にパイプラインへ反映する関数を渡す。
    例外を投げた場合は「適用失敗」として即座に巻き戻す。
    """

    def __init__(self, path: str, apply_fn: Callable[[dict], None] | None = None,
                 validator: ConfigValidator | None = None,
                 clock: Callable[[], int] | None = None):
        self.path = path
        self.apply_fn = apply_fn or (lambda cfg: None)
        self.v = validator or ConfigValidator()
        self.clock = clock or (lambda: 0)
        self.st = self._load()

    # ------------------------------------------------------------ 永続化

    def _load(self) -> ConfigState:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fp:
                return ConfigState(**json.load(fp))
        cfg, _ = self.v.apply_hard_clamps(self.v.defaults())
        et = ConfigValidator.etag(cfg)
        return ConfigState(active=cfg, active_etag=et, lkg=dict(cfg), lkg_etag=et)

    def _save(self):
        """一時ファイル経由で置換する。書き込み中の電源断で壊れないようにするため。"""
        d = os.path.dirname(os.path.abspath(self.path)) or "."
        fd, tmp = tempfile.mkstemp(dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(asdict(self.st), fp, ensure_ascii=False)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ------------------------------------------------------------ 設定の受け入れ

    def on_config_received(self, cfg: dict, etag: str) -> dict:
        """サーバから受け取った設定を検証し、問題なければ試用を開始する。"""
        if etag in self.st.rejected_etags:
            return self._reject(etag, "REJECTED_BEFORE",
                                "以前ロールバックした設定と同一のため受け付けません")

        if etag == self.st.active_etag:
            return {"result": "NO_CHANGE", "etag": etag}

        if self.st.state == TRIAL:
            return {"result": "BUSY",
                    "detail": "別の設定を試用中です。確定または巻き戻しまで待ってください",
                    "etag": etag}

        r = self.v.validate_and_clamp(cfg)
        if not r.ok:
            return self._reject(etag, "INVALID", "; ".join(r.errors))

        # ここが要点: LKG を更新できるのは STABLE のときだけ
        self.st.lkg = dict(self.st.active)
        self.st.lkg_etag = self.st.active_etag

        self.st.active = r.config
        self.st.active_etag = etag
        self.st.state = TRIAL
        self.st.trial_ok_heartbeats = 0
        self.st.boots_in_trial = 0
        self.st.trial_deadline_ms = self.clock() + r.config["config.probation_s"] * 1000
        self._save()

        try:
            self.apply_fn(self.st.active)
        except Exception as e:
            self._rollback(f"設定の適用に失敗しました: {e}")
            return {"result": "APPLY_FAILED", "etag": etag, "detail": str(e)}

        return {"result": "TRIAL_STARTED", "etag": etag,
                "probation_s": r.config["config.probation_s"],
                "warnings": r.warnings, "clamped": r.clamped}

    def _reject(self, etag: str, code: str, detail: str) -> dict:
        self.st.pending_report = {"type": "config_rejected", "etag": etag,
                                  "code": code, "detail": detail}
        self._save()
        return {"result": code, "etag": etag, "detail": detail}

    # ------------------------------------------------------------ 試用の確定

    def on_heartbeat_success(self):
        """サーバとの往復が成功したときに呼ぶ。試用中ならこれが確定の根拠になる。"""
        if self.st.state != TRIAL:
            if self.st.state == ROLLED_BACK and self.st.pending_report is None:
                self.st.state = STABLE
                self._save()
            return
        self.st.trial_ok_heartbeats += 1
        need = self.st.active["config.required_heartbeats"]
        if self.st.trial_ok_heartbeats >= need:
            self.st.state = STABLE
            self.st.lkg = dict(self.st.active)
            self.st.lkg_etag = self.st.active_etag
            self.st.pending_report = {"type": "config_confirmed",
                                      "etag": self.st.active_etag}
        self._save()

    def on_report_delivered(self):
        self.st.pending_report = None
        if self.st.state == ROLLED_BACK:
            self.st.state = STABLE
        self._save()

    # ------------------------------------------------------------ 巻き戻しの契機

    def tick(self):
        """定期的に呼ぶ。試用期限の超過を検出する。"""
        if self.st.state == TRIAL and self.clock() > self.st.trial_deadline_ms:
            self._rollback("試用期限内にサーバとの通信を確認できませんでした")

    def on_boot(self):
        """起動時に必ず呼ぶ。再起動を跨いだ期限とクラッシュループを見る。"""
        if self.st.state != TRIAL:
            self.apply_fn(self.st.active)
            return
        self.st.boots_in_trial += 1
        limit = self.st.active["config.max_boots_in_trial"]
        if self.st.boots_in_trial > limit:
            self._rollback(f"試用中の再起動が {self.st.boots_in_trial} 回に達しました")
            return
        self._save()
        if self.clock() > self.st.trial_deadline_ms:
            self._rollback("試用期限を超過した状態で起動しました")
            return
        self.apply_fn(self.st.active)

    def on_pipeline_stall(self):
        """ウォッチドッグが検知パイプラインの停止を検出したときに呼ぶ。"""
        if self.st.state == TRIAL:
            self._rollback("試用中に検知パイプラインが停止しました")
            return True
        return False

    def _rollback(self, reason: str):
        bad = self.st.active_etag
        self.st.rejected_etags.append(bad)
        self.st.rejected_etags = self.st.rejected_etags[-MAX_REJECTED:]
        self.st.active = dict(self.st.lkg)
        self.st.active_etag = self.st.lkg_etag
        self.st.state = ROLLED_BACK
        self.st.trial_ok_heartbeats = 0
        self.st.boots_in_trial = 0
        self.st.trial_deadline_ms = 0
        self.st.pending_report = {"type": "config_rolled_back",
                                  "etag": bad, "restored": self.st.lkg_etag,
                                  "reason": reason}
        self._save()
        try:
            self.apply_fn(self.st.active)
        except Exception:
            # LKG の適用にすら失敗する状況では、組み込みの既定値まで戻す
            cfg, _ = self.v.apply_hard_clamps(self.v.defaults())
            self.st.active = cfg
            self.st.active_etag = ConfigValidator.etag(cfg)
            self.st.lkg = dict(cfg)
            self.st.lkg_etag = self.st.active_etag
            self._save()
            self.apply_fn(self.st.active)

    # ------------------------------------------------------------ 参照

    @property
    def config(self) -> dict:
        return self.st.active

    def status(self) -> dict:
        return {"state": self.st.state, "active_etag": self.st.active_etag,
                "lkg_etag": self.st.lkg_etag,
                "trial_ok": self.st.trial_ok_heartbeats,
                "boots_in_trial": self.st.boots_in_trial,
                "rejected": list(self.st.rejected_etags),
                "pending_report": self.st.pending_report}
