# SuperBirdTools UI 组件与设计规范

本文件是 SuperViewer、SuperBirdStamp 与 `app_common` 的 Qt UI 组件选型表。
行为约束以根 [AGENTS.md](../AGENTS.md) 为准；做可见 UI 前先执行
[superbird-ui-design](../.agents/skills/superbird-ui-design/SKILL.md)。参考 AIWriter Studio 的
「先定级、再查组件、缺失先登记」方法，使用本项目的 Qt 组件，不照搬网页 CSS 或浏览器断点。

## 1. 先设计再实现

1. 确认目标应用、窗口/页签、用户任务及现有状态归属。先审查同类界面和共享组件。
2. 写出简短设计清单：层级、区域、组件、默认显隐、伸缩/滚动归属、主操作位置、状态与保存语义。
   简单变更在工作说明中列清单即可；跨窗口或共享组件迁移写到 `docs/ui-design/`，与代码同提交。
3. 用布局草图或线框说明新增/重排的层级。复用已有设计且只改样式时可用当前截图标注。
   设计不默认增加审批环节；已获授权的工作可以按规范自行完成。
4. 查下表选组件。已有组件缺能力时优先扩展；确需新组件时先登记用途、接口和样式来源，再实现。
5. 实现后验证真实布局和交互，不能只检查代码或把截图漂亮等同于行为正确。

## 2. 层级与组件选型

```text
L0 应用外壳：主窗口、菜单、状态栏
└─ L1 页面：编辑器、设置页、模板管理、工具对话框
   └─ L2 工作区布局：分栏、导航与页面、滚动区、常驻操作区
      └─ L3 区域容器：折叠分组 / 无标题平铺容器 / 浮动窗口
         └─ L4 内容块：表单 / 列表表格 / 预览 / 说明
            └─ L5 元件：字段 / 复选开关 / 动作 / 状态反馈
```

