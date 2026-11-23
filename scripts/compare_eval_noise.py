"""同配置重复轮的噪声底对比：python3 scripts/compare_eval_noise.py a.json b.json [c.json]

读 ``evaluate.py --json-output`` 的产物，输出三件事：指标是否轮轮一致、两两配对的逐条行为差异、
耗时差。耗时单独列，因为第一轮慢是冷启动（要加载生成与精排模型），跟行为波动不是一回事。

判定口径见 docs/ROADMAP.md 的 v8.3：指标变化 ≥ 1 条样本才当真；耗时作废第一轮；措辞变化不算证据。
"""

from __future__ import annotations

import itertools
import json
import sys

# 会影响结论的逐条字段：这些变了才是行为变了
BEHAVIOR = [
    ("拒答", lambda s: s["refusal"]),
    ("意图", lambda s: s["intent"]),
    ("停止原因", lambda s: s["stop_reason"]),
    ("引用命中", lambda s: s["metrics"].get("source_hit")),
    ("取证命中", lambda s: s["metrics"].get("evidence_hit@3")),
    ("工具调用数", lambda s: s["tool_calls"]),
    ("证据字数", lambda s: s["context_chars"]),
    ("答案文本", lambda s: s.get("answer") or ""),
]
METRICS = [
    "answer_rate",
    "source_hit",
    "refusal_accuracy",
    "both_sides_hit",
    "citation_rate",
    "citation_validity",
    "evidence_hit@3",
    "mrr@3",
    "context_precision@3",
]


def load(path: str):
    with open(path) as fh:
        data = json.load(fh)
    config = data["configs"].get("agent") or next(iter(data["configs"].values()))
    return config["metrics"], {s["id"]: s for s in data["samples"]}


def main(paths: list[str]) -> int:
    rounds = [load(p) for p in paths]
    names = [p.split("/")[-1].replace(".json", "") for p in paths]

    print("=== 指标是否轮轮一致 ===")
    unstable = []
    for key in METRICS:
        values = [metrics.get(key) for metrics, _ in rounds]
        if len(set(values)) == 1:
            print(f"{key}: 一致 {values[0]}")
        else:
            unstable.append(key)
            print(f"{key}: 不一致 {' / '.join(f'{v:.4f}' for v in values)}")

    print("\n=== 两两配对 ===")
    for (i, (ma, sa)), (j, (mb, sb)) in itertools.combinations(list(enumerate(rounds)), 2):
        hits: list[str] = []
        slow: list[str] = []
        for sid in sa:
            for label, getter in BEHAVIOR:
                if str(getter(sa[sid])) != str(getter(sb[sid])):
                    hits.append(f"{sid}:{label}")
            if abs((sa[sid]["retrieval_ms"] + sa[sid]["generation_ms"]) -
                   (sb[sid]["retrieval_ms"] + sb[sid]["generation_ms"])) > 5000:
                slow.append(sid)
        la, lb = ma["avg_latency_ms"] / 1000, mb["avg_latency_ms"] / 1000
        spread = abs(la - lb)
        changed = sorted({name.split(":")[0] for name in hits})
        print(f"{names[i]} vs {names[j]}: 行为差异 {len(changed)} 条"
              f"（{'、'.join(hits) if hits else '无'}）；耗时差 {spread:.1f}s"
              f"（{spread / min(la, lb) * 100:.1f}%）、耗时差超过 5 秒的样本 {len(slow)} 条")
    return 0 if not unstable else 1


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法：python3 scripts/compare_eval_noise.py a.json b.json [c.json]")
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1:]))
