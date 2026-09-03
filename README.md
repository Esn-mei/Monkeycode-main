<<<<<<< HEAD
# Monkeycode-main
=======
# MonkeyCode——终端 AI 编程助手

MonkeyCode 是一个面向本地开发工作流的终端 AI 编程助手。它支持对话式任务处理、文件读写、代码搜索、命令执行、权限确认和多步工具调用，可通过 OpenAI 兼容或 Anthropic API 接入模型。

想快速理解代码结构和一次请求的完整调用链，请先阅读 [项目架构导览](docs/architecture.md)。

## ✨ 功能特点

- **Agent Loop**：模型可连续执行 `model → tool → model`，直到任务完成或需要人工决定。
- **终端对话界面**：提供可滚动对话记录、固定输入栏、流式输出与权限状态栏。
- **代码工具**：内置文件读取、写入、编辑、文件查找、文本搜索和命令执行。
- **权限确认**：写入与命令默认需要确认；危险命令、路径越界和显式拒绝规则不能绕过。
- **任务协作**：支持子 Agent、后台任务和 Git worktree 隔离。
- **扩展能力**：支持本地 Skills、MCP 工具和 Hooks 自动化。
- **会话管理**：支持会话恢复、长期记忆与上下文自动压缩。

## 🛠️ 技术栈

### 运行时

- Python 3.10+
- Prompt Toolkit
- HTTPX
- PyYAML

### 模型协议

- OpenAI Chat Completions 兼容协议
- Anthropic Messages 协议
- SSE 流式响应与工具调用

### 本地能力

- 工作区路径沙箱与危险命令拦截
- Git worktree 隔离
- Skills、MCP、Hooks 与子 Agent

## 🧱 运行架构

```text
终端用户
   │
   ▼
Prompt Toolkit TUI
   │
   ▼
Agent Runner ──────── 权限系统
   │                    │
   ├── 文件 / 搜索 / 命令工具
   ├── 子 Agent / 后台任务
   ├── Skills / MCP / Hooks
   │
   ▼
OpenAI 兼容 API 或 Anthropic API
```

Agent Runner 根据模型返回的工具调用操作当前工作区；所有文件路径受工作区边界限制，写入和命令则经过权限判断。

## 📁 项目结构

```text
.
├── monkeycode/
│   ├── cli.py              # 命令行入口
│   ├── tui.py              # 终端交互界面
│   ├── agent.py            # Agent Loop
│   ├── permissions.py      # 权限判断与规则
│   ├── tools/              # 文件、搜索和命令工具
│   ├── providers/          # OpenAI / Anthropic Provider
│   ├── subagent/           # 子 Agent 定义与执行
│   ├── mcp/                # MCP 客户端
│   ├── hooks/              # Hooks 引擎
│   └── skills/             # 内置 Skill
├── tests/                  # 测试
├── configs/                # 示例和开发配置
├── config.yaml             # 本地运行配置示例
├── monkeycode.mcp.yaml     # MCP 配置
└── pyproject.toml
```

## 🚀 获取源码后开始运行

以下操作在项目根目录执行。

### 1. 检查运行环境

需要 Python 3.10 或更高版本。使用 Git worktree 隔离时，还需要 Git。

```powershell
python --version
git --version
```

### 2. 安装依赖

开发与测试环境：

```powershell
python -m pip install -e ".[dev]"
```

只安装运行依赖：

```powershell
python -m pip install -e .
```

### 3. 配置模型服务

在项目根目录创建或编辑 `config.yaml`：

```yaml
protocol: openai
model: gpt-4o-mini
base_url: https://api.openai.com/v1
api_key: ${MONKEYCODE_API_KEY}

options:
  temperature: 0.7
```

再创建同目录 `.env`：

```dotenv
MONKEYCODE_API_KEY=your-real-api-key
```

| 配置项 | 说明 |
| --- | --- |
| `protocol` | `openai` 或 `anthropic` |
| `model` | 服务商支持的模型名称 |
| `base_url` | 服务接口地址 |
| `api_key` | API Key 或引用的环境变量 |