| 层级 / 用途 | 组件与来源 | 规范 |
| --- | --- | --- |
| L0 外壳 | 现有 `QMainWindow`、应用菜单/状态栏 | 不因分组重排改启动、后台线程和关闭行为 |
| L1 用户选项 | [`SettingsDialog / SettingsPage`](../app_common/settings_dialog.py) | 用 `add_page` 注册页面；确认/取消固定在滚动区外 |
| L2 左侧页面导航 | [`SidebarTabWidget`](../app_common/sidebar_tabs.py) | 复用统一的左侧图标/文字页签 |
| L2 可调分栏 | `QSplitter`；需折叠整块侧栏时用 [`TriangleToggleSplitter`](../app_common/triangle_toggle_splitter.py) | 分栏折叠与表单分组折叠是不同职责；不以分组替代整幅画布/目录树 |
| L2 滚动与操作区 | `QScrollArea` / `SettingsPage`；BirdStamp [`ExportActionBar`](../SuperBirdStamp/birdstamp/gui/editor_compact_panels.py) | 单一主滚动归属；BirdStamp 视频参数按当前宽度所需高度完整展开，上方主滚动区让出空间；内部 `_CurrentExportActions` 只取当前按钮页高度，隐藏页不占空间；图片/GIF/去抖动参数保持紧凑高度，超出窗口的极长重复播放列表保留滚动兜底，动作、取消与进度始终可达 |
| L3 有标题分组 | [`CollapsibleSection`](../app_common/collapsible_section.py) | 替代应用自建 `QGroupBox`；内容放 `body` 或 `set_content_widget`；默认展开 |
| L3 无标题布局容器 | `QWidget` + 布局 | 不为了边框再套一层折叠组，不给每个字段单独建组 |
| L3 浮窗 | `QDialog`；已有模板/叠加窗口 | 模态与非模态按用户任务决定，复用已有保存/关闭流程 |
| L4 表单 | `QFormLayout` / `QGridLayout` | 同级字段共享标签/控件列；窄宽度换行或滚动，不裁掉编辑控件 |
| L4 照片、元数据、标签列表 | 现有共享浏览器、模型与委托 | 分组调整不重建模型，不触发缩略图/EXIF 读取 |
| L4/L5 Viewer 目录操作与文件拖放 | [`ViewerDirectoryBrowser / DirectoryDropTree / DragFileTableView / DragThumbnailView`](../SuperViewer/superviewer/file_drag_views.py)、原生 `QInputDialog`；[`DirectoryFileController`](../SuperViewer/superviewer/directory_file_controller.py) | 目录右键“新建子目录…”只创建输入名称的一级目录，增量插入并保留当前浏览/展开；列表、缩略图多选拖到目录，默认移动，实体 Ctrl 复制（macOS 使用 Qt MetaModifier）；悬停目录按 palette Highlight 描边，600 ms 展开，边缘滚动，离开/完成清除；系统拥有操作进度/冲突/取消，应用状态栏反馈结果。共享视图工厂默认不启用拖放，BirdStamp 保持原行为；见[设计与覆盖](ui-design/viewer-directory-file-operations.md) |
| L5 待剪切反馈 | 共享 `FileCutState`、`ThumbnailItemDelegate` / `CutFileNameDelegate`；Viewer 通过 `show_cut_state` 启用 | 剪贴板是唯一状态源；缩略图图片淡化并显示“待剪切”，列表文件名加提示及斜体，保留选中高亮、元数据、尺寸和滚动。复制/清空剪贴板或成功粘贴恢复；失败保留。只重绘视口，绘制不读文件、不重建模型；共享默认关闭 |
| L4 识鸟批量结果 | Viewer [`BirdIDResultsTable`](../SuperViewer/superviewer/bird_identification_table.py) / [`PerBirdResultsTable`](../SuperViewer/superviewer/per_bird_results_table.py) | 一个批次一个窗口；候选/逐只记录由模型保存，按照片分组，可见缩略图与滚动由表格统一调度；逐只汇总隐藏整图采纳动作 |
| L4 图片/视频预览 | 已有 `PreviewCanvas` / `PreviewWithStatusBar` | 不改像素来源、坐标、缩放/平移和 A/B 语义；模板原有预览分组可折叠 |
| L1/L4 BirdStamp 拼图 | [`CollageEditor`](../SuperBirdStamp/birdstamp/gui/editor_collage.py) + `CollapsibleSection` / `ColorEditor` / `PreviewWithStatusBar` / `OverlayEditorDock` | 处理设置中位于去抖动之后；原生比例/分辨率组合框与联动宽高框定义最终画布（默认 3:4、1080 × 1440），单格自动计算；自定义宫格显示原生横向列数/纵向行数框，切回预设隐藏但保留数值；左侧参数和素材顺序由主滚动区容纳，右侧独立画布复用 OverlayPanel / OverlaySession，导出/取消/进度固定在 ExportActionBar。切页和折叠保留内容，拼图参数与素材、叠加层随工作区保存，后台单 worker 按版本接收并等真实 finished 交接；见[设计说明](ui-design/birdstamp-collage.md) |
| L5 Viewer 预览状态与拍摄信息 | [`PreviewPanel`](../SuperViewer/superviewer/preview_panel.py) 原有底部状态 `QLabel`；格式化 [`preview_photo_info.py`](../SuperViewer/superviewer/preview_photo_info.py) | 分辨率/缩放之后显示快门、光圈、ISO、焦距、镜头和机器型号，A/B 各侧独立；窄宽度自动换行，使用原生字体/共享主题色，缺失值显示“—”；只读源照片的浏览器/报告缓存，未选择或视频不显示拍摄信息，无新增显示栏、滚动、保存或 EXIF 读取 |
| L5 输入字段 | 原生编辑器、项目 `ColorEditor` / `PercentEditor` 等 | 复用范围、校验和信号；UI 不持有新的业务副本 |
| L4/L5 Viewer 过滤下拉栏 | [`FilterDropdown / FilterFlowLayout`](../SuperViewer/superviewer/filter_controls.py)、共享 `FilterableComboBox`、已有 `add_filterable_tag_actions` | 镜头、稀有等级、标签并列，窄宽度按控件换行；下拉显示已选数量，菜单可连续多选、键盘切换及清除。标签搜索/分组与完全匹配保留，稀有等级复用共享分档和配置名称；状态由文件列表持有，关闭菜单不清空，不新增保存或读取 |
| L5 Viewer 颜色选择 | [`rarity_badge.ColorButton`](../SuperViewer/superviewer/rarity_badge.py) | 颜色色块、色值及原生 QColorDialog；徽章和鸟名悬停框共用，修改设置草稿，确认设置后持久化 |
| L4 徽章配置表单 | Viewer [`MetadataBadgesForm`](../SuperViewer/superviewer/rarity_badge.py) | 显示文本输入框旁提供“编辑”按钮，使用原生 `QInputDialog`；应用更新草稿及预览，设置页确定后统一保存，取消不写配置 |
| L5 面板布尔选项 | `QCheckBox` | 启用/禁用与展开/收起分开；折叠不取消功能 |
| L5 工具栏显示开关 | [`ToggleToolButton`](../app_common/toggle_button.py) | 蓝底白字选中态；与根规范一致。折叠标题是 disclosure，不使用蓝色开关样式 |
| L5 动作 | `QPushButton` / `QToolButton`、已有菜单动作 | 主次动作位置一致，保留忙碌、取消及键盘行为 |
| L4/L5 Viewer 视频回放 | [`VideoPlayerView`](../SuperViewer/superviewer/video_preview.py)、`FilmstripSlider` / `WaveformSlider`、原生 `QPushButton` | 预览伸缩、两条时间轴满行；常驻单行控制：左侧上一帧/播放/下一帧/重播，中间弹性空白，右侧倍速/静音/音量；时间读数放在时间轴上方状态行右侧。视频区域空格切换播放、左右键暂停并逐帧，Home/End 定位首尾帧，时间轴点击/拖动及首尾定位均保持播放状态，首次定位显示目标帧并暂停；音量和倍速保留原生键盘操作。状态由每侧播放器拥有，无新增持久化或滚动区 |
| L5 反馈 | `QLabel` / `QProgressBar`、现有状态组件 | 反馈靠近所属操作；不能因折叠把唯一取消入口藏掉 |
| L1 Viewer 视频逐帧导出 | 复用 [`BirdIDProgressDialog`](../SuperViewer/superviewer/bird_identification_controller.py)，由 [`VideoFrameExportController`](../SuperViewer/superviewer/video_frame_export_controller.py) 驱动 | 右键动作 → 按用户选项决定目录（询问模式才打开选择框）→ 当前视频/帧数、忙碌进度、可滚动结果、固定停止/关闭按钮；原生主题和可缩放布局，Esc/关闭取消，完成 PNG 保留，任务不绑定当前选择 |
| L1 Viewer 图片 PNG 导出 | 复用 `QFileDialog` / [`BirdIDProgressDialog`](../SuperViewer/superviewer/bird_identification_controller.py)，由 [`ImagePngExportController`](../SuperViewer/superviewer/image_png_export_controller.py) 驱动 | 列表/缩略图一级右键“导出为 PNG…” → 选择输出目录 → 当前文件、完成张数、可滚动结果、固定停止/关闭按钮；原生主题，最小 600×380，可伸缩结果区，Esc/关闭取消；后台任务持有选择快照，目录切换不影响任务；不新增持久化选项 |
| L1/L4 Viewer 视频处理选项 | `SettingsPage` + `CollapsibleSection`（视频帧提取）+ [`VideoFrameOptionsForm`](../SuperViewer/superviewer/video_frame_options_form.py) | 纵向排列目录类型、固定根目录/浏览、目录名后缀、示例与说明；分组默认展开，固定根目录仅 fixed 模式启用；外层页面统一滚动，确认/取消固定，折叠和模式切换保留草稿，配置在确认后保存 |

