"""
検知システムの評価指標を算出する。

このシステムは S3(端末) -> S4(サーバ即時) -> S5(サーバ事後) のカスケードなので、
単純に「モデルの精度」を出すだけでは運用判断に使えない。本モジュールは3つの層で測る。

  1. 段階別        各段が何を通し何を落としたか
  2. 二値          クマ / クマ以外。運用の合否はここで決まる
  3. 多クラス      種別。誤りの中身(イノシシと取り違えているのか等)を見る

--------------------------------------------------------------------------
再現率(Recall)についての重要な注意
--------------------------------------------------------------------------
サーバに届いたデータだけで計算した再現率は「条件付き再現率」であり、真の再現率
ではない。S1/S2 が落としたものはラベルが付かないため、分母に現れないからである。

真の再現率を出すには、独立した正解数が必要になる。
  - Phase 1 の距離別試験(既知の標的を何回通過させたか)
  - 生映像を人が全数確認して数えた実イベント数

本モジュールは known_positives を渡すと真の再現率を、渡さなければ条件付き再現率
を計算し、どちらであるかを必ず出力に明記する。

--------------------------------------------------------------------------
Accuracy についての注意
--------------------------------------------------------------------------
クマは全イベントの数%以下しかない。「すべてクマでない」と答えるだけで Accuracy は
97%を超える。したがって Accuracy 単独では合否判断に使えない。
本モジュールは Accuracy を出力するが、必ず Balanced Accuracy を併記し、
陽性率が極端な場合は警告を出す。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict, field
from typing import Sequence, Iterable

POSITIVE_LABEL = "ツキノワグマ"


# ---------------------------------------------------------------- 信頼区間

def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval。標本が少ないときに単純な正規近似より妥当。

    クマの実サンプルは初年度100件程度しか集まらない見込みなので、
    点推定だけを見て判断しないための装置。
    """
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


# ---------------------------------------------------------------- 二値評価

@dataclass
class BinaryResult:
    tp: int
    fp: int
    fn: int
    tn: int
    threshold: float | None = None

    recall_is_conditional: bool = True
    known_positives: int | None = None

    accuracy: float = 0.0
    balanced_accuracy: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    specificity: float = 0.0
    f1: float = 0.0
    precision_ci: tuple[float, float] = (0.0, 0.0)
    recall_ci: tuple[float, float] = (0.0, 0.0)
    positive_rate: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self):
        tp, fp, fn, tn = self.tp, self.fp, self.fn, self.tn
        n = tp + fp + fn + tn

        self.precision = tp / (tp + fp) if (tp + fp) else 0.0
        self.precision_ci = wilson_ci(tp, tp + fp)

        if self.known_positives is not None:
            # 独立に数えた正解数がある場合は真の再現率
            self.recall = tp / self.known_positives if self.known_positives else 0.0
            self.recall_ci = wilson_ci(tp, self.known_positives)
            self.recall_is_conditional = False
        else:
            self.recall = tp / (tp + fn) if (tp + fn) else 0.0
            self.recall_ci = wilson_ci(tp, tp + fn)
            self.recall_is_conditional = True

        self.specificity = tn / (tn + fp) if (tn + fp) else 0.0
        self.accuracy = (tp + tn) / n if n else 0.0
        self.balanced_accuracy = 0.5 * (self.recall + self.specificity)
        self.f1 = (2 * self.precision * self.recall / (self.precision + self.recall)
                   if (self.precision + self.recall) else 0.0)
        self.positive_rate = (tp + fn) / n if n else 0.0

        if n and self.positive_rate < 0.10:
            self.warnings.append(
                f"陽性率が {self.positive_rate*100:.1f}% と低いため Accuracy は無意味に高く出ます。"
                f"Balanced Accuracy ({self.balanced_accuracy*100:.1f}%) を見てください。")
        if tp + fp < 30:
            self.warnings.append(
                f"陽性予測が {tp+fp} 件しかなく、適合率の推定は不安定です "
                f"(95%信頼区間 {self.precision_ci[0]*100:.0f}〜{self.precision_ci[1]*100:.0f}%)。")
        if self.recall_is_conditional:
            self.warnings.append(
                "これは条件付き再現率です。S1/S2が落としたものは分母に入っていません。"
                "真の再現率には known_positives を指定してください。")


