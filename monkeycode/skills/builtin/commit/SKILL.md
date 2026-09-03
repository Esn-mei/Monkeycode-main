---
name: commit
description: 分析 git diff 并生成规范的 commit
allowed_tools:
  - run_command
  - read_file
  - search_code
mode: inline
---
# Commit Skill

你负责帮助用户完成一次谨慎的 Git 提交。

## SOP

1. 先用 `run_command` 查看 `git status --short`。
2. 如有必要，用 `run_command` 查看 `git diff` 与暂存区差异。
3. 只提交与用户请求相关的修改；不要把无关文件混入提交。
4. 生成简洁、准确的 commit message。
5. 在真正执行 `git add` / `git commit` 前，说明将要提交的文件范围。

## User Request

$ARGUMENTS
