# SuperViewer Branch Merge Guide

## 特性清单（合并时可指定）

后续实际合并时可以直接按编号指定范围，例如“合并 F01/F02/F07”或“先只合并 F00 和 F04”。编号只描述迁移边界，不代表必须一次全合。

| 编号 | 特性 | 主要范围 | 关键依赖/注意点 |
| --- | --- | --- | --- |
| F00 | `app_common` 公共层切到 `res_mgr` | `.gitmodules`、`app_common` 子模块指针/checkout 策略 | 这是多数 SuperViewer 改动的前置依赖；不要只合主仓库 Python 文件。 |
| F01 | 两阶段预览加载与快速切图 | `SuperViewer/superviewer/preview_panel.py`、`SuperViewer/main.py`、`app_common/file_browser/_panel.py` | 快速导航必须走 `load_full=False`；最终提交再加载 full image；异步回填必须 token/path 校验。 |
| F02 | `.superpicky` 缩略图缓存布局 | `app_common/file_browser/_browser_core.py`、`_thumbnail.py`、`_workers.py` | 新缓存写入 `.superpicky/thumb_cache/<size>`；保留旧缓存迁移/读取兼容。 |
| F03 | 统一图片格式支持（含 PSD） | `app_common/image_formats.py`、`SuperBirdStamp/birdstamp/decoders/image_decoder.py`、`SuperViewer` 预览/扫描代码 | 扫描、缩略图、预览、导入路径都要使用同一扩展名来源；PSD 走 Pillow flattened image。 |
| F04 | 性能探针、诊断日志与 Python 环境解析 | `app_common/perf_probe.py`、`app_common/superviewer_user_options.py`、`SuperViewer/main.py`、`run.bat` | 探针默认关闭；`SuperViewer_PERF_PROBES` 可覆盖；启动/验证脚本不要硬编码当前 worktree `.venv`，需要支持共享 venv 路径。 |
| F05 | “在资源管理器中显示”路径修正 | `app_common/file_utils.py`、`app_common/file_browser/_panel.py`、`_directory_browser.py` | 文件列表 reveal 必须使用真实源文件路径；Windows 文件选择参数必须是 `explorer.exe /select,"<path>"`。 |
| F06 | `.superpicky/tags.cfg` 标签配置 | `SuperViewer/superviewer/photo_tags.py`、`SuperViewer/main.py`、标签菜单/信息面板 | 切换到不同 `.superpicky` 根时必须重新加载 tag vocabulary。 |
| F07 | JSON sidecar 元数据 | `app_common/exif_io/json_sidecar.py`、`photo_meta.py`、`SuperViewer/superviewer/photo_tags.py` | 新写入优先项目级 `.superpicky/metadata` 或配置目录；读取仍兼容 legacy sibling JSON/XMP。 |
| F08 | XMP sidecar 协作编辑日志 | `app_common/exif_io/xmp_sidecar_edits.py`、`photo_meta.py` | pending edits 放进 `.superpicky/sidecar_edits`；读取时要合并，写入时要锁和 compact。 |
| F09 | 删除/复制/剪切/粘贴联动 sidecar | `app_common/file_utils.py`、`app_common/file_browser/_panel.py` | 图片、`.xmp`、`.superviewer.json` 必须一起移动/复制；冲突后缀要保持主图和 sidecar 一致。 |
| F10 | `.superpicky` 写权限与只读门禁 | `app_common/file_browser/_permissions.py`、`_panel.py`、信息面板 | 目录/sidecar 不可写时禁用写入动作并显示“只读”标记；不要让缩略图 worker 写入只读根。 |
| F11 | 图片信息/标签面板拆分 | `SuperViewer/superviewer/image_info_tab_*.py`、`tag_menu.py`、`SuperViewer/main.py` | 右侧信息面板、标签 chip、注释编辑、文件名编辑、splitter 状态要一起迁移。 |
| F12 | 目录扫描、递归列表与进度 | `app_common/file_browser/_panel.py`、`_models.py`、`_browser_core.py` | 大目录扫描和元数据加载要保持后台/分批；目录浏览器需要跟随文件选择同步展开。 |
| F13 | 文件名/注释/标签/星级过滤与列表列 | `app_common/file_browser/_browser_core.py`、`_panel.py`、`SuperViewer/superviewer/photo_tags.py` | 过滤文本要匹配文件名和注释；标签过滤默认 match all；星级/pick/rating 兼容多来源。 |
| F14 | 删除到 `.superpicky/deleted` | `app_common/file_utils.py`、`app_common/file_browser/_panel.py` | 保留项目根相对路径结构；同时清理相关缩略图缓存；可用环境变量回退系统垃圾桶。 |
| F15 | 旧 report.db 依赖移除/弱化 | `app_common/file_browser/_browser_core.py`、`SuperViewer/main.py`、`README.md` | 文件系统扫描和 sidecar 元数据成为主路径；只保留兼容读取，不要把新功能重新绑回 report.db。 |

