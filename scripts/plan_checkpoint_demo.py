from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from monkeycode.plan import DefaultPlanManager
from monkeycode.session_archive import SessionArchive


def main() -> None:
    manager = DefaultPlanManager()
    plan = manager.create("1. 读取文件\n2. 运行测试")
    plan = manager.mark_tool_started(plan, "call_read")
    plan = manager.mark_tool_result(plan, "call_read", True)
    plan = manager.mark_tool_started(plan, "call_test")
    plan = manager.mark_tool_result(plan, "call_test", False, "测试失败")
    print(f"failed plan: {plan.status.value}, replans={plan.replan_count}")

    replacement = manager.apply_replan(plan, "1. 修复测试\n2. 重新运行测试")
    print(f"replanned: {replacement.status.value}, replans={replacement.replan_count}")

    with TemporaryDirectory() as directory:
        archive = SessionArchive.create(Path(directory))
        archive.append_plan_created(plan)
        archive.append_plan_replanned(replacement, failure="测试失败")
        restored = archive.restore()
        print(f"restored: {restored.plan.plan_id if restored.plan else 'none'}")


if __name__ == "__main__":
    main()
