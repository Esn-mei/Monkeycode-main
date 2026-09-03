# 结构化 Plan + Checkpoint + 失败重规划技术方案

## 1. 上下文注入

**项目地址：** <https://github.com/Initial512/Monkeycode>

**项目核心架构：** 基于 ReAct 的单 Agent 工具调用循环，已经有独立的 `/plan` 只读模式和 `/do` 执行入口，但尚未形成真正的 Plan-and-Execute 状态层。

**选定的贡献方向：** 将当前自然语言计划升级为可持久化、可观察、可恢复的结构化 Plan，并在工具失败时触发一次受控的失败重规划。

**涉及的核心文件：**

- `monkeycode/agent.py`：`AgentRunner.run_turn()` 驱动模型请求、工具执行和下一轮循环。
- `monkeycode/tui.py`：`_run_turn_and_maybe_confirm_plan()` 处理 `/plan`、用户确认和 `/do`。
- `monkeycode/session_archive.py`：`SessionArchive.append()`、`restore()` 负责 JSONL 会话持久化和恢复。
- `monkeycode/events.py`：`AgentEvent` 是当前 Agent 状态向 TUI 和后台任务传播的事件载体。
- `monkeycode/command/builtin_ui.py`、`monkeycode/command/builtin_prompt.py`：定义 `/plan`、`/do` 的命令行为。

**当前实现缺陷：**

- `/plan` 只限制工具集合，模型输出的计划是普通文本，没有步骤 ID、依赖、状态或失败原因。
- `/do` 通过固定提示词依赖上下文中的旧计划，计划没有独立保存，无法可靠恢复。
- `AgentRunner.run_turn()` 能处理工具错误并继续循环，但没有把错误映射为某个计划步骤的失败，也没有显式重规划策略。
- `SessionArchive.restore()` 能截断未完成的工具调用，但不知道上次停在哪个计划步骤，也无法避免恢复后重复执行有副作用的调用。
- `AgentEvent` 已有进度、工具调用和完成事件，但没有计划步骤事件，TUI 和后台任务只能展示迭代或工具名称。

---

## 2. 问题边界定义

**要解决的核心问题：** 让一个经过用户确认的计划具备明确步骤状态，并能在工具失败或进程中断后安全地告诉用户停在哪里、从哪里继续或重新规划。

**不在本次 PR 范围内的问题：**

1. 不实现通用 DAG 调度、条件分支、循环步骤或跨多个 Agent 的全局计划。
2. 不改变 Provider 协议、Tool 接口、权限判断规则和 MCP 协议。
3. 不保证外部命令或远程 MCP 调用具备事务回滚；对可能已经产生副作用的未完成调用只标记为 `unknown`，要求用户重新确认。

**成功标准：**

- `/plan` 确认后生成至少包含 `plan_id`、步骤 ID、描述和状态的结构化计划。
- 每个步骤至少经过 `pending -> running -> completed/failed/unknown` 的状态流转，TUI 能显示 `已完成/进行中/失败/待执行`。
- 在进程中断后恢复会话，已完成步骤保持 `completed`，不会自动重复执行；未完成步骤显示为 `unknown` 或 `pending_confirmation`。
- 单个失败步骤最多自动触发一次重规划，重规划失败或再次失败后停止并交给用户，不进入无限循环。
- 旧版 JSONL 会话没有 Plan 事件时仍可正常恢复；现有 `AgentRunner.run_turn()`、`ToolExecutor` 和 Provider 公共调用方式保持不变。
- 新增单元测试覆盖正常执行、恢复和失败重规划 3 条主路径。

**约束条件：**

- 不破坏现有公共 API 的调用方式，新增参数均使用可选值或内部适配。
- 兼容项目当前支持的 Python 3.10+。
- 生产代码改动尽量控制在 300 行以内；若实现完整的自动重规划超出范围，应拆成两个 PR。
- 计划和 checkpoint 事件只保存参数摘要和状态，不默认保存 API Key 或完整敏感输出。