推荐合并批次：

1. 基础批次：F00、F02、F03、F04、F10。
2. 预览性能批次：F01、F12，并回归快速方向键导航。
3. 元数据批次：F06、F07、F08、F13。
4. 文件操作批次：F05、F09、F14。
5. UI 批次：F11、F15，以及 README/docs/配置更新。

## 当前 review 结论（2026-06-10）

- `SuperBirdTools-img_mgr` 是 `img_mgr` worktree，`SuperBirdTools` 是 `main` worktree；当前约定为“分支 = `SuperBirdTools-img_mgr`，主干 = `SuperBirdTools`”。
- 主仓库 `origin/img_mgr` 把 `.gitmodules` 中 `app_common` 分支从 `main` 改为 `res_mgr`，但当前主仓库提交里没有记录 `app_common` gitlink；`SuperBirdTools-img_mgr` 工作区里 `app_common` 是已暂存新增项。实际合并 F00 前必须决定：提交子模块指针，或在主干按独立 checkout/包管理方式固定 `app_common/res_mgr`。
- `app_common/res_mgr` 相对 `app_common/main` 承载了文件浏览器、缩略图、sidecar、权限、性能探针等大部分公共层变化；如果主干仍使用 `app_common/main`，F01-F14 多数会缺依赖。
- `run.bat` 已补成动态解析 Python 并默认设置 `APP_COMMON_LOG_FILE=logs\SuperViewer.log`；合并 F04 时要把这段启动脚本逻辑一起迁移到主干，否则文档里的日志预期会失效。
- 当前机器上最新可用共享 venv 位于主干 worktree：`E:\SuperApps\SBT\SuperBirdTools\.venv`；`SuperBirdTools-img_mgr` worktree 本身没有 `.venv`。验证命令和启动脚本不要直接假设 `.\.venv\Scripts\python.exe` 存在，应先解析 `PYTHON_EXE`、`VIRTUAL_ENV`、兄弟主干 `..\SuperBirdTools\.venv`，最后才回退当前 worktree `.venv`。
- `PreviewPanel.set_image(load_full=True)` 当前对不超过 `_DIRECT_ORIGINAL_PREVIEW_MAX_PIXELS = 40 * 1024 * 1024` 的非 RAW 图可能同步加载原图。若主干目标是严格避免普通选择同步解码，应在合并 F01 时把该逻辑改成异步或降低/关闭同步阈值。
- 右侧 `ImageInfoTabPanel_ImageInfo` 优先复用预览 pixmap；若当前画布还只是快速缩略图，图片尺寸可能显示缩略图尺寸。provider miss 时仍会同步 `QPixmap(path)`。合并 F11 时需要决定是否改用元数据尺寸或后台读取，避免重新引入 UI 卡顿。
- 本次轻量验证：使用共享 venv `E:\SuperApps\SBT\SuperBirdTools\.venv\Scripts\python.exe`（Python 3.13.11）执行 `py_compile` 通过；该 venv 未安装 `pytest`，单元测试未能运行；主仓库 `git diff --check origin/main...origin/img_mgr` 通过，`app_common/res_mgr` 还有 3 处 trailing whitespace（`file_browser/_panel.py` 6758、6761、6979）。

本指南用于把当前分支中 SuperViewer 相关的重要行为合并到其他分支，重点覆盖缩略图性能优化、性能探针、`在资源管理器中显示` 路径修正，以及 `.superpicky` 数据布局相关改动。

