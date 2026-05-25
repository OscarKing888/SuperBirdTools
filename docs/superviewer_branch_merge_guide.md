# SuperViewer Branch Merge Guide

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

使用仓库根目录 `.venv` 解释器验证改动文件：

```powershell
.\.venv\Scripts\python.exe -m py_compile `
  app_common\file_utils.py `
  app_common\perf_probe.py `
  app_common\file_browser\_browser_core.py `
  app_common\file_browser\_panel.py `
  app_common\file_browser\_directory_browser.py `
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