---

## 3. 方案设计

### 方案 1：轻量 Plan 快照

**核心思路：** 不引入复杂状态机，只增加一个可序列化的 `PlanSnapshot`，在 `/plan` 完成后保存计划文本和当前步骤索引。工具调用结束后按顺序推进索引，恢复时加载快照并从下一个步骤继续。

**关键设计决策：**

- 新增 `PlanSnapshot` 数据类，字段只包含 `plan_id`、`steps`、`current_index`、`status`。
- `SessionArchive` 增加 `plan_created`、`plan_progress` 事件。
- TUI 继续负责计划确认，`AgentRunner` 只接收当前计划快照。
- 工具失败时将当前步骤标记为失败，但不自动调用模型重规划。

**数据流：** `/plan` 文本 -> 简单步骤解析 -> `PlanSnapshot` -> JSONL -> `/do` 读取 -> 工具结果推进状态 -> TUI 展示。

**伪代码或接口签名草稿：**

```python
@dataclass
class PlanSnapshot:
    plan_id: str
    steps: list[str]
    current_index: int = 0
    status: str = "draft"

class SessionArchive:
    def append_plan_created(self, plan: PlanSnapshot) -> None: ...
    def append_plan_progress(self, plan: PlanSnapshot) -> None: ...

def next_step(plan: PlanSnapshot, success: bool) -> PlanSnapshot: ...
```

**优点：** 改动最小；容易兼容旧会话；两周内完成概率最高。

**缺点 / 风险：** 只能按顺序推进，无法表达工具与步骤的真实对应关系；失败时仍依赖用户手工修改计划；不能体现真正的重规划能力。

**适用场景：** 只需要“计划可见、可恢复”，暂时不要求 Agent 自动适应失败的版本。

### 方案 2：Plan 状态机 + Checkpoint + 单次重规划（推荐）

**核心思路：** 引入小型 `PlanManager`，维护步骤状态和当前执行上下文；每次有工具调用、工具结果、权限拒绝或取消时写入 checkpoint。失败时只把失败信息注入一次专用 re-plan 请求，由模型返回剩余步骤的替代计划，用户确认后继续执行。

**关键设计决策：**

- 使用固定状态集合：`draft`、`awaiting_confirmation`、`running`、`completed`、`failed`、`paused`、`unknown`。
- 步骤使用稳定 ID，工具调用通过 `step_id` 元数据关联；无法可靠关联时标记当前步骤而不是猜测。
- checkpoint 采用追加式 JSONL 事件，不修改原有消息事件；恢复时取同一 `plan_id` 的最后有效状态。
- 自动重规划设置 `replan_count` 上限为 1；重规划结果必须再次经过用户确认。
- 对权限拒绝、取消和进程中断不自动重试，分别转成 `blocked`、`paused`、`unknown`，避免重复副作用。

**数据流：**

```text
用户输入
  -> PLAN 模式模型输出
  -> 解析 PlanDocument
  -> 用户确认
  -> plan_created + checkpoint
  -> EXECUTE 模式调用工具
  -> tool_started checkpoint
  -> tool_result checkpoint
  -> 成功：步骤 completed，进入下一步
  -> 可恢复失败：生成一次 re-plan 草案
  -> 用户确认后替换剩余步骤
  -> session restore：加载最后 checkpoint，继续或请求确认
```

**伪代码或接口签名草稿：**

```python
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

class PlanManager:
    def create(self, text: str) -> PlanDocument: ...
    def mark_tool_started(self, tool_call_id: str, step_id: str | None) -> PlanDocument: ...
    def mark_tool_result(self, tool_call_id: str, success: bool, error: str | None) -> PlanDocument: ...
    def recover(self, events: list[dict[str, Any]]) -> PlanDocument | None: ...
    def can_replan(self) -> bool: ...

class SessionArchive:
    def append_plan_event(self, event_type: str, plan: PlanDocument, **extra) -> None: ...

def build_replan_prompt(plan: PlanDocument, failure: str) -> str: ...
```

