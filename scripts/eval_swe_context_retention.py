#!/usr/bin/env python3
"""Fair retention A/B test: naive truncation vs real ContextManager summary."""
from __future__ import annotations
import argparse, hashlib, json, os, sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from monkeycode.config import ContextConfig
from monkeycode.context import ContextManager
from monkeycode.messages import StreamEvent
from monkeycode.session import ChatSession

class RealSummaryProvider:
    def __init__(self, key: str, base_url: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=key, base_url=base_url.rstrip("/"))
        self.model = model
    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        payload = []
        if prompt_payload and prompt_payload.stable_system_text:
            payload.append({"role": "system", "content": prompt_payload.stable_system_text})
        payload.extend({"role": m.role, "content": str(m.content)} for m in messages)
        response = self.client.chat.completions.create(model=self.model, messages=payload, temperature=0, stream=True)
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield StreamEvent(type="text_delta", text=chunk.choices[0].delta.content)
        yield StreamEvent(type="done")

def load_rows(path: Path, limit: int) -> list[dict[str, Any]]:
    from datasets import load_dataset
    data = load_dataset("parquet", data_files=str(path), split="train")
    return [dict(data[i]) for i in range(min(limit, len(data)))]

def facts(row: dict[str, Any], i: int) -> list[str]:
    task = str(row.get("instance_id") or row.get("id") or f"row-{i}")
    repo = str(row.get("repo") or row.get("repository") or "unknown")
    problem = " ".join(str(row.get("problem_statement") or "").split())[:160]
    commit = str(row.get("base_commit") or "unknown")[:12]
    tests = str(row.get("FAIL_TO_PASS") or row.get("fail_to_pass") or "未提供")[:120]
    return [f"FACT_TASK: {task}", f"FACT_REPO: {repo}", f"FACT_COMMIT: {commit}", f"FACT_GOAL: {problem or ('修复 ' + task + ' 中的问题并通过测试')}", f"FACT_TESTS: {tests}", "FACT_TODO: 修改代码后运行相关测试并核对回归"]

def _layout(row: dict[str, Any], i: int) -> tuple[list[str], list[str]]:
    """Use a deterministic, task-specific layout so every case is different."""
    task = str(row.get("instance_id") or row.get("id") or i)
    digest = hashlib.sha256(task.encode()).digest()
    recent_count = 1 + digest[0] % 4
    order = list(range(6)); order.sort(key=lambda n: digest[n + 1])
    recent = [f for n, f in enumerate(facts(row, i)) if n in order[:recent_count]]
    old = [f for n, f in enumerate(facts(row, i)) if n not in order[:recent_count]]
    return old, recent

def score(required: list[str], text: str) -> tuple[float, list[str]]:
    missing = []
    for fact in required:
        label, value = fact.split(": ", 1)
        if label in {"FACT_TASK", "FACT_REPO", "FACT_COMMIT", "FACT_TESTS"}:
            ok = value in text
        elif label == "FACT_GOAL":
            ok = "问题" in text or "修复" in text
        else:  # FACT_TODO
            ok = "测试" in text or "回归" in text
        if not ok: missing.append(fact)
    return (len(required) - len(missing)) / len(required), missing

def baseline_text(session: ChatSession, keep_messages: int) -> str:
    # Old behavior: discard old messages without summarization or archiving.
    return "\n".join(str(m.content) for m in session.messages[-keep_messages:])

def build_session(row: dict[str, Any], i: int) -> tuple[ChatSession, list[str]]:
    """Construct a realistic SWE-bench coding trajectory from dataset fields."""
    required = facts(row, i)
    problem = str(row.get("problem_statement") or "")[:4000]
    hints = str(row.get("hints_text") or "")[:1200]
    patch = str(row.get("patch") or "")[:2500]
    test_patch = str(row.get("test_patch") or "")[:1800]
    old_facts, recent_facts = _layout(row, i)
    session = ChatSession()
    session.add_user_message("请修复以下 SWE-bench 问题：\n" + problem + "\n" + "\n".join(old_facts))
    session.add_assistant_message("我先定位相关模块和复现路径，检查基线提交。")
    session.add_user_message("git checkout base_commit\n" + str(row.get("base_commit") or "unknown"))
    session.add_assistant_message("已读取仓库结构，准备检查实现和测试。")
    session.add_user_message("工具 read_file 返回：\n" + patch)
    session.add_assistant_message("初步分析完成，发现需要同时关注输入校验和回归行为。")
    session.add_user_message("运行测试失败：\n" + test_patch)
    session.add_assistant_message("提出修复方案并准备修改代码。")
    session.add_user_message("补充线索：\n" + hints + "\n" + "\n".join(recent_facts))
    session.add_assistant_message("修复已完成，下一步运行 FAIL_TO_PASS 和 PASS_TO_PASS 测试。")
    return session, required

def optimized_text(row, i, provider, work_dir):
    session, required = build_session(row, i)
    manager = ContextManager(provider, work_dir, ContextConfig(context_window_tokens=180, auto_safety_margin_tokens=20, recent_tail_tokens=30, recent_tail_min_messages=3))
    status = manager.prepare_before_request(session)
    rate, missing = score(required, "\n".join(str(m.content) for m in session.messages))
    return rate, missing, status

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--dataset", type=Path, required=True); p.add_argument("--limit", type=int, default=20); p.add_argument("--work-dir", type=Path, default=Path(".context-retention-real")); p.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY")); p.add_argument("--base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1"); p.add_argument("--model", default="qwen3.7-max"); args = p.parse_args()
    if not args.api_key: p.error("请设置 OPENAI_API_KEY 或传入 --api-key")
    rows = load_rows(args.dataset, args.limit); base_rates=[]; opt_rates=[]; failures=[]
    for i, row in enumerate(rows):
        session, required = build_session(row, i)
        base_rate,_=score(required, baseline_text(session, 3)); opt,missing,status=optimized_text(row, i, RealSummaryProvider(args.api_key,args.base_url,args.model), args.work_dir/"optimized")
        base_rates.append(base_rate); opt_rates.append(opt); failures.append({"index":i,"missing":missing}) if missing else None
        print(f"{i+1}/{len(rows)} baseline={base_rate:.0%} optimized={opt:.0%} status={status.skipped_reason or 'compacted'}", flush=True)
    result={"dataset":str(args.dataset),"model":args.model,"samples":len(rows),"baseline_retention":round(sum(base_rates)/len(rows),4),"optimized_retention":round(sum(opt_rates)/len(rows),4),"improvement":round(sum(opt_rates)/len(rows)-sum(base_rates)/len(rows),4),"optimized_failures":failures}
    print(json.dumps(result,ensure_ascii=False,indent=2)); args.work_dir.mkdir(parents=True,exist_ok=True); (args.work_dir/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")

if __name__ == "__main__": raise SystemExit(main())
