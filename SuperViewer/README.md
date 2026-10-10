# Super Viewer - 图片 EXIF 查看器/编辑器


### 珍禽入册

选中照片，在右键菜单顶部点击带金色鸟图标的 **珍禽入册…**，或使用“文件 → 珍禽入册”。在“设置 → 用户选项 → 珍禽入册”中配置归档根目录；入册窗口也可修改并保存。

- 按鸟名和格式建立子目录：RAW/HIF/HEIF/HEIC → `RAW`，PSD → `PSD`，PNG/JPG/JPEG → `Export`；其他受支持图片仍放在鸟名目录。例如 `鸟类名册/白鹭/RAW/20261005_083015_DSC01234.ARW`。
- 默认移动，可改为复制；拍摄时间前缀可关闭。重名自动追加 `_002`、`_003`，不覆盖已有文件。
- XMP 随照片入册，同选 RAW/JPEG/PSD 保持相同 stem，每个格式目录各有一份 XMP；任一目标目录重名时整组加相同序号。只选部分同名照片时，原目录保留其余照片需要的 XMP。
- RAW/HIF/HEIF/HEIC 旁存在同名 `.acr`（大小写不限）时，一起移入 `RAW`，日期前缀和冲突编号与照片一致，ACR 内容保持原样。复制入册或仍有未选中的同名照片时保留源 ACR；照片、XMP、ACR 任一转移失败，整组回滚。
- 旧版平铺在鸟名目录的照片可重新选择入册，自动放入格式子目录；已有拍摄时间前缀不会重复添加。
- 无鸟名跳过，缺拍摄日期标记“日期未知”；结果窗口显示失败原因和恢复文件位置，支持停止批次。
- 只保存在 report.db 的信息会补入 XMP，报告数据库保持只读。

命令行（macOS；Windows 改用 `.venv\Scripts\python.exe`）：

```bash
.venv/bin/python3 -m SuperViewer.superviewer.bird_archive --directory "/照片/鸟类名册" --mode copy "/照片/DSC01234.ARW"
```
适合通过`慧眼选鸟(4.1.0及后继版本)`处理后，不需要LRC、PS流程处理照片的情况

## 开发定位

完整模块关系、目录/预览/元数据数据流和功能修改入口见 [架构与代码定位](docs/ARCHITECTURE.md)。修改前同时查看仓库根目录的 [AGENTS 行为约束](../AGENTS.md)。下方保留简要模块概览。

## 批量 RGB 降噪

照片单选/多选及目录右键可启动 NAFNet 本地降噪。RAW 完整显影后处理，默认另存 16 位 TIFF 和 XMP，不修改原文件。在“设置 → 用户选项 → 降噪”中可配置输出目录、JPEG/TIFF、强度、设备及并发。模型随完整安装包提供，运行无需联网。使用限制、CLI 与模型预下载见 [批量 RGB 降噪说明](../docs/image_denoise.md)。