**优点：** 用户能直接看到进度和失败位置；支持中断恢复；重规划行为有上限且需要再次确认；复用现有 AgentEvent、SessionArchive 和 TUI，不改变 Provider/Tool 公共接口。

**缺点 / 风险：** 需要定义自然语言计划到步骤的解析规则；工具调用与步骤的自动对应可能不总是可靠；若把自动重规划做得过深，容易超过 300 行并改变现有行为。

**适用场景：** 需要一个两周内可交付、用户体验明显、又能体现 Agent 工程能力的开源 PR。

### 方案 3：事件溯源工作流引擎

**核心思路：** 将计划、工具调用、权限决策和恢复都抽象为不可变事件，构建完整的 workflow reducer；每个步骤可以有依赖、重试策略、幂等键和人工审批节点。

**关键设计决策：**

- 新增 `Workflow`, `Node`, `Reducer`, `CheckpointStore` 等抽象。
- Agent 只产生意图事件，由 reducer 决定下一步可执行节点。
- ToolExecutor 通过幂等键避免重复副作用，MCP 和命令工具需要额外适配。

**伪代码或接口签名草稿：**

```python
class WorkflowReducer(Protocol):
    def apply(self, state: WorkflowState, event: WorkflowEvent) -> WorkflowState: ...

class CheckpointStore(Protocol):
    def append(self, event: WorkflowEvent) -> None: ...
    def load(self, workflow_id: str) -> WorkflowState | None: ...
```

**优点：** 可扩展性和恢复语义最好，适合长期演进为复杂 Agent 工作流平台。

**缺点 / 风险：** 对现有 Agent、权限、MCP、命令执行和测试的侵入性都很高；需要处理幂等、并发、事件版本迁移；明显超出单个两周 PR 的合理范围。

**适用场景：** 已经有稳定工作流需求和维护者明确支持架构重构时。

### 方案对比矩阵

| 维度 | 方案 1：轻量快照 | 方案 2：状态机 + Checkpoint | 方案 3：事件溯源引擎 |
|---|---|---|---|
| 实现复杂度 | 低 | 中 | 高 |
| 对现有代码侵入性 | 低 | 中低 | 高 |
| 可扩展性 | 低到中 | 中高 | 高 |
| 预计 PR 通过难度 | 低 | 中低 | 高 |
| 面试叙事价值 | 中 | 高 | 高但容易失焦 |
| 用户可感知变化 | 中 | 高 | 高 |
| 两周交付可行性 | 高 | 高（控制 MVP） | 低 |

**推荐方案：** 方案 2

**推荐理由：** 方案 2 在现有 `AgentRunner.run_turn()`、`AgentEvent` 和 `SessionArchive` 上做增量扩展，能直接改善 `/plan`、`/do`、取消和 `/resume` 这些用户路径；同时用 `replan_count <= 1`、不自动重试未知副作用和再次人工确认控制风险。方案 1 缺少失败重规划，方案 3 则明显超出当前仓库的 PR 规模。

---

## 4. 实现规划

### 需要新增的文件

| 文件路径 | 职责 | 预计行数 |
|---|---|---:|
| `monkeycode/plan.py` | `PlanStatus`、`PlanStep`、`PlanDocument`、解析和状态转移 | 100～140 |
| `tests/test_plan_manager.py` | 计划解析、状态转移、恢复和重规划上限测试 | 100～150 |

若维护者要求单个 PR 的生产改动严格低于 300 行，可以把 `PlanManager` 的持久化适配和自动重规划拆为第二个 PR；第一 PR 只交付结构化计划、checkpoint 和恢复。

### 需要修改的文件