## 3. 折叠组 API 与样式来源

```python
from app_common.collapsible_section import CollapsibleSection

group = CollapsibleSection("参数")
form = QFormLayout(group.body)  # 不使用 QFormLayout(group)
form.setContentsMargins(0, 0, 0, 0)
form.addRow("名称", editor)
layout.addWidget(group)
# 或 group.set_content_widget(existing_widget)，复用原控件和信号。
group.set_expanded(False)
```

- `title()` / `setTitle()` 管理标题；`is_expanded()` / `set_expanded()` 管理显隐；`toggled(bool)` 仅报告折叠变化。
- `group.layout()` 是标题与正文的容器布局；正文修改用 `group.body.layout()`。不要覆盖、重设外层布局。
- 小实心三角、高 DPI 绘制、标题点击/空格、键盘焦点、单层外框及连续背景由共享组件负责。
  标题/正文之间不另画分割线，不铺不同标题底色，不复制组件 QSS 到窗口。
- 颜色取 Qt palette，跟随深浅主题；不使用固定黑/白底。组件独立使用也必须完整，不能依赖 BirdStamp 主窗口样式。
- 默认展开；已有高级参数和长说明可默认收起。折叠状态默认只在当前窗口生效，既有持久化字段保留。
  不因折叠写选项、启动任务、销毁正文、清空输入、关掉功能或重置工作区。