## 目标

合并时不要只拣单个函数。这里的改动是一个整体：文件选择、过滤、标星等高频操作必须避免同步解码原图；诊断日志必须可开关；网络盘和映射盘上的路径行为必须稳定；共享目录下的运行状态必须集中放进 `.superpicky`，避免污染源目录。

## 必合特性

### 1. 预览图两阶段加载

相关文件通常包括：

- `SuperViewer/superviewer/preview_panel.py`
- `SuperViewer/main.py`
- `app_common/file_browser/_browser_core.py`

必须保留的行为：

- 切换图片时，`PreviewPanel.set_image()` 先同步显示磁盘缩略图缓存，再异步加载原图。
- 缩略图优先从 `.superpicky/thumb_cache/<size>` 读取，通常按 `512 -> 256 -> 128` 尝试，必要时才生成小图兜底。
- 原图加载使用后台线程，后台线程只传递 `QImage` 等线程安全结果，UI 线程再转换为 `QPixmap`。
- 异步回填必须用 token/path 校验，防止快速切图后旧任务覆盖新图。
- 同一路径重复 `set_image()` 必须快速返回，避免重复解码。
- 快速选择路径应使用 `load_full=False`，不要在选择、过滤、标星等高频流程里同步加载原图。
- 导出、保存叠加层或需要原图质量的流程，必须先确保 full image 已加载完成。
- `closeEvent` 或窗口关闭时必须调用预览面板的 shutdown/stop 逻辑，保证后台线程退出。

禁止回退的模式：

- 在选择变化、过滤变化、标星变化中直接 `QPixmap(path)` 或同步读取大图。
- 让旧的异步加载任务无条件覆盖当前画布。
- 用本地 AppData 缓存替代 `.superpicky` 共享缓存，除非当前目录没有可用的 `.superpicky`。

### 2. 缩略图缓存目录布局

相关文件通常包括：

- `app_common/file_browser/_browser_core.py`
- 任何调用 `_thumb_disk_cache_path`、缩略图缓存 worker、缩略图读取函数的文件

必须保留的目录规则：

- 有 `.superpicky` 时，缩略图缓存写入该目录下：
  - `.superpicky/thumb_cache/128`
  - `.superpicky/thumb_cache/256`
  - `.superpicky/thumb_cache/512`
  - `.superpicky/thumb_cache/1024`
- 不要把所有尺寸混放到 `thumb_cache` 根目录。
- 映射网络驱动器和 UNC 路径必须解析到同一个项目级 `.superpicky` 语义下。
- 只有找不到项目级 `.superpicky` 时，才考虑回退到用户本地缓存。
- 旧版 `.superpicky/cache/thumb_cache_<size>` 缓存可以读取并迁移，但新缓存不应继续写入该目录。

验证重点：

- 打开 `K:\...` 或 UNC 路径目录后，缓存应出现在对应项目的 `.superpicky/thumb_cache/<size>` 下。
- 128 和 256 等尺寸必须分别落在不同子目录。

### 3. 性能探针开关

相关文件通常包括：

- `app_common/perf_probe.py`
- `app_common/superviewer_user_options.py`
- SuperViewer 菜单栏/设置对话框相关代码
- `run.bat`

必须保留的行为：

- 性能探针默认关闭，避免正常使用时刷大量日志。
- 用户配置中保留 `KEY_PERF_PROBES_ENABLED = "perf_probes_enabled"`。
- GUI 中保留“性能探针日志”开关。
- 环境变量 `SuperViewer_PERF_PROBES` 可以覆盖配置，便于临时诊断。
- 旧的 `[PERF]`、`[STAT]`、`[THUMB_PROFILE]` 诊断日志必须接入同一个开关体系。
- `SuperViewer_THUMB_PROFILE` 默认关闭。
- `run.bat` 应将日志输出到仓库 `logs/SuperViewer.log`，便于用户直接提供诊断日志。

验证重点：

- 关闭开关时，切图和过滤不应持续输出 `PERF_PROBE`。
- 开启开关后，切图、过滤、标星等流程应有可定位耗时的日志。

### 4. 在资源管理器中显示