每个预览视口可点击来源按钮循环切换“默认预览 → 显示 RAW → 显示降噪”（非 RAW 跳过 RAW），A/B 两侧独立。降噪模式显示已有成片，未找到时保留原图并提示。“显示鸟体”在后台识别主体鸟并将鸟框缓存到 XMP；长按方向键期间只显示缓存框，松键后再处理最终照片。具体缓存、模型及坐标规则见 [预览架构](docs/ARCHITECTURE.md#显示鸟体)。

## 鸟清晰度检测

目录树右键「鸟清晰度检测」子菜单，或文件右键「检测鸟清晰度（N 张）」：先识别每只鸟，再只用鸟自己的像素（看得到眼时只用眼周头部区域）计算清晰度，写入 XMP，并在文件列表的「鸟清晰」列、缩略图和信息页显示。右键「查看清晰度计算过程…」可以逐步查看一张照片的计算过程。算法见 [鸟清晰度检测](../docs/bird_sharpness.md)。

鸟体分割模型和鸟眼模型使用 [SuperPicky（慧眼选鸟）](https://github.com/jamesphotography/SuperPicky) 的权重，运行时从已安装的 SuperPicky 中读取，不随本程序打包；没有它们时退回到 `yolo11n.pt` 鸟框，准确度明显下降。模型清单与查找顺序见 [根目录 README](../README.md#鸟识别模型与-superpicky)。

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
  * 右键菜单可复制粘贴鸟名，同时携带中英文名、拼音、稀有度、保护等级和拍摄地点，支持多选粘贴；源照片缺失的关联信息清空目标旧值。用户编辑写入照片同目录、同名的 XMP sidecar
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

列表和缩略图模式都支持区间选择：在起始图上按 `[` 标记起点，移动到结束图后按 `]`，即连续选中两者之间（含两端）的所有图片；中文输入法下的 `【` / `】` 同样有效。起点会保留，可在其它图上再按 `]` 调整终点；底部状态栏显示“区间起点”序号，切换目录后清除。

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

图片信息先展示已缓存的字段，再由后台补齐，避免界面等待上一目录的批量 ExifTool 读取。超过 HEIF 同步阈值（默认 4 MP，`SuperViewer_SYNC_FULL_PREVIEW_HEIF_MAX_MP`）的 HIF 优先复用当前尺寸档位的缓存；没有缓存时显示加载提示，由后台解码完整预览。RAW 同样先显示档位缓存或加载提示，再由后台换成相机内嵌的高清预览。小图和按住方向键的预览策略保持原有约定。

## 视频预览与播放

在列表或缩略图中右键视频，选择 **提取全部帧为 PNG…**。默认在每个视频所在目录创建 `<视频名>_Frames` 文件夹（文件名不含扩展名），重名自动编号。在 **设置 → 用户选项 → 视频处理** 中，可改为指定固定根目录或每次弹出目录选择框；三种方式均按“视频文件名 + 目录名后缀”创建独立文件夹，后缀默认 `_Frames`，可修改或留空。多选来自不同目录的视频时，默认分别输出到各自所在目录；固定/选择模式输出到同一根目录。

图片按 `frame_00000001.png` 起顺序保存，保持原分辨率，包含完整视频的每一帧（变帧率视频也不跳帧或补帧）。进度窗口显示已提取帧数及结果路径，可随时停止并保留已完成图片。源码命令行可使用根 `.venv` 执行 `python -m SuperViewer.superviewer.video_frame_export <视频...> [--output <输出根目录>] [--suffix _Frames]`；省略 `--output` 时保存到各视频所在目录，CLI 参数独立于 GUI 设置。

照片和视频可在同一目录中浏览，支持 MP4、MOV、MKV、AVI、WebM、M4V、MPEG、TS/MTS/M2TS 等常见容器；具体编码能否播放取决于当前 Qt Multimedia 后端。

- 缩略图显示视频封面及 `▶ 时长` 角标；列表追加时长、分辨率、帧率与编码列，可排序。
- 选中视频先显示封面，点击“播放”启动音视频播放。下方提供暂停、重播、进度拖动、0.25–2 倍速、音量和静音。
- 右侧自动切换为视频信息，显示时长、分辨率、帧率、编码、码率、音轨、文件大小与修改时间。切回照片恢复图片信息和原有预览工具。
- 切换文件/目录或关闭窗口会停止播放；长按方向键只快速浏览封面，松开后加载最终文件的信息。

使用仓库共享环境安装 `SuperViewer/requirements.txt` 中的依赖。新增的 `imageio-ffmpeg` 自带平台 FFmpeg，封面与信息读取不要求额外安装系统 ffprobe；也可通过 `SUPERVIEWER_FFMPEG` 指定 FFmpeg 路径。视频功能不会修改原视频文件。构建脚本优先仓库根 `.venv`；打包会检查并显式收集 FFmpeg，缺少依赖时停止构建。可运行应用入口 `--check-video <视频路径> --output <诊断.json>` 验证实际运行环境的封面与信息读取。