- `setEnabled` 仍是 Qt 控件启用状态；需要「启用这一组功能」时单独放 `QCheckBox`，不把折叠信号接到业务 enabled。
- 不做 `QGroupBox = CollapsibleSection` 别名替换；显式迁移布局、类型引用、伸缩策略及测试。
  Qt 兼容层可以保留原类型导出；第三方 UI 不在本项目强制替换。
- [`editor_collapsible.py`](../SuperBirdStamp/birdstamp/gui/editor_collapsible.py) 仅保留兼容导出、BirdStamp 页签尺寸策略和布局诊断，不能再出现第二份折叠实现。
- `app_common/ui_style/styles.py` 是历史主题资源，不是两款应用的新组件目录；新的通用组件在本表登记，样式由组件自身或共享主题模块提供。

## 4. 布局、状态与验证

- 同级块使用同一种容器、边距和标题层级；嵌套只表达真实的父子关系，避免无意义的多层边框。
- 横向并列字段共用表格/表单列，纵向标签、输入、说明和动作各自对齐；正文留白由容器和内容布局共同检查，避免双重内边距。
- 参数页顶部对齐，多余空间留在底部或预览区，不把表单行拉散。收起有 stretch 的组也必须释放实际高度。
- 收起再展开、重排、切模式和切主题后保留参数、选择、启用状态、撤销历史与原信号；隐藏不等于任务暂停。
- 验证最小支持窗口、常用尺寸、放大字体、深浅主题、键盘操作及重复展开/收起。
  实际渲染截图检查三角、文字、边界、留白与滚动可达性；测量控件几何，不只断言 `isVisible()`。
- GUI 验证按根规范隔离用户配置并离屏运行，检查运行状态文件未改动。macOS 上的 Qt Windows 样式只能检查样式差异，不能声称已通过 Windows 64 位实机验证。
- 共享组件回归：[test_collapsible_section.py](../app_common/tests/test_collapsible_section.py)；两应用设置页：[test_shared_settings_dialog.py](../tests/test_shared_settings_dialog.py)；BirdStamp 分组布局：[test_editor_groupbox_titles.py](../SuperBirdStamp/tests/test_editor_groupbox_titles.py)。

## 5. 当前覆盖与审查边界

迁移清单及本轮设计见 [统一折叠组设计](ui-design/unified-groups.md)。应用代码不再创建原生 `QGroupBox`。
Viewer 的长选项表单使用语义分组；照片列表、目录导航、预览视口及 Tab 页本身不机械套组。
新增组件或适用范围改变时，必须与实现一起更新本表和对应架构入口。