同目录 `.env` 中的值会覆盖终端继承的同名环境变量，避免旧 Key 覆盖项目配置。不要把真实 Key 提交到 Git。

使用 Anthropic 时，将配置改为：

```yaml
protocol: anthropic
model: claude-sonnet-4-5
base_url: https://api.anthropic.com
api_key: ${MONKEYCODE_API_KEY}

options:
  max_tokens: 2048
```

### 4. 启动 MonkeyCode

```powershell
python -m monkeycode --config .\config.yaml
```

不传 `--config` 时，程序会优先读取当前目录的 `monkeycode.yaml`，其次读取 `config.yaml`。

## 💬 使用示例

启动后直接输入任务：

```text
检查登录接口为什么返回 500，修复后运行相关测试
```

也可以先规划再执行：

```text
/plan
为当前项目增加登录失败重试，并给出实施计划

/do
```

## ⌨️ 常用命令

| 命令 | 用途 |
| --- | --- |
| `/help` | 显示可用命令 |
| `/plan` | 进入只读计划模式 |
| `/do` | 执行已确认计划 |
| `/clear` | 清空当前会话并新建会话 |
| `/resume` | 恢复历史会话 |
| `/status` | 显示运行状态 |
| `/permission` | 显示权限档位 |
| `/skill` | 列出已加载的 Skill |
| `/quit` | 退出程序 |

在 TUI 中按 `Shift+Tab` 可循环切换权限档位。

## 🔐 权限与安全

| 档位 | 行为 |
| --- | --- |
| `Default permissions` | 读取默认允许；写入和命令通常需要确认。 |
| `Auto-review` | 对潜在副作用操作保持人工确认。 |
| `Full access` | 普通工具默认允许。 |

危险命令黑名单、工作区路径沙箱和显式 `deny` 规则始终生效，即使处于 `Full access` 也不能绕过。

权限规则可放在：

```text
~/.monkeycode/permissions.yaml
<workspace>/monkeycode.permissions.yaml
<workspace>/.monkeycode/permissions.local.yaml
```

确认时：`y` 仅本次允许，`s` 允许本会话，`n` 拒绝。

## 🔌 扩展配置

| 能力 | 位置 |
| --- | --- |
| 子 Agent | `<workspace>/.monkeycode/agents/` |
| 项目级 Skills | `<workspace>/.monkeycode/skills/` |
| MCP | `<workspace>/monkeycode.mcp.yaml` 或 `~/.monkeycode/mcp.yaml` |
| Hooks | `<workspace>/monkeycode.hooks.yaml`、`<workspace>/.monkeycode/hooks.local.yaml` 或 `~/.monkeycode/hooks.yaml` |
| 会话、记忆、worktree | `<workspace>/.monkeycode/` |

使用 `isolation: "worktree"` 的子 Agent 会在 `<workspace>/.monkeycode/worktrees/` 创建独立目录，未合并的改动会被保留。

## 🧪 测试

```powershell
python -m pytest -q
python -m monkeycode --help
```

## ❓ 常见问题

### 启动后提示 API Key 无效

检查 `config.yaml` 的 `base_url`、`model`、`protocol` 是否匹配服务商，并确认同目录 `.env` 内的 `MONKEYCODE_API_KEY` 有效。不要在配置中使用其他服务商的 Key。

### PowerShell 找不到 `monkeycode` 命令

直接使用模块入口：

```powershell
python -m monkeycode --config .\config.yaml
```

### 想使用隔离的子 Agent

当前工作区必须是 Git 仓库，并在子 Agent 配置中使用 `isolation: "worktree"`。隔离工作目录会创建在 `.monkeycode/worktrees/` 下。

## 当前边界

- Provider 协议仅支持 `openai` 和 `anthropic`。
- MCP 当前只接入工具，不支持 Resources、Prompts 或 Sampling。
- 文件路径沙箱不是容器或 OS 级隔离；命令及其子进程仍以当前用户权限运行。

## License

当前仓库尚未提供 License 文件。在公开发布或接收外部贡献前，应先确定许可证。
>>>>>>> 06b64af (first commit)
