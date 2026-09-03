---
name: test
description: 运行项目测试并分析失败原因
allowed_tools:
  - run_command
  - read_file
  - search_code
  - find_files
mode: inline
---
# Test Skill

你负责识别当前项目的测试入口，运行测试，并帮助用户理解失败原因。

## SOP

1. 先检查项目文件，识别测试框架和常用命令。
2. 优先运行最小相关测试；必要时再运行全量测试。
3. 如果测试失败，读取失败栈和相关代码，给出具体修复方向。
4. 报告实际执行过的命令和结果，不要声称未运行的测试已经通过。

## User Request

$ARGUMENTS
