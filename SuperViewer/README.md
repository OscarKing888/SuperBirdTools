# Super Viewer - 图片 EXIF 查看器/编辑器
适合通过`慧眼选鸟(4.1.0及后继版本)`处理后，不需要LRC、PS流程处理照片的情况

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
  * 选中`慧眼选鸟`处理过的目录会自动读取数据库并可过滤显示
  * 支持从`慧眼选鸟`发送文件到本应用
  * 支持发送文件到`Super Birdstamp`切图工具
    * https://github.com/OscarKing888/SuperBirdStamp.git
  * 支持星级、文件名、精选（奖杯）过滤
  * 文件列表可排序
  * 右键菜单可复制粘贴鸟名，并写入数据库
  * 实际文件路径与数据库不一致的会自动修正数据库中的文件路径
  * 支持自定义显示顺序，支持自定义标签名称。
  * 支持常见的照片格式（如 各种RAW/JPEG/TIF/HEIC/HEIF）。    
  * `文件信息-标题` 与 `文件信息-描述` 支持直接双击编辑并写回元数据。
  * 额外增加了超焦距计算，公式为 H = f^2 / (N * c) + f，其中 f=焦距(mm), N=光圈值, c=弥散圆(mm)。

* 主界面
[![主界面](./manual/images/MainCH.png)](./manual/images/MainCH.png)
[![主界面](./manual/images/MainEng.png)](./manual/images/MainEng.png)
* 自定义显示顺序
[![自定义显示顺序](./manual/images/CustomEdit.png)](./manual/images/CustomEdit.png)
* 自定义隐藏标签
[![自定义隐藏标签](./manual/images/CustomEditHiddenTag.png)](./manual/images/CustomEditHiddenTag.png)

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
