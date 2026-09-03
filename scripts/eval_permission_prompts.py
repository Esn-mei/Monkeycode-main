#!/usr/bin/env python3
"""Measure permission prompts and verify hard security boundaries."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from monkeycode.permissions import HumanDecision, PermissionManager, PermissionMode
from monkeycode.tools.base import ToolPolicy


WRITE = ToolPolicy(tool_name="write_file", category="write")
COMMAND = ToolPolicy(tool_name="run_command", category="command")
READ = ToolPolicy(tool_name="read_file", category="read", has_side_effects=False)


class CountingPrompter:
    def __init__(self, decision: HumanDecision):
        self.decision = decision
        self.count = 0

    def prompt(self, request, decision):
        self.count += 1
        return self.decision


def run_group(root: Path, decision: HumanDecision) -> dict:
    prompter = CountingPrompter(decision)
    manager = PermissionManager(mode=PermissionMode.DEFAULT, prompter=prompter)
    results = []
    # Ten repeated operations for each of five targets: 50 operations/session.
    for target in ["src/a.py", "src/b.py", "tests/a.py", "docs/a.md", "config/dev.yaml"]:
        for i in range(10):
            result = manager.authorize("write_file", {"path": target, "content": str(i)}, WRITE, root)
            results.append(result.allowed)
    return {"operations": len(results), "successful": sum(results), "prompts": prompter.count}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="monkeycode-permission-eval-") as tmp:
        root = Path(tmp)
        baseline = run_group(root, HumanDecision.ALLOW_ONCE)
        optimized = run_group(root, HumanDecision.ALLOW_SESSION)
        manager = PermissionManager(mode=PermissionMode.ALLOW)
        dangerous = manager.authorize("run_command", {"command": "rm -rf /"}, COMMAND, root)
        escape = manager.authorize("read_file", {"path": "../secret.txt"}, READ, root)
        report = {
            "benchmark": "permission_prompt_microbenchmark",
            "sessions": 1,
            "workload": "50 write operations, five repeated targets, ten calls per target",
            "baseline": baseline,
            "optimized": optimized,
            "prompt_reduction_rate": round((baseline["prompts"] - optimized["prompts"]) / baseline["prompts"], 4),
            "hard_boundary_checks": {
                "dangerous_command_denied": not dangerous.allowed and dangerous.denial_result.error_type == "dangerous_command_denied",
                "workspace_escape_denied": not escape.allowed and escape.denial_result.error_type == "path_outside_workspace",
            },
            "interpretation": "Controlled permission-layer microbenchmark; not a representative end-to-end Agent task sample.",
        }
        output = Path.cwd() / "permission-prompt-report.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if all(report["hard_boundary_checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
