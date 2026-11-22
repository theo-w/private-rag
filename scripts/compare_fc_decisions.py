"""同一份决策提示词：JSON 模式与原生 function calling 的输出对比（需要本机 Ollama）。

要量的是「决策输出能不能稳定拿到」，不是「路由判得对不对」：
两种模式用完全相同的 ROUTER_PROMPT / STEP_PROMPT 与同一预算，只比较——
解析成功率、动作/工具合法性、两模式判定一致率、单条平均耗时。

用法：
    python3 scripts/init_demo_db.py
    python3 scripts/compare_fc_decisions.py [--per-category 3] [--control-model qwen2.5:7b-instruct]

输出：eval/reports/fc_comparison.json 与 eval/reports/fc_comparison_report.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from private_rag.agent.prompts import (
    ask_json,
    ask_tool_json,
    build_route_prompt,
    build_step_prompt,
    parse_decision,
    parse_route,
)
from private_rag.agent.runtime import build_agent_runtime
from private_rag.config import AGENT_LLM_MODEL, AGENT_PLANNER_TOKENS

ROOT = Path(__file__).resolve().parent.parent
EVAL_SET = ROOT / "eval" / "v7_agent_qa.jsonl"
AGENT_SOURCES = ROOT / "config" / "sources.eval.agent.yaml"
OUT_JSON = ROOT / "eval" / "reports" / "fc_comparison.json"
OUT_MD = ROOT / "eval" / "reports" / "fc_comparison_report.md"

# 只取这四类做路由对比：multi_step / coreference 需要对话历史或多步证据，不在本实验范围
CATEGORIES = ("knowledge", "database", "mixed", "refusal")

# 合成的步进场景：只看决策输出的形状是否拿得到，与评估正确性无关
STEP_SCENARIOS = [
    {
        "name": "文档证据已足够",
        "question": "评估链路如何避免和演示服务争抢 Qdrant 本地文件锁？",
        "evidence": "[1] (来源: eval/README.md)\n评估库使用独立集合 rag_eval_kb 与 storage/eval 路径，不与演示服务争抢 Qdrant 本地文件锁。",
        "observations": ["步骤 1 kb_search 命中 2 段，含评估库路径说明"],
    },
    {
        "name": "缺业务库侧证据",
        "question": "香蕉的单价是多少？它的说明里建议怎么保存？",
        "evidence": "[1] (来源: README.md)\n项目支持文件 / 网页 / SQL 三类数据源入库。",
        "observations": ["步骤 1 kb_search 命中 2 段，但没有香蕉的价格与保存说明"],
    },
    {
        "name": "第一步没有任何证据",
        "question": "本系统的告警规则由哪个模块负责？",
        "evidence": "",
        "observations": [],
    },
]


def load_questions(per_category: int) -> list[dict]:
    picked: list[dict] = []
    counts = {c: 0 for c in CATEGORIES}
    for line in EVAL_SET.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        sample = json.loads(line)
        category = str(sample.get("category") or "")
        if category in counts and counts[category] < per_category:
            counts[category] += 1
            picked.append({"id": sample["id"], "category": category, "question": sample["question"]})
    return picked


def run_route(question: str, tool_specs: str, mode: str, model: str) -> dict:
    prompt = build_route_prompt(question, tool_specs)
    started = time.perf_counter()
    try:
        if mode == "function":
            data = ask_tool_json(prompt, kind="route", model=model, num_predict=AGENT_PLANNER_TOKENS)
        else:
            data = ask_json(prompt, model=model, num_predict=AGENT_PLANNER_TOKENS)
        intent, action, _reason = parse_route(data)
        return {
            "ok": True,
            "intent": intent,
            "tool": str(action.get("tool") or ""),
            "error": "",
            "seconds": round(time.perf_counter() - started, 2),
        }
    except Exception as exc:
        return {
            "ok": False,
            "intent": "",
            "tool": "",
            "error": f"{type(exc).__name__}: {exc}"[:200],
            "seconds": round(time.perf_counter() - started, 2),
        }


def run_step(scenario: dict, tool_specs: str, mode: str, model: str, tool_names: list[str]) -> dict:
    prompt = build_step_prompt(
        scenario["question"],
        tool_specs,
        scenario["evidence"],
        scenario["observations"],
        None,
        steps_used=2,
        max_steps=4,
    )
    started = time.perf_counter()
    try:
        if mode == "function":
            data = ask_tool_json(prompt, kind="step", model=model, num_predict=AGENT_PLANNER_TOKENS)
        else:
            data = ask_json(prompt, model=model, num_predict=AGENT_PLANNER_TOKENS)
        action, payload, _reason, answer = parse_decision(data)
        tool = str(payload.get("tool") or "") if action == "tool" else ""
        return {
            "ok": True,
            "action": action,
            "tool": tool,
            "valid_tool": (action == "final") or (tool in tool_names),
            "has_answer": bool(answer.strip()) if action == "final" else True,
            "error": "",
            "seconds": round(time.perf_counter() - started, 2),
        }
    except Exception as exc:
        return {
            "ok": False,
            "action": "",
            "tool": "",
            "valid_tool": False,
            "has_answer": False,
            "error": f"{type(exc).__name__}: {exc}"[:200],
            "seconds": round(time.perf_counter() - started, 2),
        }


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    ok = [r for r in rows if r["ok"]]
    mean_seconds = round(sum(r["seconds"] for r in rows) / n, 2) if n else 0.0
    return {
        "n": n,
        "parse_ok": len(ok),
        "parse_ok_rate": round(len(ok) / n, 3) if n else 0.0,
        "valid_action_rate": (
            round(sum(1 for r in ok if r.get("valid_tool", True)) / n, 3) if n else 0.0
        ),
        "mean_seconds": mean_seconds,
        "errors": [r["error"] for r in rows if not r["ok"]],
    }


def agreement(a_rows: list[dict], b_rows: list[dict], key: str) -> dict:
    both = [(x, y) for x, y in zip(a_rows, b_rows) if x["ok"] and y["ok"]]
    same = sum(1 for x, y in both if x[key] == y[key])
    return {
        "comparable": len(both),
        "same_rate": round(same / len(both), 3) if both else 0.0,
        "diff": [
            {"id": a.get("id", ""), "json": a[key], "function": b[key]}
            for a, b in zip(a_rows, b_rows)
            if a["ok"] and b["ok"] and a[key] != b[key]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-category", type=int, default=3, help="每类取几条路由样本")
    parser.add_argument("--model", default=AGENT_LLM_MODEL, help="主模型（默认与线上一致）")
    parser.add_argument("--control-model", default="qwen2.5:7b-instruct",
                        help="对照组模型：工具调用格式已知的模型，用来区分「接线坏了」与「MLX 模型不支持」")
    parser.add_argument("--skip-control", action="store_true")
    args = parser.parse_args()

    # KnowledgeSearchTool 只用 spec() 拼提示词，检索引擎在对比里不会被调用
    runtime = build_agent_runtime(SimpleNamespace(), sources_path=AGENT_SOURCES)
    registry = runtime._available(allow_sql=True)
    tool_specs = registry.describe()
    tool_names = registry.names()

    questions = load_questions(args.per_category)
    print(f"样本：路由 {len(questions)} 条 × 步进 {len(STEP_SCENARIOS)} 个场景；主模型 {args.model}")
    print(f"工具清单：{tool_names}")

    report: dict = {"model": args.model, "control_model": args.control_model, "items": []}
    json_routes, fc_routes = [], []
    for q in questions:
        j = run_route(q["question"], tool_specs, "json", args.model)
        j["id"] = q["id"]; j["category"] = q["category"]
        f = run_route(q["question"], tool_specs, "function", args.model)
        f["id"] = q["id"]; f["category"] = q["category"]
        json_routes.append(j); fc_routes.append(f)
        print(f"[route {q['id']}] json={j['intent'] or j['error'][:40]} fc={f['intent'] or f['error'][:40]}")

    json_steps, fc_steps = [], []
    for s in STEP_SCENARIOS:
        j = run_step(s, tool_specs, "json", args.model, tool_names)
        f = run_step(s, tool_specs, "function", args.model, tool_names)
        j["id"] = s["name"]; f["id"] = s["name"]
        json_steps.append(j); fc_steps.append(f)
        print(f"[step {s['name']}] json={j['action'] or j['error'][:40]} fc={f['action'] or f['error'][:40]}")

    control_routes = []
    if not args.skip_control:
        for q in questions:
            f = run_route(q["question"], tool_specs, "function", args.control_model)
            f["id"] = q["id"]; f["category"] = q["category"]
            control_routes.append(f)
            print(f"[control route {q['id']}] fc={f['intent'] or f['error'][:40]}")

    report["main_model_json_route"] = summarize(json_routes)
    report["main_model_fc_route"] = summarize(fc_routes)
    report["main_model_json_step"] = summarize(json_steps)
    report["main_model_fc_step"] = summarize(fc_steps)
    report["control_model_fc_route"] = summarize(control_routes) if control_routes else None
    report["route_intent_agreement"] = agreement(json_routes, fc_routes, "intent")
    report["route_tool_agreement"] = agreement(json_routes, fc_routes, "tool")
    report["step_action_agreement"] = agreement(json_steps, fc_steps, "action")
    report["items"] = {
        "json_route": json_routes, "fc_route": fc_routes,
        "json_step": json_steps, "fc_step": fc_steps, "control_fc_route": control_routes,
    }

    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# JSON 模式 vs 原生 function calling：决策输出通道对比",
        "",
        f"- 主模型：`{args.model}`；对照模型：`{args.control_model}`（仅 FC 通道）",
        f"- 样本：路由 {len(questions)} 条（{', '.join(CATEGORIES)}）、步进 {len(STEP_SCENARIOS)} 个合成场景；同一提示词、`num_predict={AGENT_PLANNER_TOKENS}`、`temperature=0`",
        "- 口径：只量「决策输出能不能稳定拿到、两模式是否判到同一边」，不判路由对错",
        "",
        "| 通道 | n | 解析成功率 | 动作合法率 | 单条均耗时 |",
        "|---|---|---|---|---|",
    ]
    rows = [
        ("主模型 route/json", report["main_model_json_route"]),
        ("主模型 route/function", report["main_model_fc_route"]),
        ("主模型 step/json", report["main_model_json_step"]),
        ("主模型 step/function", report["main_model_fc_step"]),
    ]
    if report["control_model_fc_route"]:
        rows.append((f"对照 {args.control_model} route/function", report["control_model_fc_route"]))
    for label, s in rows:
        lines.append(
            f"| {label} | {s['n']} | {s['parse_ok']}/{s['n']} | {s['valid_action_rate']:.2f} | {s['mean_seconds']:.2f}s |"
        )
    lines += [
        "",
        f"- 两模式意图一致率（两侧都解析成功的条目内）：`{report['route_intent_agreement']['same_rate']:.2f}`"
        f"（可比较 {report['route_intent_agreement']['comparable']} 条）",
        f"- 两模式步进动作一致率：`{report['step_action_agreement']['same_rate']:.2f}`"
        f"（可比较 {report['step_action_agreement']['comparable']} 条）",
    ]
    for label, key in (("主模型 FC route 失败样本", "fc_route"), ("主模型 FC step 失败样本", "fc_step")):
        errors = [it["error"] for it in report["items"][key] if not it["ok"]]
        if errors:
            lines.append(f"- {label}：")
            lines.extend(f"  - {e}" for e in errors)
        else:
            lines.append(f"- {label}：无")
    lines.append("")
    lines.append("逐条读数见 `eval/reports/fc_comparison.json`。")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n写出：{OUT_JSON} 与 {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
