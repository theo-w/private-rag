"""量「同一输入重复调用会不会给出同一份输出」——评估读数能不能复现，先看这个。

同一条证据文本连续生成 N 次，对输出取 SHA256 指纹并数唯一个数。默认走评估环境
（``configure_eval_env()`` 会把最终作答温度钉成 0），想看改动前的行为加 ``--unpinned``。

用法：
    python3 scripts/check_generation_determinism.py --repeat 4
    python3 scripts/check_generation_determinism.py --repeat 4 --thinking off
    python3 scripts/check_generation_determinism.py --repeat 4 --unpinned

要打到本机 Ollama，不打网络。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from private_rag.eval_env import configure_eval_env  # noqa: E402  必须在导入 config 之前调用

configure_eval_env()

import private_rag.agent.prompts as prompts  # noqa: E402
from private_rag.agent.prompts import ask_json, build_step_prompt, generate_final_answer  # noqa: E402

QUESTION = "这款咖啡豆的保存方式是什么？开封后能放多久？"
EVIDENCE = """[1] 《咖啡储存手册》第三章：烘焙后的咖啡豆在常温密封罐里可以保存 4 周；开封后建议在 7 天内用完。
[2] 《咖啡储存手册》第三章：不要放冰箱，冷藏会让豆子吸味并产生冷凝水，研磨前才需要冷藏的场景是大批量囤货。
[3] 客服记录 2024-05-11：客户询问单包 250g 咖啡豆开封后的最佳饮用期，回复为 7 天内用完，避免受潮。"""
STEP_PROMPT = build_step_prompt(
    QUESTION, "kb_search / sql_query", "已有证据摘要", ["第 1 步：kb_search 返回 3 条"]
)


def fingerprint(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def repeat(label: str, calls: int, fn) -> int:
    fingerprints, elapsed = [], []
    for _ in range(calls):
        started = time.time()
        fingerprints.append(fingerprint(fn()))
        elapsed.append(round(time.time() - started, 2))
    unique = len(set(fingerprints))
    print(f"{label}: 重复 {calls} 次 → 唯一输出 {unique} 个 指纹={fingerprints} 耗时={elapsed}")
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description="检查生成与决策调用的可复现性")
    parser.add_argument("--repeat", type=int, default=3, help="每种参数组合重复几次（默认 3）")
    parser.add_argument(
        "--thinking",
        choices=["keep", "on", "off"],
        default="keep",
        help="最终作答的思考取值（默认 keep，即运行时默认档）",
    )
    parser.add_argument(
        "--unpinned",
        action="store_true",
        help="不传温度与 seed，复现改动前的行为（那时每次调用都可能不同）",
    )
    args = parser.parse_args()

    if args.unpinned:
        prompts.AGENT_ANSWER_TEMPERATURE = None
        prompts.AGENT_LLM_SEED = None
    prompts.AGENT_ANSWER_THINKING = args.thinking

    print(f"参数：temperature={prompts.AGENT_ANSWER_TEMPERATURE} seed={prompts.AGENT_LLM_SEED} "
          f"thinking={prompts.AGENT_ANSWER_THINKING} repeat={args.repeat}")
    answer_unique = repeat("最终作答", args.repeat, lambda: generate_final_answer(QUESTION, EVIDENCE))
    step_unique = repeat("步进决策", args.repeat, lambda: ask_json(STEP_PROMPT, kind="step"))
    print("\n结论：唯一输出 1 个才算复现。这台 MLX 后端不接受 seed，复现只能靠 temperature=0；"
          "带思考时即使温度为 0 也会抖（见 docs/ROADMAP.md 的 v8.3）。")
    return 0 if answer_unique == 1 and step_unique == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
