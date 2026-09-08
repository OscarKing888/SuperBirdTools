# Super Viewer - 图片 EXIF 查看器/编辑器
适合通过`慧眼选鸟(4.1.0及后继版本)`处理后，不需要LRC、PS流程处理照片的情况

## 开发定位

完整模块关系、目录/预览/元数据数据流和功能修改入口见 [架构与代码定位](docs/ARCHITECTURE.md)。修改前同时查看仓库根目录的 [AGENTS 行为约束](../AGENTS.md)。下方保留简要模块概览。

## 代码结构（重构后）

- **main.py**：应用入口 `main()`、主窗口类 `MainWindow`、构图线常量与线宽图标；对脚本兼容的 re-export（`QApplication`、`RAW_EXTENSIONS`、`_load_preview_pixmap_for_canvas`、`_load_exifread_metadata_for_focus`、`_resolve_focus_calc_image_size`、`_load_focus_box_for_preview`）。脚本仍可 `import main` 使用上述符号。
- **superviewer/**：子包，包含以下模块：
  - **qt_compat.py**：PyQt5/PyQt6 统一导入与枚举别名，无业务逻辑。
  - **paths_settings.py**：程序目录、用户状态目录、last folder、cfg 读写、应用身份与窗口标题。
  - **exif_helpers.py**：EXIF 读取/解析、标签显示与优先级、报告元数据、扩展名常量（如 `RAW_EXTENSIONS`、`HEIF_EXTENSIONS`）。
  - **focus_preview_loader.py**：预览图加载、焦点框提取与 report.db 保底、`IMAGE_EXTENSIONS`。
  - **photo_focus_memory_cache_state.py** / **photo_preview_memory_entry.py**：预览期焦点缓存 dataclass。
  - **super_viewer_user_options_dialog.py**：用户选项对话框。
  - **focus_box_loader.py** / **focus_cache_preload_worker.py**：焦点加载与预加载线程。
  - **preview_panel.py**：预览区控件（内嵌 PreviewCanvas）。
  - **exif_table.py**：EXIF 表格；**exif_tag_order_dialog.py**：EXIF 显示顺序与禁止显示配置。

推荐从 `main` 或 `SuperViewer` 包导入以保持兼容；新代码可按需从 `SuperViewer.superviewer` 子模块直接导入。

* V0.1.0版本功能
  * 选中`慧眼选鸟`处理过的目录会读取 `report.db` 作为只读兼容/补全来源，并可过滤显示
  * 支持从`慧眼选鸟`发送文件到本应用
  * 支持发送文件到`Super Birdstamp`切图工具
    * https://github.com/OscarKing888/SuperBirdStamp.git
  * 支持星级、文件名、精选（奖杯）过滤
  * 文件列表可排序
  * 右键菜单可复制粘贴鸟名；鸟名及其它用户编辑写入照片同目录、同名的 XMP sidecar
  * `report.db` 始终只读；实际文件路径与数据库记录不一致时仅在运行期解析匹配，不修改数据库
  * 支持自定义显示顺序，支持自定义标签名称。
  * 支持常见的照片格式（如 各种RAW/JPEG/TIF/HEIC/HEIF）。    
  * `文件信息-标题` 与 `文件信息-描述` 支持直接双击编辑并写入 XMP sidecar，不修改 RAW/原始照片。
  * 额外增加了超焦距计算，公式为 H = f^2 / (N * c) + f，其中 f=焦距(mm), N=光圈值, c=弥散圆(mm)。

* 主界面
[![主界面](./manual/images/MainCH.png)](./manual/images/MainCH.png)
[![主界面](./manual/images/MainEng.png)](./manual/images/MainEng.png)
* 自定义显示顺序
[![自定义显示顺序](./manual/images/CustomEdit.png)](./manual/images/CustomEdit.png)
* 自定义隐藏标签
[![自定义隐藏标签](./manual/images/CustomEditHiddenTag.png)](./manual/images/CustomEditHiddenTag.png)

## 标签分组与搜索

`tags.cfg` 兼容原有每行一个标签的格式，也可用缩进组织分组。例如：

```text
鸟类
    白鹭
    苍鹭
行为
    飞行
    筑巢
```

分组只用于菜单导航，只有最末级标签会写入照片的 XMP sidecar。程序优先读取照片库 `.superpicky/tags.cfg`；没有库配置时使用 `SuperViewer/tags.cfg`。

文件列表的标签菜单支持搜索分组或标签，并可连续勾选。文件名搜索框同时匹配文件名、备注和标签；空格分开的多个关键词需要全部命中，但可以分别命中不同字段。例如 `白鹭 飞行` 可匹配同时有这两个标签的照片。评级、精选、焦点等已有过滤条件仍同时生效。

## 标签撤销与重做

顶部“编辑”工具栏提供蓝色撤销、绿色重做按钮，与“编辑”菜单的“撤销标签”和“重做标签”共用操作和启用状态。也可使用系统常规撤销/重做快捷键（Windows 为 Ctrl+Z / Ctrl+Y）。在文件名、备注或搜索框中输入时，快捷键优先撤销该输入框的文字编辑；工具栏按钮始终用于标签历史。

历史只记录实际保存成功的标签变化，最多保留 100 次操作。批量操作中原本已有的标签不会被撤销误删；部分照片写入失败时会提示失败路径，已成功部分仍可撤销。撤销或重做若部分失败，再次执行会重试未完成部分。切换标签库、重命名照片或更改可用标签集合后会清空历史；历史不跨程序重启保存。

## 界面主题

界面跟随系统深色/浅色主题，切换时更新目录、标签筛选栏和信息面板配色，保留当前照片、筛选条件及未保存输入。主题切换不会重新读取照片或 EXIF；旧版 Qt 使用应用调色板变化作为兼容回退。

# 关于作者
小红书 @追鸟奇遇记 https://xhslink.com/m/A2cowPsYj8P


# 友情链接：慧眼选鸟
* 官网：https://superpicky.app

* 小红书 @詹姆斯摄影 https://xhslink.com/m/3UWGeUJqUi0

*开源库：https://github.com/jamesphotography/SuperPicky
[![友情链接：慧眼选鸟](https://raw.githubusercontent.com/jamesphotography/SuperPicky/master/img/icon.png)](https://superpicky.app)

# License

本仓库根目录代码与文档在未另行说明时，按 `GNU Affero General Public License v3.0 (AGPL v3.0)` 发布，详见 `LICENSE`。

仓库中包含独立子模块与第三方组件时，这些内容仍以其各自上游许可证为准，不因本仓库根目录 `LICENSE` 自动变更。相关边界说明见 `THIRD_PARTY_NOTICES.md`。

## HIF 大目录切换

图片信息先展示已缓存的字段，再由后台补齐，避免界面等待上一目录的批量 ExifTool 读取。超过同步预览阈值的大 HIF 优先复用当前尺寸档位的缓存；没有缓存时显示加载提示，由后台解码完整预览。小图、RAW 和按住方向键的预览策略保持原有约定。