相关文件通常包括：

- `app_common/file_utils.py`
- `app_common/file_browser/_panel.py`
- `app_common/file_browser/_directory_browser.py`

必须保留的行为：

- 文件列表的“在资源管理器中显示”必须使用当前选中文件的真实路径。
- 目标列表/目录列表的 reveal 行为不能被文件列表逻辑改坏。
- Windows 上选择具体文件时，Explorer 命令应使用：

```text
explorer.exe /select,"<absolute-path>"
```

- 不要回退成 `subprocess.Popen(["explorer.exe", f"/select,{path}"])`。当路径包含空格、中文或网络盘路径时，Explorer 可能把参数解析错，导致打开错误目录。

验证重点：

- 在文件列表中选择 `K:\测试图\DSC06705.jpg`，应打开该文件所在目录并选中该文件。
- 在目标列表或目录树中 reveal 目录，仍应打开对应目录。
- 路径包含中文、空格、映射盘符和 UNC 时都要验证。

### 5. `.superpicky` 项目级状态

相关文件通常包括：

- `app_common/file_utils.py`
- `app_common/file_browser/_browser_core.py`
- `SuperViewer/superviewer/photo_tags.py`
- `SuperViewer/superviewer/image_info_tab_tags.py`
- `SuperViewer/superviewer/image_info_tab_image_info.py`

必须保留的行为：

- `tags.cfg` 从当前项目的 `.superpicky/tags.cfg` 读取。
- 切换到不同 `.superpicky` 所在目录时，必须重新读取并替换现有 tags。
- 删除文件默认可以移动到 `.superpicky/deleted` 或等价删除目录，并保持原目录相对路径结构。
- 删除图片时，相关 sidecar 文件也要一起移动到删除目录的对应位置。
- sidecar 并行编辑记录放入 `.superpicky/sidecar_edits` 或等价项目级目录，不要生成在源图片目录旁边。
- sidecar 编辑目录必须按源文件相对路径或稳定哈希规划，避免同名文件冲突。
- 读取 sidecar 时，必须能正确读取 `dc:description` 注释内容。
- “过滤文件名/注释”必须同时匹配文件名和注释字段。

## 其他可移植改动

以下改动不是单独的“大功能入口”，但也适合移植到其他分支：

- XMP/Exif 元数据别名归一化：`XMP-dc:Description`、`XMP-dc:description`、`XMP:Description`、`Description` 等注释字段应在读取层统一暴露，避免写入成功但 UI 读不到。
- sidecar 协作编辑日志：`app_common/exif_io/xmp_sidecar_edits.py` 的编辑日志和合并逻辑适合复用到任何多人共享网盘场景，核心目标是降低多人同时写同一个 XMP 时的覆盖风险。
- sidecar 读取自动合并：读取 XMP 行数据时要叠加 pending edits，让 UI 看到“基础 sidecar + 本地/其他客户端编辑日志”的合并结果。
- 删除/移动相关 sidecar：`move_to_trash` 不只移动主图，还要识别并移动对应 `.xmp`，并在目标已存在时让主图和 sidecar 使用同一冲突后缀。
- 删除目录相对路径保持：移动到 `.superpicky/deleted` 时保留相对项目根的目录结构，便于恢复和审计。
- 文件名/注释过滤：过滤框文本应匹配文件名和注释；一旦过滤条件需要跨目录结果，应保留递归范围切换和 selection restore 行为。
- 后台持久缩略图预生成：文件列表加载后异步补齐多个尺寸的持久缩略图缓存，和预览面板的首帧缩略图优化配套使用。
- 旧缩略图缓存迁移：如果目标分支已经生成过 `.superpicky/cache/thumb_cache_<size>`，合并时应保留读取并迁移到 `.superpicky/thumb_cache/<size>` 的兼容逻辑。
- 测试契约：`app_common/tests/test_file_browser_cache_paths.py`、`app_common/tests/test_file_utils.py`、`app_common/tests/test_photo_meta_proxy.py`、`SuperViewer/tests/test_photo_tags.py`、`SuperViewer/tests/test_image_info_metadata.py` 里的断言适合作为移植后的最小回归测试集合。

## 合并顺序建议

