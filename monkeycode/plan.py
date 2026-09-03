from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import re
from typing import Any, Protocol
import uuid


class PlanStatus(str, Enum):
    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PlanStep:
    id: str
    description: str
    status: PlanStatus = PlanStatus.DRAFT
    tool_call_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class PlanDocument:
    plan_id: str
    steps: tuple[PlanStep, ...]
    status: PlanStatus = PlanStatus.DRAFT
    replan_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "steps": [
                {
                    "id": step.id,
                    "description": step.description,
                    "status": step.status.value,
                    "tool_call_id": step.tool_call_id,
                    "error": step.error,
                }
                for step in self.steps
            ],
            "status": self.status.value,
            "replan_count": self.replan_count,
        }


class PlanManager(Protocol):
    def create(self, text: str) -> PlanDocument:
        ...

    def mark_tool_started(
        self,
        plan: PlanDocument,
        tool_call_id: str,
        step_id: str | None = None,
    ) -> PlanDocument:
        ...

    def mark_tool_result(
        self,
        plan: PlanDocument,
        tool_call_id: str,
        success: bool,
        error: str | None = None,
    ) -> PlanDocument:
        ...

    def recover(self, events: list[dict[str, Any]]) -> PlanDocument | None:
        ...

    def can_replan(self, plan: PlanDocument) -> bool:
        ...

    def mark_interrupted(self, plan: PlanDocument) -> PlanDocument:
        ...

    def apply_replan(self, plan: PlanDocument, text: str) -> PlanDocument:
        ...


class DefaultPlanManager:
    """纯函数式更新 PlanDocument 的默认实现。"""

    max_replans = 1

    def create(self, text: str) -> PlanDocument:
        return parse_plan(text)

    def mark_tool_started(
        self,
        plan: PlanDocument,
        tool_call_id: str,
        step_id: str | None = None,
    ) -> PlanDocument:
        index = _step_index(plan.steps, step_id) if step_id else _next_pending_index(plan.steps)
        if index is None:
            return plan
        step = plan.steps[index]
        if step.status not in {PlanStatus.DRAFT, PlanStatus.AWAITING_CONFIRMATION, PlanStatus.PAUSED}:
            return plan
        updated = replace(
            step,
            status=PlanStatus.RUNNING,
            tool_call_id=tool_call_id,
            error=None,
        )
        return replace(
            plan,
            steps=_replace_step(plan.steps, index, updated),
            status=PlanStatus.RUNNING,
        )

    def mark_tool_result(
        self,
        plan: PlanDocument,
        tool_call_id: str,
        success: bool,
        error: str | None = None,
    ) -> PlanDocument:
        index = _step_index_by_tool_call(plan.steps, tool_call_id)
        if index is None:
            return plan
        step = plan.steps[index]
        if step.status != PlanStatus.RUNNING:
            return plan
        status = PlanStatus.COMPLETED if success else PlanStatus.FAILED
        updated = replace(step, status=status, error=None if success else error or "tool failed")
        steps = _replace_step(plan.steps, index, updated)
        document_status = PlanStatus.COMPLETED if success and all(
            item.status == PlanStatus.COMPLETED for item in steps
        ) else PlanStatus.RUNNING if success else PlanStatus.FAILED
        return replace(plan, steps=steps, status=document_status)

    def recover(self, events: list[dict[str, Any]]) -> PlanDocument | None:
        latest: PlanDocument | None = None
        for event in events:
            if not isinstance(event, dict) or event.get("type") not in {
                "plan_created",
                "plan_checkpoint",
                "plan_replanned",
            }:
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            candidate = _plan_from_dict(payload.get("plan"))
            if candidate is not None:
                latest = candidate
        return latest

    def can_replan(self, plan: PlanDocument) -> bool:
        return plan.status == PlanStatus.FAILED and plan.replan_count < self.max_replans

    def mark_interrupted(self, plan: PlanDocument) -> PlanDocument:
        steps = tuple(
            replace(step, status=PlanStatus.UNKNOWN, error="execution interrupted")
            if step.status == PlanStatus.RUNNING
            else step
            for step in plan.steps
        )
        if steps == plan.steps:
            return plan
        return replace(plan, steps=steps, status=PlanStatus.UNKNOWN)

    def apply_replan(self, plan: PlanDocument, text: str) -> PlanDocument:
        if not self.can_replan(plan):
            raise ValueError("plan replan limit reached")
        replacement = parse_plan(text)
        completed = tuple(step for step in plan.steps if step.status == PlanStatus.COMPLETED)
        offset = len(completed)
        remaining = tuple(
            replace(step, id=f"step_{offset + index + 1}", status=PlanStatus.DRAFT, tool_call_id=None, error=None)
            for index, step in enumerate(replacement.steps)
        )
        return PlanDocument(
            plan_id=plan.plan_id,
            steps=completed + remaining,
            status=PlanStatus.AWAITING_CONFIRMATION,
            replan_count=plan.replan_count + 1,
        )


