import pytest

from monkeycode.plan import DefaultPlanManager, PlanDocument, PlanStatus, parse_plan


def test_plan_happy_path_and_checkpoint_recovery() -> None:
    manager = DefaultPlanManager()
    plan = manager.create("1. 读取文件\n2. 运行测试")
    assert plan.status is PlanStatus.AWAITING_CONFIRMATION
    assert [step.id for step in plan.steps] == ["step_1", "step_2"]

    running = manager.mark_tool_started(plan, "call_1")
    completed = manager.mark_tool_result(running, "call_1", True)

    assert completed.steps[0].status is PlanStatus.COMPLETED
    assert completed.status is PlanStatus.RUNNING
    restored = manager.recover(
        [{"type": "plan_checkpoint", "payload": {"plan": completed.to_dict()}}]
    )
    assert restored == completed


def test_plan_rejects_empty_input_and_replans_once() -> None:
    manager = DefaultPlanManager()
    with pytest.raises(ValueError):
        parse_plan("")
    with pytest.raises(ValueError):
        parse_plan("没有明确步骤的说明")

    plan = manager.mark_tool_started(manager.create("1. 执行测试"), "call_1")
    failed = manager.mark_tool_result(plan, "call_1", False, "测试失败")
    replanned = manager.apply_replan(failed, "1. 修复测试\n2. 重新执行")
    assert replanned.replan_count == 1
    assert replanned.status is PlanStatus.AWAITING_CONFIRMATION
    assert not manager.can_replan(
        PlanDocument(
            plan_id=replanned.plan_id,
            steps=replanned.steps,
            status=PlanStatus.FAILED,
            replan_count=replanned.replan_count,
        )
    )


def test_interrupted_running_step_becomes_unknown() -> None:
    manager = DefaultPlanManager()
    running = manager.mark_tool_started(manager.create("1. 执行命令"), "call_1")
    interrupted = manager.mark_interrupted(running)
    assert interrupted.status is PlanStatus.UNKNOWN
    assert interrupted.steps[0].status is PlanStatus.UNKNOWN
    assert interrupted.steps[0].error == "execution interrupted"