1. 先合并 `.superpicky` 路径和缩略图缓存布局，因为预览性能依赖共享缩略图缓存。
2. 再合并两阶段预览加载，确保普通切图流程不会同步解码原图。
3. 合并性能探针开关，方便后续验证和定位分支差异。
4. 合并 reveal 路径修正，并分别验证文件列表和目录/目标列表。
5. 最后合并 sidecar、tags、删除目录、过滤注释等项目级状态行为。

## 冲突处理原则

- 如果目标分支已有不同预览架构，也要保留“先缩略图、后异步原图”的外部行为。
- 如果目标分支已有性能日志，不要再新增一套独立开关，应接入统一的 `perf_probe` 语义。
- 如果目标分支改过文件浏览模型，优先追踪当前选中文件的真实绝对路径，不要从显示文本、缓存 key 或过滤后的中间数据反推 reveal 路径。
- 如果目标分支支持网络盘，所有路径比较和相对路径计算都要覆盖映射盘符、UNC、中文路径和大小写差异。

## 最小验证清单

先解析可用 Python，不要直接假定当前 worktree 根目录存在 `.venv`。在当前双 worktree 布局下，分支 `SuperBirdTools-img_mgr` 可以复用兄弟主干 `SuperBirdTools\.venv`：

```powershell
$PythonExe = $env:PYTHON_EXE
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
  if ($env:VIRTUAL_ENV -and (Test-Path (Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"))) {
    $PythonExe = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
  } elseif (Test-Path "..\SuperBirdTools\.venv\Scripts\python.exe") {
    $PythonExe = "..\SuperBirdTools\.venv\Scripts\python.exe"
  } elseif (Test-Path ".\.venv\Scripts\python.exe") {
    $PythonExe = ".\.venv\Scripts\python.exe"
  } else {
    $PythonExe = "python"
  }
}
Write-Host "Using Python: $PythonExe"
```

然后用解析出的解释器验证改动文件：

```powershell
& $PythonExe -m py_compile `
  app_common\file_utils.py `
  app_common\perf_probe.py `
  app_common\image_formats.py `
  app_common\file_browser\_browser_core.py `
  app_common\file_browser\_panel.py `
  app_common\file_browser\_directory_browser.py `
  app_common\file_browser\_permissions.py `
  app_common\exif_io\json_sidecar.py `
  app_common\exif_io\xmp_sidecar_edits.py `
  app_common\exif_io\photo_meta.py `
  SuperViewer\superviewer\preview_panel.py `
  SuperViewer\main.py `
  SuperViewer\superviewer\photo_tags.py `
  SuperViewer\superviewer\image_info_tab_tags.py `
  SuperViewer\superviewer\image_info_tab_image_info.py
```

手工验证：

- 打开包含大图的本地目录，切换图片时画布应先立即显示缩略图，再替换为清晰原图。
- 打开映射网络盘目录，例如 `K:\...`，重复验证切图、过滤、标星。
- 开启性能探针后执行切图、过滤、标星，检查 `logs/SuperViewer.log` 中是否有可用耗时点。
- 关闭性能探针后，确认日志不再持续输出性能探针内容。
- 检查缩略图缓存是否写入项目 `.superpicky/thumb_cache/<size>`。
- 在文件列表和目录/目标列表分别执行“在资源管理器中显示”，确认打开位置正确。
- 写入并重新读取中文 `dc:description`，确认注释显示和“过滤文件名/注释”匹配正常。
- 删除图片后，确认图片和 sidecar 一起移动到 `.superpicky` 删除目录，并保留相对路径结构。

## 回归风险信号

出现以下现象时，优先检查本指南对应模块：

- 切换图片明显卡顿，日志显示选择变化期间同步解码原图。
- 缩略图缓存重新出现在 `AppData\Local\SuperViewer\thumb_cache`，而项目目录下已有 `.superpicky`。
- `thumb_cache` 根目录直接堆缓存文件，没有按尺寸分目录。
- 文件列表 reveal 打开父级错误目录，目标列表 reveal 却正常。
- sidecar 编辑目录出现在图片源目录旁边，污染源目录。
- 注释能写入但重新打开读不出来，或过滤注释无效。