def parse_plan(text: str) -> PlanDocument:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("plan text must not be empty")
    steps: list[PlanStep] = []
    for line in text.splitlines():
        match = re.match(r"^\s*(?:\d+[.)]|[-*])\s+(.+?)\s*$", line)
        if not match:
            continue
        description = match.group(1).strip()
        if description:
            steps.append(PlanStep(id=f"step_{len(steps) + 1}", description=description))
    if not steps:
        raise ValueError("plan must contain numbered or bulleted steps")
    return PlanDocument(
        plan_id=f"plan_{uuid.uuid4().hex[:8]}",
        steps=tuple(steps),
        status=PlanStatus.AWAITING_CONFIRMATION,
    )


def build_replan_prompt(plan: PlanDocument, failure: str) -> str:
    remaining = [
        f"- {step.id}: {step.description}"
        for step in plan.steps
        if step.status != PlanStatus.COMPLETED
    ]
    return "\n".join(
        [
            "当前执行计划中的一个步骤失败，请仅为剩余步骤生成替代计划。",
            f"失败原因：{failure.strip() or '未知错误'}",
            "已完成步骤不会重复执行。",
            "请使用编号列表输出新的剩余步骤，并保持步骤短小、可验证。",
            "",
            "剩余步骤：",
            *remaining,
        ]
    )


def _step_index(steps: tuple[PlanStep, ...], step_id: str) -> int | None:
    for index, step in enumerate(steps):
        if step.id == step_id:
            return index
    return None


def _step_index_by_tool_call(steps: tuple[PlanStep, ...], tool_call_id: str) -> int | None:
    for index, step in enumerate(steps):
        if step.tool_call_id == tool_call_id:
            return index
    return None


def _next_pending_index(steps: tuple[PlanStep, ...]) -> int | None:
    for index, step in enumerate(steps):
        if step.status in {PlanStatus.DRAFT, PlanStatus.AWAITING_CONFIRMATION, PlanStatus.PAUSED}:
            return index
    return None


def _replace_step(steps: tuple[PlanStep, ...], index: int, step: PlanStep) -> tuple[PlanStep, ...]:
    updated = list(steps)
    updated[index] = step
    return tuple(updated)


def _plan_from_dict(value: Any) -> PlanDocument | None:
    if not isinstance(value, dict):
        return None
    plan_id = value.get("plan_id")
    raw_steps = value.get("steps")
    if not isinstance(plan_id, str) or not plan_id.strip() or not isinstance(raw_steps, list):
        return None
    steps: list[PlanStep] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            return None
        try:
            status = PlanStatus(str(raw_step.get("status", PlanStatus.DRAFT.value)))
        except ValueError:
            return None
        step_id = raw_step.get("id")
        description = raw_step.get("description")
        if not isinstance(step_id, str) or not step_id or not isinstance(description, str):
            return None
        steps.append(
            PlanStep(
                id=step_id,
                description=description,
                status=status,
                tool_call_id=raw_step.get("tool_call_id") if isinstance(raw_step.get("tool_call_id"), str) else None,
                error=raw_step.get("error") if isinstance(raw_step.get("error"), str) else None,
            )
        )
    try:
        status = PlanStatus(str(value.get("status", PlanStatus.DRAFT.value)))
    except ValueError:
        return None
    replan_count = value.get("replan_count", 0)
    if not isinstance(replan_count, int) or isinstance(replan_count, bool) or replan_count < 0:
        return None
    return PlanDocument(plan_id=plan_id, steps=tuple(steps), status=status, replan_count=replan_count)
