"""評価モジュールの動作確認用に、実運用に近い偏りを持つ合成イベントを作る。

実データが貯まる前に metrics.py の挙動を確認し、閾値の効き方を体感するためのもの。
クマの出現率を3%に設定してあるので、Accuracy の罠がそのまま再現される。
"""
import json, random, argparse

LABELS = ["ツキノワグマ", "イノシシ", "ニホンジカ", "カモシカ", "犬", "人", "動物なし"]
WEIGHTS = [0.03, 0.10, 0.12, 0.03, 0.04, 0.08, 0.60]


def score(is_pos, mu_pos, mu_neg, sd=0.18, rng=random):
    return max(0.0, min(1.0, rng.gauss(mu_pos if is_pos else mu_neg, sd)))


def main(n=4000, seed=11, out="events.jsonl"):
    rng = random.Random(seed)
    with open(out, "w", encoding="utf-8") as f:
        for i in range(n):
            lab = rng.choices(LABELS, WEIGHTS)[0]
            bear = lab == "ツキノワグマ"
            animal = lab not in ("動物なし", "人")
            if bear:
                pred = "ツキノワグマ" if rng.random() < 0.80 else "イノシシ"
            elif lab == "イノシシ":
                pred = "イノシシ" if rng.random() < 0.70 else "ツキノワグマ"
            else:
                pred = lab if rng.random() < 0.85 else rng.choice(LABELS)
            f.write(json.dumps(dict(
                event_id=f"e{i}",
                scores=dict(s3=score(animal, .72, .30, rng=rng),
                            s4=score(animal, .78, .22, rng=rng),
                            s5=score(bear, .80, .20, rng=rng),
                            s5_label=pred),
                review=dict(label=lab)), ensure_ascii=False) + "\n")
    print(f"{n} 件を {out} に出力しました")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("-o", default="events.jsonl")
    a = ap.parse_args()
    main(a.n, a.seed, a.o)
