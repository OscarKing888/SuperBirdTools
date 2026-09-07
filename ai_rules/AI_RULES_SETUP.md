# AI 协作规则入口

本仓库的文档分为两层，两份规则都适用于 Viewer、BirdStamp 和共享库：

- [AI_CODING_RULES.md](AI_CODING_RULES.md)：跨工具编码基线，包括编码、跨平台、数据正确性与验证方法。
- [AGENTS.md](../AGENTS.md)：项目行为与回归验证的权威约束，包括预览策略、元数据、后台任务、标签历史、工作区和导出。
- [Viewer 架构](../SuperViewer/docs/ARCHITECTURE.md)、[BirdStamp 架构](../SuperBirdStamp/docs/ARCHITECTURE.md)：实现位置、数据流和扩展入口，不覆盖上述行为约束。

## Codex

仓库根目录 `AGENTS.md` 是入口，文件开头链接到编码基线与两份架构文档。具体指令发现行为以使用的客户端为准。

## Claude

仓库根目录 [CLAUDE.md](../CLAUDE.md) 是入口，要求同时读取编码基线和 `AGENTS.md`。保持入口简短，不复制完整行为规则。

## Cursor 或其它客户端

当前检出没有 `.cursor/rules/cursor-rules.mdc` 或 `.cursorrules`；不能假定项目规则已经自动启用。使用客户端支持的项目指令设置，引用上面两份规则即可。`.cursor/skills/` 中的专项资料也不能替代项目规则入口。

## 维护方式

通用开发约束更新 `AI_CODING_RULES.md`；应用行为、共享库契约与验证要求更新 `AGENTS.md`。移动模块、修改数据流或增加功能入口时，同时更新对应架构文档的定位表。避免将临时排查命令、机器绝对路径或过期行号写成长期规则。

用户已经明确的任务范围、授权和提交要求优先适用；保持已有无关修改，按功能选择性暂存和提交。文档应记录已实现并验证的行为，不把尚未落地的建议写成现状。
