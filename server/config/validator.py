"""設定の値域チェック。

サーバ側は「配る前に弾く」ために、端末側は「適用する前に弾く」ために、
どちらも同じスキーマとロジックを使う。

--------------------------------------------------------------------------
なぜ端末側でも検証するのか
--------------------------------------------------------------------------
サーバが必ず正しい設定を配る保証はない。スキーマの版ずれ、手動でのDB書き換え、
デプロイ事故。端末が壊れると現地に行くしかないので、端末は「送られてきたものを
信用しない」前提で作る。

さらに critical パラメータには hard_clamps があり、検証を通ったあとでも
物理的に安全な範囲へ強制的に丸める。安全停止温度をサーバから無効化できては
ならないため。
"""

from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass, field

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "..", "shared", "config_schema.json")


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    clamped: dict[str, tuple] = field(default_factory=dict)
    config: dict = field(default_factory=dict)

    def report(self) -> str:
        L = ["OK" if self.ok else "NG"]
        for e in self.errors:
            L.append(f"  [error] {e}")
        for w in self.warnings:
            L.append(f"  [warn ] {w}")
        for k, (before, after) in self.clamped.items():
            L.append(f"  [clamp] {k}: {before} -> {after}")
        return "\n".join(L)


class ConfigValidator:
    def __init__(self, schema_path: str = SCHEMA_PATH):
        with open(schema_path, encoding="utf-8") as fp:
            self.schema = json.load(fp)
        self.params = self.schema["params"]

    # ------------------------------------------------------------ 既定値

    def defaults(self) -> dict:
        return {k: v["default"] for k, v in self.params.items()}

    def fill_defaults(self, cfg: dict) -> dict:
        out = self.defaults()
        out.update({k: v for k, v in cfg.items() if k in self.params})
        return out

    # ------------------------------------------------------------ 検証

    def validate(self, cfg: dict, fill_missing: bool = True) -> ValidationResult:
        errors, warnings, clamped = [], [], {}

        unknown = [k for k in cfg if k not in self.params]
        for k in unknown:
            warnings.append(f"未知のパラメータ `{k}` は無視されます")

        work = self.fill_defaults(cfg) if fill_missing else dict(cfg)

        # 必須(critical)の欠落は埋めずにエラーとする
        for k, spec in self.params.items():
            if spec.get("critical") and k not in cfg and not fill_missing:
                errors.append(f"`{k}` は必須です(安全に関わるため既定値で補完しません)")

        # 型と値域
        for k, spec in self.params.items():
            if k not in work:
                errors.append(f"`{k}` がありません")
                continue
            v = work[k]
            t = spec["type"]
            if t == "bool":
                if not isinstance(v, bool):
                    errors.append(f"`{k}` は真偽値である必要があります (受信値: {v!r})")
                continue
            if t == "int":
                if isinstance(v, bool) or not isinstance(v, int):
                    errors.append(f"`{k}` は整数である必要があります (受信値: {v!r})")
                    continue
            elif t == "float":
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    errors.append(f"`{k}` は数値である必要があります (受信値: {v!r})")
                    continue
                work[k] = float(v)
                v = work[k]
            lo, hi = spec.get("min"), spec.get("max")
            if lo is not None and v < lo:
                errors.append(f"`{k}` = {v} が下限 {lo} を下回っています")
            if hi is not None and v > hi:
                errors.append(f"`{k}` = {v} が上限 {hi} を超えています")

        if errors:
            return ValidationResult(False, errors, warnings, clamped, work)

        # パラメータ間の制約
        for c in self.schema["constraints"]:
            kind = c["kind"]
            if kind in ("lt", "le"):
                a, b = work[c["a"]], work[c["b"]]
                bad = (a >= b) if kind == "lt" else (a > b)
                if bad:
                    errors.append(f"[{c['id']}] {c['msg']} ({c['a']}={a}, {c['b']}={b})")
            elif kind == "ge_product":
                need = c["multiplier"]
                for f in c["factors"]:
                    need *= work[f]
                if work[c["a"]] < need:
                    errors.append(
                        f"[{c['id']}] {c['msg']} (必要 {need:.0f} 以上, 実際 {work[c['a']]})")

        # 警告(エラーにはしない)
        for w in self.schema["warnings"]:
            if w["kind"] == "estimated_daily_mb":
                est = self._estimated_daily_mb(work)
                if est > work["upload.daily_limit_mb"]:
                    warnings.append(
                        f"[{w['id']}] {w['msg']} (推定 {est:.1f} MB/日 > 上限 "
                        f"{work['upload.daily_limit_mb']} MB)")
            elif w["kind"] == "test_mode_long":
                if work["test_mode.max_duration_s"] > 1800:
                    est = (work["test_mode.max_duration_s"] * 1000
                           / max(work["capture.interval_ms"], 1)
                           / work["test_mode.frame_stride"] * 0.25)
                    warnings.append(
                        f"[{w['id']}] {w['msg']} (推定 {est:.0f} MB)")
            elif w["kind"] == "preview_range":
                if work["capture.preview_long_edge"] < 1280:
                    warnings.append(
                        f"[{w['id']}] {w['msg']} "
                        f"(現在 {work['capture.preview_long_edge']} px)")

        return ValidationResult(not errors, errors, warnings, clamped, work)

    # ------------------------------------------------------------ 強制クランプ

    def apply_hard_clamps(self, cfg: dict) -> tuple[dict, dict]:
        """検証を通ったあとでも、安全に関わる値は物理的な範囲へ丸める。

        サーバから安全停止温度を無効化できてはならない、という考え方。
        """
        out = dict(cfg)
        clamped = {}
        for k, (lo, hi) in self.schema["hard_clamps"].items():
            if k not in out:
                out[k] = self.params[k]["default"]
                clamped[k] = ("(欠落)", out[k])
                continue
            v = out[k]
            nv = min(max(v, lo), hi)
            if nv != v:
                out[k] = nv
                clamped[k] = (v, nv)
        return out, clamped

    def validate_and_clamp(self, cfg: dict) -> ValidationResult:
        r = self.validate(cfg)
        if not r.ok:
            return r
        cfg2, clamped = self.apply_hard_clamps(r.config)
        r.config = cfg2
        r.clamped = clamped
        for k, (b, a) in clamped.items():
            r.warnings.append(f"`{k}` を安全範囲に丸めました: {b} -> {a}")
        return r

    # ------------------------------------------------------------ 補助

    @staticmethod
    def _estimated_daily_mb(cfg: dict, events_per_day: int = 50) -> float:
        """第一報の縮小画像だけで見積もる粗い推定。"""
        edge = cfg["upload.thumbnail_long_edge"]
        kb = (edge / 640.0) ** 2 * 80.0          # 640px で約80KB を基準に面積比
        return events_per_day * kb / 1024.0

    @staticmethod
    def etag(cfg: dict) -> str:
        """設定内容から決まる版番号。同じ内容なら同じ値になる。"""
        s = json.dumps(cfg, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(s.encode()).hexdigest()[:16]


if __name__ == "__main__":
    import sys
    v = ConfigValidator()
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as fp:
            cfg = json.load(fp)
    else:
        cfg = v.defaults()
    r = v.validate_and_clamp(cfg)
    print(r.report())
    if r.ok:
        print(f"\netag = {ConfigValidator.etag(r.config)}")
