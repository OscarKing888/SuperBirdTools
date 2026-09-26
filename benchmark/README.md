# 图像热路径基准（M0-1）

对应 [核心加速计划](../docs/plans/core-acceleration-plan.md) 的 M0-1。脚本直接调用现有生产函数，不修改生产代码。

## 文件

| 文件 | 作用 |
| --- | --- |
| `bench_imaging.py` | 逐样本、逐用例记录耗时 P50/P95/max、ExifTool 一次性进程启动次数与耗时、整进程峰值 RSS 增量，输出 JSON 与 Markdown |
| `env_report.py` | 记录平台、CPU、内存、Python、依赖与原生库版本（libjpeg-turbo/LibRaw/libheif/Qt）、ExifTool 版本、线程相关环境变量 |
| `DSC*.{jpg,ARW,HIF}` | 用户提供的样本（`*.jpg` 走 Git LFS，需要 `git lfs pull`；否则脚本把它标记为“未覆盖”） |
| `results/` | 已提交的结果摘要（只含样本 ID 与哈希，不含本机路径） |

## 运行

从仓库根目录运行，使用 repo 根 `.venv`。

macOS：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python3 benchmark/bench_imaging.py --repeat 10
```

Windows PowerShell：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe benchmark\bench_imaging.py --repeat 10
```

常用参数：

- `--samples <dir>`：样本目录，默认 `benchmark/`。可以指向仓库外的目录。
- `--cold-copy`：每轮把样本复制到新的临时路径，让按路径键控的应用缓存失效。OS 页缓存不受控：macOS 可在运行前执行 `sudo purge`；Windows 没有无特权的可靠方法，报告里要注明。
- `--cases viewer.raw,thumb`：按用例名前缀过滤。
- `--synthetic`：在临时目录生成合成图（24MP JPEG 方向 1/6、RGBA PNG、50% 截断 JPEG），只用来验证脚本能跑通，结果**不作为基线**。
- `--out <dir>`：结果目录，默认系统临时目录下的 `sbt-bench-results`。
- `EXIFTOOL_EXE=<path>`：显式指定 ExifTool（沿用 `app_common.exif_io.exiftool_path` 的环境变量）。

## 安全约束

- 样本先复制到临时目录，只在副本上运行，临时目录在结束时删除。脚本结束前会核对样本哈希，不一致时退出码为 2。
- 被测函数都是只读解码路径；脚本不写原图、XMP 或 `report.db`。
- 不要把私人照片库目录直接作为 `--samples`。先复制一份。

## 用例

| 用例 | 对应生产路径 | 线程（生产中） |
| --- | --- | --- |
| `viewer.raw.get_raw_preview_jpeg` | `thumb_stream.get_raw_preview_jpeg` | GUI（点击）/ 池线程（缩略图） |
| `viewer.raw.orientation_read` | `focus_preview_loader._get_orientation_from_file` | GUI |
| `viewer.raw.set_image_sync_segment` | RAW 点击时 `set_image` 的同步段 + `QPixmap.fromImage` | GUI |
| `viewer.full_preview_qimage` / `_to_pixmap` | `preview_panel._load_full_preview_qimage`（≤40MP 非 RAW 时同步） | GUI 或 QThread |
| `viewer.quick_preview_pixmap_512` | `preview_panel._load_quick_preview_pixmap` | GUI |
| `thumb.load_thumbnail_rgb_{128,512,2048}` | `thumb_stream.load_thumbnail_rgb` | 池线程 |
| `birdstamp.decode_image_for_preview_2048` / `preview_to_qpixmap` | `image_decoder.decode_image_for_preview` + `pil_to_qpixmap` | QThread + GUI |
| `birdstamp.decode_image_full` | `image_decoder.decode_image`（导出） | 导出线程池 |

单线程顺序执行，测的是单次调用延迟，不是并发吞吐。吞吐、GUI 心跳、画布绘制与导出分段由 M0-2 的探针补齐。
