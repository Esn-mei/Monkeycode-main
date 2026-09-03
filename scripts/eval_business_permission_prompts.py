#!/usr/bin/env python3
"""Business-shaped permission benchmark with repeated Agent edit/test loops."""
from __future__ import annotations
import json, statistics, tempfile
from pathlib import Path
from monkeycode.permissions import HumanDecision, PermissionManager, PermissionMode
from monkeycode.tools.base import ToolPolicy

READ = ToolPolicy(tool_name="read_file", category="read", has_side_effects=False)
EDIT = ToolPolicy(tool_name="edit_file", category="write")
COMMAND = ToolPolicy(tool_name="run_command", category="command")

class Counter:
    def __init__(self, decision): self.decision, self.prompts = decision, 0
    def prompt(self, request, decision): self.prompts += 1; return self.decision

def make_tasks():
    tasks = []
    for i in range(10):
        files = [f"src/module_{i}_a.py", f"src/module_{i}_b.py", f"tests/test_module_{i}.py"]
        command = f"pytest tests/test_module_{i}.py"
        ops = []
        for iteration in range(5):
            ops += [("read_file", files[0]), ("read_file", files[1]), ("edit_file", files[0]), ("edit_file", files[1]), ("run_command", command)]
            if iteration in (1, 3): ops += [("edit_file", files[2]), ("run_command", "pytest")]
        tasks.append({"name": f"business_task_{i+1:02d}", "ops": ops})
    return tasks

def execute_task(root, task, decision):
    counter, manager = Counter(decision), PermissionManager(mode=PermissionMode.DEFAULT, prompter=Counter(decision))
    counter = manager.prompter
    allowed = denied = 0
    for tool, target in task["ops"]:
        if tool == "read_file": args, policy = {"path": target}, READ
        elif tool == "edit_file": args, policy = {"path": target, "old_text": "old", "new_text": "new"}, EDIT
        else: args, policy = {"command": target}, COMMAND
        result = manager.authorize(tool, args, policy, root)
        allowed += int(result.allowed); denied += int(not result.allowed)
    return {"task": task["name"], "operations": len(task["ops"]), "prompts": counter.prompts, "allowed": allowed, "denied": denied, "success": denied == 0}

def p95(values): return sorted(values)[max(0, min(len(values)-1, int(len(values)*0.95 + 0.999)-1))]
def summarize(rows):
    values = [r["prompts"] for r in rows]
    return {"tasks": len(rows), "total_operations": sum(r["operations"] for r in rows), "total_prompts": sum(values), "mean_prompts": round(statistics.mean(values), 2), "median_prompts": statistics.median(values), "p95_prompts": p95(values), "success_rate": round(sum(r["success"] for r in rows)/len(rows), 4)}

def main():
    with tempfile.TemporaryDirectory(prefix="monkeycode-business-eval-") as tmp:
        root, workload = Path(tmp), make_tasks()
        baseline_rows = [execute_task(root, t, HumanDecision.ALLOW_ONCE) for t in workload]
        optimized_rows = [execute_task(root, t, HumanDecision.ALLOW_SESSION) for t in workload]
        baseline, optimized = summarize(baseline_rows), summarize(optimized_rows)
        report = {"benchmark": "business_task_permission_replay_v2", "task_count": 10, "session_model": "one PermissionManager session per task; identical operation sequence in both groups", "workload": "10 coding tasks; 5 edit/test iterations per task; repeated edits to 3 files and repeated test commands", "baseline": baseline, "optimized": optimized, "prompt_reduction_rate": round((baseline["mean_prompts"]-optimized["mean_prompts"])/baseline["mean_prompts"], 4), "baseline_tasks": baseline_rows, "optimized_tasks": optimized_rows, "limitations": ["真实权限执行器任务回放，不包含模型推理", "命令未实际执行，仅测量授权决策", "不等同于真实SWE-bench修复成功率"]}
        Path.cwd().joinpath("business-permission-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
if __name__ == "__main__": main()
