# MonkeyCode 项目架构导览

这份文档用于帮助第一次阅读 MonkeyCode 的开发者建立整体心智模型。项目是一个 Python 终端 AI 编程助手：用户在终端输入任务，模型通过工具读取/修改工作区、执行命令，并在需要时创建子 Agent、调用 MCP 服务或触发 Hooks。

## 1. 一句话模型

```text
终端输入
  -> TUI/会话
  -> AgentRunner（模型-工具循环）
  -> Provider（OpenAI/Anthropic）
  -> ToolScheduler
  -> ToolExecutor（权限与工具执行）
  -> 文件、命令、搜索、Skill、子 Agent、MCP
  -> 工具结果写回会话，再请求模型
```

Agent 循环会持续到模型不再返回工具调用、达到最大迭代次数、发生错误或用户取消。

## 2. 启动与组装流程

程序入口只有两层：

```text
python -m monkeycode
  monkeycode/__main__.py
    -> monkeycode/cli.py:main()
```

`cli.main()` 是应用的组合根（composition root），负责把基础设施拼起来：

```text
配置文件                 monkeycode.config.load_config
模型客户端               providers.factory.create_provider
内置工具注册表           tools.create_default_registry
工具执行器               tools.create_default_executor
权限规则                 permissions
子 Agent / 后台任务      agent_tool + subagent + task
Git worktree             worktree.WorktreeManager
MCP 远程工具             mcp.McpToolManager
Hooks                    hooks.HookEngine
终端界面                 tui.run_chat_loop
```

初始化完成后，`cli.py` 将所有对象传入 TUI。`cli.py` 本身不承载 Agent 业务逻辑，阅读时可以把它当作依赖注入和生命周期管理代码。

## 3. 一次用户请求的时序

```text
用户
 | 输入消息
 v
TUI (tui.py)
 | 调用 AgentRunner.run_turn
 v
AgentRunner (agent.py)
 | 构造 prompt、加载 instructions/skills/memory
 | ContextManager 检查并压缩上下文
 | Provider.stream_chat(...)
 v
模型 Provider (providers/)
 | 返回文本或 tool_calls（支持 SSE 流式事件）
 v
AgentRunner
 | 有 tool_calls ?
 +---- 否 -> 输出完成事件，保存 session/memory
 |
 +---- 是 -> ToolScheduler.run_tool_calls
                    |
                    v
              ToolExecutor.execute
                    |
                    +-> 权限检查 / 工作区路径检查
                    +-> ToolRegistry 找到工具
                    +-> 工具实现执行
                    +-> ToolResult
                    |
                    v
              结果写入 ChatSession
                    |
                    +------ 回到 AgentRunner 下一轮请求模型
```

要追踪一条具体请求，建议按 `tui.py -> agent.py:run_turn -> tool_scheduler.py -> tools/executor.py` 的顺序阅读。

## 4. 目录与职责地图

| 目录/文件 | 主要职责 | 阅读重点 |
| --- | --- | --- |
| `monkeycode/cli.py` | 启动、配置、依赖组装、资源关闭 | 应用入口和对象关系 |
| `monkeycode/tui.py` | Prompt Toolkit 终端界面、事件渲染、输入循环 | 用户交互如何映射到 Agent |
| `monkeycode/agent.py` | Agent 主循环、prompt 构建、上下文、会话和事件 | 系统的核心控制流 |
| `monkeycode/tool_scheduler.py` | 批量工具调用、并行只读调用、重复调用缓存、取消 | 模型一次返回多个工具时如何执行 |
| `monkeycode/tools/` | 工具协议、注册表、执行器和内置工具 | 新增工具的入口 |
| `monkeycode/permissions.py` | 黑名单、沙箱、项目/用户规则、交互确认 | 工具执行前的安全决策 |
| `monkeycode/context.py` | token 估算、上下文压缩与状态 | 长对话如何保持在窗口内 |
| `monkeycode/session.py` | `ChatSession` 消息序列 | user/assistant/tool 消息格式 |
| `monkeycode/providers/` | OpenAI/Anthropic 协议适配、流式解析 | 模型协议差异隔离 |
| `monkeycode/prompting.py`、`instructions.py` | 系统提示词、项目指令、动态上下文 | 模型看到的输入从哪里来 |
| `monkeycode/skills/` | Skill 发现、解析、安装、激活和执行 | 可复用工作流扩展 |
| `monkeycode/subagent/`、`agent_fork.py` | 子 Agent 定义、解析和派生 | Agent 协作模型 |
| `monkeycode/task/` | 后台任务的列表、查询、停止、消息传递 | 子 Agent 生命周期 |
| `monkeycode/worktree/` | Git worktree 创建、隔离、清理 | 子 Agent 的代码隔离 |
| `monkeycode/mcp/` | MCP 配置、JSON-RPC、stdio/HTTP 传输和远程工具 | 外部工具接入 |
| `monkeycode/hooks/` | 事件匹配、动作执行、Hook 生命周期 | 自动化和可观测性扩展 |
| `monkeycode/memory.py`、`session_archive.py` | 长期记忆、会话归档与恢复 | 跨轮次/跨会话状态 |
| `monkeycode/config.py` | YAML 配置与默认值 | 功能开关和运行参数 |
| `tests/` | 按模块覆盖单元、集成和端到端行为 | 现有设计契约 |