def evaluate_binary(y_true: Sequence[bool], y_score: Sequence[float],
                    threshold: float = 0.5,
                    known_positives: int | None = None) -> BinaryResult:
    """スコアと閾値から二値評価を行う。"""
    tp = fp = fn = tn = 0
    for t, s in zip(y_true, y_score):
        pred = s >= threshold
        if t and pred:
            tp += 1
        elif (not t) and pred:
            fp += 1
        elif t and (not pred):
            fn += 1
        else:
            tn += 1
    return BinaryResult(tp=tp, fp=fp, fn=fn, tn=tn, threshold=threshold,
                        known_positives=known_positives)


def evaluate_binary_pred(y_true: Sequence[bool],
                         y_pred: Sequence[bool],
                         known_positives: int | None = None) -> BinaryResult:
    """スコアがなく予測ラベルだけある場合(人手判定など)。"""
    return evaluate_binary(y_true, [1.0 if p else 0.0 for p in y_pred], 0.5,
                           known_positives)


# ---------------------------------------------------------------- 閾値探索

def pr_curve(y_true: Sequence[bool], y_score: Sequence[float],
             n_points: int = 101) -> list[dict]:
    """閾値を振って適合率・再現率の推移を返す。運用点の決定に使う。"""
    lo, hi = min(y_score, default=0.0), max(y_score, default=1.0)
    if hi <= lo:
        hi = lo + 1e-6
    out = []
    for i in range(n_points):
        th = lo + (hi - lo) * i / (n_points - 1)
        r = evaluate_binary(y_true, y_score, th)
        out.append(dict(threshold=th, precision=r.precision, recall=r.recall,
                        f1=r.f1, tp=r.tp, fp=r.fp, fn=r.fn))
    return out


def threshold_for_target_precision(y_true: Sequence[bool], y_score: Sequence[float],
                                   target_precision: float = 0.95,
                                   min_predictions: int = 20) -> dict | None:
    """目標適合率を満たす中で、最も再現率が高くなる閾値を返す。

    S5 の運用点はこれで決める。通報先が猟友会・警察である以上、
    適合率を先に固定し、その制約下で再現率を最大化するのが正しい順序。
    """
    best = None
    for p in pr_curve(y_true, y_score, 201):
        if p["tp"] + p["fp"] < min_predictions:
            continue
        if p["precision"] >= target_precision:
            if best is None or p["recall"] > best["recall"]:
                best = p
    return best


# ---------------------------------------------------------------- 多クラス評価

@dataclass
class MulticlassResult:
    labels: list[str]
    matrix: list[list[int]]
    per_class: dict[str, dict]
    accuracy: float
    macro_precision: float
    macro_recall: float
    macro_f1: float
    weighted_f1: float
    support: dict[str, int]


def evaluate_multiclass(y_true: Sequence[str], y_pred: Sequence[str],
                        labels: Sequence[str] | None = None) -> MulticlassResult:
    """種別判定の評価。誤りの中身(何と取り違えたか)を見るのが目的。"""
    if labels is None:
        labels = sorted(set(y_true) | set(y_pred))
    labels = list(labels)
    idx = {l: i for i, l in enumerate(labels)}
    k = len(labels)
    m = [[0] * k for _ in range(k)]
    for t, p in zip(y_true, y_pred):
        if t in idx and p in idx:
            m[idx[t]][idx[p]] += 1

    n = sum(sum(row) for row in m)
    correct = sum(m[i][i] for i in range(k))
    per_class, support = {}, {}
    ps, rs, fs, ws = [], [], [], []
    for i, l in enumerate(labels):
        tp = m[i][i]
        fp = sum(m[j][i] for j in range(k)) - tp
        fn = sum(m[i]) - tp
        sup = sum(m[i])
        pr = tp / (tp + fp) if (tp + fp) else 0.0
        rc = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * pr * rc / (pr + rc) if (pr + rc) else 0.0
        per_class[l] = dict(precision=pr, recall=rc, f1=f1, support=sup,
                            tp=tp, fp=fp, fn=fn,
                            precision_ci=wilson_ci(tp, tp + fp),
                            recall_ci=wilson_ci(tp, tp + fn))
        support[l] = sup
        ps.append(pr); rs.append(rc); fs.append(f1); ws.append(sup)

    tot = sum(ws) or 1
    return MulticlassResult(
        labels=labels, matrix=m, per_class=per_class,
        accuracy=correct / n if n else 0.0,
        macro_precision=sum(ps) / k if k else 0.0,
        macro_recall=sum(rs) / k if k else 0.0,
        macro_f1=sum(fs) / k if k else 0.0,
        weighted_f1=sum(f * w for f, w in zip(fs, ws)) / tot,
        support=support)


