# Domain Docs

本仓库采用 single-context 领域文档布局。

## Reading order

Engineering skills 探索代码前应按需读取：

1. 根目录 `CONTEXT.md`
2. `docs/adr/` 中与当前区域相关的 ADR

如果这些文件或目录不存在，静默继续，不要仅为补齐结构而创建空文件。

## Vocabulary

命名 issue、测试、重构方案和诊断假设时，使用 `CONTEXT.md` glossary 中定义的术语，避免使用 glossary 明确排除的同义词。

如果所需概念尚未出现在 glossary 中，应先判断它是新领域概念还是不必要的新术语。

## ADR conflicts

方案与现有 ADR 冲突时，必须明确指出冲突的 ADR 和重新讨论它的理由，不得静默覆盖既有决策。