## 5. 核心对象关系

```text
ChatSession
  保存 ChatMessage[]

AgentRunner
  持有 ChatProvider
  持有 ToolExecutor
  持有 ContextManager / PromptBuilder
  每轮产出 AgentEvent

ToolExecutor
  持有 ToolRegistry
  持有 PermissionManager / workspace context
  返回 ToolResult

ToolRegistry
  name -> Tool 实例

Provider
  stream_chat(messages, tools, prompt_payload)
  -> StreamEvent(text / tool_call / usage / done)
```

几个重要边界：

1. Provider 只负责模型协议，不直接读写项目文件。
2. 工具实现只描述自己的参数和执行逻辑，统一由 `ToolExecutor` 负责注册、权限和错误包装。
3. Agent 不直接调用具体文件工具，而是通过调度器和执行器调用注册表中的工具。
4. 权限模式不能替代工作区边界检查；文件工具仍必须在当前工作区内运行。

## 6. 扩展点

### 新增一个内置工具

1. 在 `monkeycode/tools/` 新建 `Tool` 实现，定义名称、描述、参数 schema 和 `execute()`。
2. 在 `tools/__init__.py` 的默认注册流程中注册它。
3. 如果工具有副作用，补充 `ToolPolicy`，让权限层知道它的风险和目标。
4. 在 `tests/` 添加工具单测，并视情况补充 `test_tool_integration.py`。

### 新增模型供应商

实现 `providers/base.py` 定义的 Provider 接口，在 `providers/factory.py` 增加配置映射，并为普通回复、工具调用、流式事件和错误处理添加测试。

### 新增 Skill / Hook / MCP 能力

这些能力都通过现有注册或发现机制接入：Skill 放入 Skill 目录并遵循解析格式；Hook 写入项目 Hook 配置；MCP 写入 `monkeycode.mcp.yaml`，由 `McpToolManager` 自动发现并注册远程工具。

## 7. 推荐阅读顺序

```text
README.md
  -> cli.py
  -> tui.py（先看 run_chat_loop）
  -> agent.py（先看 AgentRunner.run_turn）
  -> messages.py / events.py / session.py
  -> tool_scheduler.py
  -> tools/base.py -> tools/registry.py -> tools/executor.py
  -> tools/files.py / commands.py / search.py
  -> permissions.py
  -> providers/base.py -> providers/openai.py / anthropic.py
  -> context.py / prompting.py
  -> skills、subagent、task、mcp、hooks、worktree
```

每读完一层，可以先运行对应测试文件；测试名称通常直接对应模块行为，是理解隐含契约最快的入口。

## 8. 常用验证入口

```text
python -m monkeycode --help       # 确认 CLI 可启动
pytest -q                         # 运行完整测试
python -m compileall -q monkeycode tests
```

默认运行时工作区是启动命令所在目录。配置通常从 `monkeycode.yaml` 或 `config.yaml` 加载；会话和本地状态位于 `.monkeycode/`。