# ---------------------------------------------------------------- カスケード評価

def evaluate_cascade(events: Iterable[dict],
                     thresholds: dict[str, float],
                     known_positives: int | None = None) -> dict:
    """段階別の通過状況と、最終出力の二値評価をまとめて返す。

    events は §7.1 の Event を想定。review.label が付いているものだけを使う。
    """
    events = [e for e in events if (e.get("review") or {}).get("label")]
    total = len(events)
    stages = ["s3", "s4", "s5"]

    def is_bear(e):
        return e["review"]["label"] == POSITIVE_LABEL

    report = {"total_reviewed": total, "stages": {}}
    survivors = events
    for st in stages:
        th = thresholds.get(st)
        if th is None:
            continue
        scored = [e for e in survivors if (e.get("scores") or {}).get(st) is not None]
        passed = [e for e in scored if e["scores"][st] >= th]
        bears_in = sum(1 for e in scored if is_bear(e))
        bears_out = sum(1 for e in passed if is_bear(e))
        report["stages"][st] = dict(
            threshold=th,
            input=len(scored),
            passed=len(passed),
            pass_rate=len(passed) / len(scored) if scored else 0.0,
            bears_in=bears_in,
            bears_kept=bears_out,
            bear_retention=bears_out / bears_in if bears_in else 0.0,
            reduction_factor=len(scored) / len(passed) if passed else float("inf"),
        )
        survivors = passed

    y_true = [is_bear(e) for e in events]
    final_ids = {id(e) for e in survivors}
    y_pred = [id(e) in final_ids for e in events]
    report["end_to_end"] = asdict(
        evaluate_binary_pred(y_true, y_pred, known_positives))
    return report


# ---------------------------------------------------------------- 出力整形

def _pct(x):
    return "  n/a " if x != x else f"{x*100:6.2f}%"


def format_binary(r: BinaryResult, title: str = "二値評価 (クマ / クマ以外)") -> str:
    L = [f"=== {title} ===",
         f"  混同行列   TP {r.tp:6d}   FP {r.fp:6d}",
         f"             FN {r.fn:6d}   TN {r.tn:6d}",
         ""]
    if r.threshold is not None:
        L.append(f"  閾値            {r.threshold:.4f}")
    L += [f"  Accuracy        {_pct(r.accuracy)}   ← 陽性率が低いと無意味に高くなる",
          f"  BalancedAcc     {_pct(r.balanced_accuracy)}",
          f"  Precision       {_pct(r.precision)}   "
          f"[{r.precision_ci[0]*100:.1f} - {r.precision_ci[1]*100:.1f}%]",
          f"  Recall          {_pct(r.recall)}   "
          f"[{r.recall_ci[0]*100:.1f} - {r.recall_ci[1]*100:.1f}%]"
          + ("  (条件付き)" if r.recall_is_conditional else "  (真の再現率)"),
          f"  Specificity     {_pct(r.specificity)}",
          f"  F1              {_pct(r.f1)}",
          f"  陽性率          {_pct(r.positive_rate)}"]
    if r.warnings:
        L.append("")
        for w in r.warnings:
            L.append(f"  [注意] {w}")
    return "\n".join(L)