| 文件路径 | 修改内容 | 改动幅度 |
|---|---|---|
| `monkeycode/session_archive.py` | 增加 `plan_created`、`plan_checkpoint`、`plan_replanned` 事件写入；恢复时重建最后有效 Plan 状态；兼容旧 JSONL | 中 |
| `monkeycode/events.py` | 增加 `plan_step` 事件所需的可选 `plan_id`、`step_id`、`plan_status` 元数据，保持构造兼容 | 小 |
| `monkeycode/agent.py` | 在工具开始/结果/取消/失败处调用 `PlanManager`；失败时最多构造一次 re-plan 请求；不改变普通非 Plan 模式 | 中 |
| `monkeycode/tui.py` | 展示计划步骤和状态；恢复时提示继续、重新确认或放弃；处理重规划确认 | 中 |
| `monkeycode/command/builtin_ui.py` | `/resume` 恢复后读取并显示未完成 Plan 状态（如果由 TUI 统一处理，可不改） | 小 |
| `tests/test_agent_runner.py` | 增加 Plan 模式下工具结果与 checkpoint 的集成测试 | 中 |
| `tests/test_session_archive.py` | 增加 Plan 事件的 JSONL round-trip 和旧会话兼容测试 | 小 |

### 实现步骤（有序）

**第 1 步：定义状态模型和解析契约。**

在 `monkeycode/plan.py` 中定义固定状态、步骤 ID 规则和 `PlanDocument`。解析器只接受稳定的编号列表；无法解析时保留原文本并要求用户重新确认，不静默猜测步骤。

**第 2 步：实现纯内存状态转移。**

为工具开始、工具成功、工具失败、取消和未知副作用分别定义状态转移，并写纯函数测试。此时不接入 Agent，项目应保持可运行。

**第 3 步：接入 SessionArchive checkpoint。**

增加追加式 Plan 事件和恢复 reducer。旧会话没有 Plan 事件时返回 `None`；损坏或不完整的 Plan 事件只记录诊断，不影响普通消息恢复。

**第 4 步：接入 AgentRunner 的工具生命周期。**

在 `ToolScheduler` 产生 `tool_call_started` 和 `tool_result` 的现有路径上补充 Plan 元数据。只在存在活动 Plan 时启用状态更新，普通执行模式不改变。

**第 5 步：增加单次失败重规划。**

当步骤失败且错误不是权限拒绝、取消或未知副作用时，构造包含失败原因和剩余步骤的 re-plan 请求。`replan_count` 达到 1 后停止自动重规划，并向用户展示失败状态。

**第 6 步：补齐 TUI 和恢复交互。**

用户确认计划后显示步骤清单；执行时显示当前步骤；恢复时若存在 `paused/unknown` 状态，要求用户选择继续确认或重新规划。所有新增交互都通过现有 TUI 入口完成。

**第 7 步：运行集成验证并整理 PR。**

运行计划相关单测、会话恢复测试、Agent 集成测试和 `compileall`；记录一次正常执行、一次工具失败、一次中断恢复的终端输出作为 PR 描述中的演示证据。

### 验证策略

**手动验证：**

1. 输入 `/plan`，提交一个包含“读取文件 -> 修改文件 -> 运行测试”的任务。
2. 确认计划，观察 TUI 显示步骤从 `pending` 到 `running`、`completed`。
3. 使用一个必然失败的测试命令，确认当前步骤显示 `failed`，并且只出现一次 re-plan 确认。
4. 在工具调用后主动 `/cancel` 或终止进程，再使用 `--resume-session <id>` 恢复，确认已完成步骤不重复执行，未完成步骤要求确认。

**单元测试：**

- `test_plan_status_transitions_and_invalid_transition()`：验证合法状态流转以及非法流转被拒绝。
- `test_plan_checkpoint_round_trip_and_legacy_session()`：验证 Plan 事件恢复，并确保没有 Plan 事件的旧会话行为不变。
- `test_failed_step_replans_once_then_stops()`：验证失败只触发一次重规划，第二次失败不进入循环。
- `test_unknown_side_effect_is_not_retried_automatically()`：验证中断中的命令或 MCP 调用不会被自动重复执行。

**边界情况：**

