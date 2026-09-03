# Issue tracker: Local Markdown

本仓库的 issues 和 PRD 作为 Markdown 文件存放在 `.scratch/` 中。

## Conventions

- 每个 feature 使用一个目录：`.scratch/<feature-slug>/`
- PRD 路径：`.scratch/<feature-slug>/PRD.md`
- Implementation issues 路径：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，编号从 `01` 开始
- 每个 issue 在文件顶部附近使用 `Status:` 记录 triage 状态
- Comments 和对话历史追加到文件底部的 `## Comments`

## Publishing

当 skill 要求“publish to the issue tracker”时，在对应 `.scratch/<feature-slug>/` 中创建文件和必要目录。

当 skill 要求“fetch the relevant ticket”时，读取用户给出的路径或 issue 编号对应的 Markdown 文件。
