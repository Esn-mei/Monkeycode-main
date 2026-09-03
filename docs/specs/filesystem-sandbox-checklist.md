# 文件系统沙箱兜底验收清单

## 可观察行为

- [ ] 项目内相对路径的读取、编辑、创建和覆盖均成功，结果路径保持项目相对路径格式。
- [ ] 项目内合法绝对路径的读取、编辑、创建和覆盖均成功。
- [ ] 读取或编辑 `../secret.txt` 返回 `path_outside_workspace`，且项目外文件内容未出现在工具输出中。
- [ ] 向项目外绝对路径创建或覆盖文件返回 `path_outside_workspace`，且项目外目标不存在或内容未改变。
- [ ] 读取或编辑指向项目外文件的符号链接返回 `path_outside_workspace`。
- [ ] 通过指向项目外目录的符号链接创建或覆盖文件返回 `path_outside_workspace`，且项目外目标不存在或内容未改变。
- [ ] 项目内符号链接解析后仍位于项目根目录内时，按目标文件的正常语义执行。

## 权限与搜索集成

- [ ] `PermissionMode.ALLOW` 不能放行任何项目外读取、编辑或写入路径。
- [ ] 会话允许规则与持久化允许规则不能放行任何项目外读取、编辑或写入路径。
- [ ] 模拟用户确认允许后，项目外路径仍返回 `path_outside_workspace`。
- [ ] 文件发现和代码搜索不返回解析后位于项目外的符号链接文件的路径或内容。

## worktree 端到端场景

- [ ] `isolation: worktree` 子 Agent 可在自己的 worktree 内读写合法项目文件。
- [ ] worktree 子 Agent 不能通过父项目路径、项目外绝对路径或项目外符号链接读取、编辑或写入。
- [ ] 非 Git 工作区的正常文件工具行为不因边界加固而回退。

## 错误与兼容性

- [ ] 项目外路径均返回现有结构化失败类型 `path_outside_workspace`，不改为未处理异常。
- [ ] 访问目录仍保留既有 `is_directory` 错误语义。
- [ ] 不存在的项目内读取/编辑仍保留既有 `file_not_found` 错误语义。
- [ ] README 明确说明该机制限制内置文件工具，且不等同 Docker、AppContainer 或其他完整 OS 级沙箱。

## 必须执行的验证

- [ ] `pytest -q tests/test_permissions.py` 通过。
- [ ] 工作区守卫、文件工具、搜索与 worktree 相关测试通过。
- [ ] `pytest -q` 通过。
- [ ] `python -m compileall -q monkeycode tests` 通过。
- [ ] `python -m monkeycode --help` 退出码为 0。
- [ ] `git diff --check` 无输出且退出码为 0。

## 通过标准

全部复选项均以自动化测试输出、命令退出码或 README 的可核对内容证明；在不具备创建符号链接权限的平台上，符号链接测试必须显式跳过并保留跳过原因，其他项目外路径和全部非符号链接检查仍必须通过。
