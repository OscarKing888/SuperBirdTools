---
name: superbird-ui-design
description: Design, implement, or review visible Qt UI in SuperBirdTools (SuperViewer, SuperBirdStamp, and app_common). Classify the UI by hierarchy, select registered shared components, plan layout and state behavior before coding, and verify rendered geometry and interaction. Use for UI additions, layout changes, visual consistency fixes, or component migrations in this project; not for unrelated apps or non-UI algorithm work.
---

# SuperBird UI Design

面向 SuperBirdTools 的「先设计、后实现」工作流。以当前 checkout 根 `AGENTS.md` 和
`docs/ui-style.md` 为准；在 worktree 中读该 worktree 的文件，不能从个人 skill 路径推导另一个 checkout。
此 skill 只定义流程，组件与样式的唯一选型表保存在项目文档。

## 实现前

- 先读 `docs/ui-style.md` 和目标应用架构入口，定位已有页面、控件和样式来源。
- 明确宿主应用/窗口、用户要完成的操作，以及哪些状态属于业务模型、哪些只是视图显隐。
- 在工作说明中写简短选型清单：页面/布局/容器/内容块/元件 → 共享组件 → 默认状态、滚动和伸缩策略 → 原状态如何保留。
  新增或重排区域配一张简短布局草图；跨页面或共享组件迁移将设计与覆盖清单保存在 `docs/ui-design/`。
- 先查工作区已有专用组件，再查共享组件。新增类型或能力先登记到 `docs/ui-style.md`，并与代码同提交。
  不照搬其他产品的 CSS、色值、布局断点，不以重复 QSS 修补共享组件缺口。

## 实现时

- 有标题分组使用 `app_common.collapsible_section.CollapsibleSection`，正文布局挂 `group.body`。
  分组控制显隐，业务启用状态使用独立复选框；收起不得清空输入或触发保存、解码、任务启停。
- 表单、列表、预览和操作的归属先按文档设计；不为每个字段或整页机械加一层折叠组。
- 共享组件必须独立于宿主窗口样式；保留 Qt5/Qt6 兼容、UTF-8、macOS/Windows 路径与生命周期约束。
- 不擅自增加等待审批。需求已授权且设计可按规范确定时，继续实现；只有影响范围或行为不清楚时才问必要问题。

## 验证与交付

- 按根规则隔离 GUI 配置、离屏验证；至少检查小窗口、放大字体、深浅色、键盘操作、重复折叠和参数保留。
- 实际渲染并检查截图与控件几何，验证标题/正文连续、同级对齐、内容不裁切、固定动作可达。
  不以样式字符串断言代替视觉和交互验证，不把 Qt Windows 样式当作 Windows 实机结果。
- 运行受影响共享组件与应用回归。迁移 API 时更新使用方、兼容导出、文档和测试；报告未覆盖平台或既有失败。
- 交付说明实现的区域、选择的共享组件、验证结果与实质限制。遵循项目的分支、子模块提交和清理流程。