- 模型输出没有编号步骤、步骤为空或重复 ID。
- 一轮返回多个工具调用，无法唯一映射到某个步骤。
- 权限拒绝、工具超时、MCP 连接失败和未知工具分别处理。
- JSONL 在 checkpoint 写入中途损坏、重复事件或事件版本缺失。
- 旧会话恢复后没有 `plan_id`，以及多个历史 Plan 只取最后一个未完成 Plan。
- 用户在 re-plan 确认时拒绝，系统应保持原计划的失败/暂停状态。

---

## 5. 面试叙事框架

**背景（Context）：**

MonkeyCode 是一个基于 ReAct 的终端 AI 编程助手，模型通过工具读取和修改工作区。项目已经有 `/plan` 和 `/do`，但计划只是上下文中的自然语言，工具失败或进程中断后无法知道任务停在哪一步，也无法安全恢复。我选择这个方向，是因为它同时影响计划执行、Human-in-the-loop 和会话恢复，是一个用户能明显感知且改动边界清晰的架构缺口。

**我做了什么（Contribution）：**

我为 MonkeyCode 引入了轻量的结构化 Plan 状态机：为计划步骤分配稳定 ID，记录工具执行前后的 checkpoint，将状态持久化到现有 JSONL 会话，并在可恢复失败时提供一次需要人工确认的重规划流程。普通聊天和非 Plan 模式继续走原有 Agent loop。

**我怎么分步做的（Process）：**

1. 重新阅读 `AgentRunner`、`TUI`、`SessionArchive` 和事件模型，确定不修改 Provider 和 Tool 公共接口，把状态放在 Agent loop 与会话归档之间。
2. 对比轻量快照、状态机和事件溯源三个方案，选择状态机方案，因为它能提供明确状态，又不会引入完整工作流引擎。
3. 先实现纯内存状态转移和失败分类，再接入追加式 checkpoint，保证每一步都能独立测试。
4. 将工具开始、工具结果、取消和恢复映射到 Plan 状态，并为未知副作用设置“不自动重试”策略。
5. 最后接入 TUI 展示和一次性 re-plan 确认，使用 fake provider 验证正常执行、失败和恢复路径。

**成果（Result）：**

- PR 状态：待提交，当前方案未声称已经合并。
- 对项目的影响：`/plan` 从文本确认升级为可见步骤；`/resume` 能恢复计划状态；工具失败有明确归因；重规划次数受控。
- 可量化指标：计划步骤状态覆盖率 100%；单次失败最多 1 次自动重规划；恢复测试保证已完成工具调用不重复执行；新增至少 4 个核心测试场景。
- 维护者反馈：提交 PR 后再补充真实 review 引用，不提前编造反馈。

**遇到的困难（Challenges）：**

- **困难描述：** 自然语言计划与工具调用并不总是一一对应。**我的解决过程：** 先使用显式 `step_id` 和保守映射；无法确定时标记步骤为 `unknown`，不让系统猜测。**结论：** 可恢复性优先于自动化程度，未知副作用必须交给用户确认。
- **困难描述：** 失败重试可能重复执行写文件、命令或 MCP 操作。**我的解决过程：** 按失败类型分级，权限拒绝、取消和未知副作用不自动重试，普通可恢复错误最多触发一次 re-plan。**结论：** 重规划必须有次数上限并重新经过人工确认。

**下次可以怎么优化（Reflection）：**

- 技术层面：如果后续工具数量和计划复杂度增加，可以将当前 Plan 事件升级为版本化 reducer，并引入更严格的工具幂等键。
- 流程层面：先向维护者提交“结构化状态 + 恢复”小 PR，再单独提交自动重规划，降低 review 风险。
- 范围层面：不在第一版引入 DAG、向量检索或跨 Agent 全局调度，把指标和真实用户反馈作为下一阶段依据。

**能体现的核心能力：**

- Agent 状态机和故障恢复设计能力。
- Human-in-the-loop 与副作用安全控制能力。
- 增量式开源贡献、兼容性约束和可测试性设计能力。