def format_multiclass(r: MulticlassResult) -> str:
    w = max(len(l) for l in r.labels) + 1
    L = ["=== 多クラス評価 (種別) ===", "",
         "  混同行列 (行=正解, 列=予測)", "",
         "  " + " " * w + "".join(f"{l[:6]:>7}" for l in r.labels)]
    for i, l in enumerate(r.labels):
        L.append("  " + f"{l:<{w}}" + "".join(f"{v:7d}" for v in r.matrix[i]))
    L += ["", f"  {'クラス':<{w}}{'適合率':>9}{'再現率':>9}{'F1':>9}{'件数':>7}"]
    for l in r.labels:
        c = r.per_class[l]
        L.append(f"  {l:<{w}}{c['precision']*100:8.1f}%{c['recall']*100:8.1f}%"
                 f"{c['f1']*100:8.1f}%{c['support']:7d}")
    L += ["", f"  Accuracy        {_pct(r.accuracy)}",
          f"  Macro Precision {_pct(r.macro_precision)}",
          f"  Macro Recall    {_pct(r.macro_recall)}",
          f"  Macro F1        {_pct(r.macro_f1)}",
          f"  Weighted F1     {_pct(r.weighted_f1)}"]
    return "\n".join(L)


def format_cascade(rep: dict) -> str:
    L = ["=== カスケード段階別 ===", "",
         f"  ラベル付きイベント総数: {rep['total_reviewed']}", "",
         f"  {'段':<5}{'閾値':>8}{'入力':>8}{'通過':>8}{'通過率':>9}"
         f"{'クマ保持':>10}{'削減倍率':>10}"]
    for st, s in rep["stages"].items():
        rf = "inf" if s["reduction_factor"] == float("inf") else f"{s['reduction_factor']:.1f}x"
        L.append(f"  {st.upper():<5}{s['threshold']:8.3f}{s['input']:8d}{s['passed']:8d}"
                 f"{s['pass_rate']*100:8.1f}%{s['bear_retention']*100:9.1f}%{rf:>10}")
    return "\n".join(L)


# ---------------------------------------------------------------- 入出力

def load_events(path: str) -> list[dict]:
    """JSONL (1行1イベント) を読む。"""
    out = []
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def full_report(events: list[dict], thresholds: dict[str, float],
                known_positives: int | None = None,
                target_precision: float = 0.95) -> str:
    """運用判断に必要な情報を一括で出す。"""
    labeled = [e for e in events if (e.get("review") or {}).get("label")]
    parts = []

    parts.append(format_cascade(evaluate_cascade(events, thresholds, known_positives)))
    parts.append("")

    # S5 スコアがあるものについて二値評価と運用点探索
    scored = [e for e in labeled if (e.get("scores") or {}).get("s5") is not None]
    if scored:
        y_true = [e["review"]["label"] == POSITIVE_LABEL for e in scored]
        y_score = [e["scores"]["s5"] for e in scored]
        th = thresholds.get("s5", 0.5)
        parts.append(format_binary(evaluate_binary(y_true, y_score, th, known_positives),
                                   f"S5 二値評価 (閾値 {th})"))
        parts.append("")
        best = threshold_for_target_precision(y_true, y_score, target_precision)
        parts.append(f"=== 運用点の探索 (目標適合率 {target_precision*100:.0f}%) ===")
        if best:
            parts.append(f"  推奨閾値 {best['threshold']:.4f} で "
                         f"適合率 {best['precision']*100:.1f}% / "
                         f"再現率 {best['recall']*100:.1f}% "
                         f"(TP {best['tp']} / FP {best['fp']} / FN {best['fn']})")
        else:
            parts.append(f"  目標適合率 {target_precision*100:.0f}% を満たす閾値が存在しません。")
            parts.append("  → 自動通報は行わず、人間確認キューを継続してください。")
        parts.append("")

    # 種別
    typed = [e for e in labeled if (e.get("scores") or {}).get("s5_label")]
    if typed:
        parts.append(format_multiclass(evaluate_multiclass(
            [e["review"]["label"] for e in typed],
            [e["scores"]["s5_label"] for e in typed])))
    return "\n".join(parts)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="検知システムの評価指標を算出する")
    ap.add_argument("events", help="イベントのJSONLファイル")
    ap.add_argument("--s3", type=float, default=0.05)
    ap.add_argument("--s4", type=float, default=0.30)
    ap.add_argument("--s5", type=float, default=0.50)
    ap.add_argument("--known-positives", type=int, default=None,
                    help="独立に数えた真のクマ出現回数。指定すると真の再現率を計算する")
    ap.add_argument("--target-precision", type=float, default=0.95)
    a = ap.parse_args()
    ev = load_events(a.events)
    print(full_report(ev, {"s3": a.s3, "s4": a.s4, "s5": a.s5},
                      a.known_positives, a.target_precision))
