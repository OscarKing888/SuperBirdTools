# SuperViewer / SuperBirdStamp 核心加速版实施计划

> 状态：**计划（未实施）**。本文只基于静态代码审查形成，没有任何运行时测量。
> 文中每条结论标注：**[代码事实]** 已在源码中核对；**[已测结论]** 有实测数据（本轮为 0 条）；**[待验证]** 推断或假设，需在 M0 中验证。
> 行号对应下文“审查版本”，后续代码变动后以符号名为准。

---

## 0. 结论先行

1. **目前没有证据表明 Python 解释执行是两款应用的主要瓶颈。** 像素级工作已经由原生库完成（Pillow/libjpeg-turbo、rawpy/LibRaw、pillow-heif/libheif、Qt raster）。已定位的高代价点大多来自**调度与数据流**：GUI 线程同步解码或起外部进程、重复解码、每帧整图拷贝 3–9 次、字体和模板不缓存、每次重绘都对整图做平滑缩放、manifest 写入 O(N²)。这些问题换成 C++ 也不会自动消失，在 Python 层用现有库就能修。
2. 因此计划改为**两条轨道**：
   - **P 轨（仅优化数据流）**：先做，风险低，也用作 A/B 的第二组对照（“只优化数据流版”）。
   - **N 轨（C++ 核心）**：受门槛 **G1** 约束。只有在 P 轨完成后，某阶段的剩余耗时仍主要落在“解码→缩放→方向→格式打包”这类可以融合的原生段上，并且原型微基准达到门槛，才进入 M1–M4。达不到门槛时，N 轨停止，本计划只交付 M0 和 P 轨的成果。这是预期内的合法结果。
3. **首个执行任务是 M0-1**：建立可重复的基准脚本、样本清单和环境记录，全部复用现有 `perf_probe`。见第 8 节。
3a. **M0-1 已完成首轮（2026-09-26）**：`benchmark/bench_imaging.py` 在 Linux 云容器上用用户提供的 JPG/ARW/HIF 跑出第一份相对数据（见 7.5）。RAW 提取与 JPEG draft 的问题都可以在 Python 层解决，N1 的预期收益下调。macOS arm64 基线已完成（见 7.6），G0 通过：P-1/P-3/P-10/P-11 进入实施；Windows x64 基线仍待运行。
4. 如果 N 轨启动，首个原生切片的候选是 **N1 `decode_scaled`**：把 JPEG 或 RAW 内嵌 JPEG 的缩放解码、方向校正和显示格式打包融合为一次调用。Viewer 缩略图、Viewer 快速预览、BirdStamp 预览解码三处都能复用。它是否值得做，由 G1 决定。

---

## 1. 审查范围、版本与证据边界

### 1.1 实际审查对象

| 项 | 值 |
| --- | --- |
| 主仓库 | `OscarKing888/SuperBirdTools`（单仓，两款应用都在其中），本地 `/home/user/SuperBirdTools` |
| 分支 / commit | `claude/loving-bell-k3zw4f` @ `7f5d99a39c11629a08146c631602f55b5f18c45e`（2026-09-25 的发布 CI 修复合并点）。用 `git ls-remote` 核对，远端 `main` 同为 `7f5d99a`；本地 `origin/main` 跟踪引用较旧（`88ffd31`），本轮未 fetch |
| 工作区修改 | 审查开始时 `git status` 干净；本轮唯一新增文件是本文 |
| SuperViewer | 单仓内 `SuperViewer/` |
| SuperBirdStamp | 单仓内 `SuperBirdStamp/`（包 `SuperBirdStamp/birdstamp`） |
| 公共库 | 子模块 `app_common` → `OscarKing888/SuperAppCommonLib`，gitlink 固定于 `ae968ffa96c97ce254dcdfdc5e502e36f050e861`，`.gitmodules` 为 `branch = main` |
| 子模块状态 | 本地 `app_common/` **未初始化**（`git submodule status` 显示 `-ae968ff`）。为避免改动 Git 状态，把 SuperAppCommonLib 只读克隆到会话临时目录，并检出同一 commit 后阅读。该 commit 等于其远端 `main`（落后 0 个提交） |
| 两应用公共库版本 | 单仓内两应用**共用同一个** `app_common` gitlink，不存在版本分歧 |
| 独立仓库 | `OscarKing888/SuperBirdViewer`（最后推送 2026-03-10）和 `OscarKing888/SuperBirdStamp`（2026-03-11）存在，但不在本会话仓库范围内，**未审查**。README 说明单仓由原仓库“复制整理”而来，单仓是当前活跃开发位置（2026-09 仍有推送）。本计划只针对单仓；若独立仓库仍需发布，需另行评估 |

### 1.2 已读规则与文档

`CLAUDE.md`、`AGENTS.md`、`ai_rules/AI_CODING_RULES.md`、`ai_rules/AI_RULES_SETUP.md`、`README.md`、`SuperViewer/docs/ARCHITECTURE.md`、`SuperBirdStamp/docs/ARCHITECTURE.md`、`docs/REVIEW_2026-09-07.md`、`.github/workflows/build-release.yml`、各 `requirements.txt`、各 `.spec`、`build_all.*`、`pytest.ini`、`THIRD_PARTY_NOTICES.md`。

### 1.3 证据边界（重要）

- 审查环境为 Linux 容器，**没有 `.venv`，也没有 Pillow、PyQt6、numpy、rawpy**。按规则本轮不安装依赖，因此**没有运行任何测试、基准或应用**。
- 仓库内的样例图（`*/images/*.jpg`、`*.png`）是 **Git LFS 指针**（约 130 字节），没有真实像素样本。
- Linux 不是发布平台。**Windows x64 和 macOS arm64 在本轮均“未验证”。**
- 结论：本文所有性能判断都是 **[代码事实]** 或 **[待验证]**，不承诺任何加速倍数。

### 1.4 现有平台与依赖事实 [代码事实]

- 发布矩阵（`build-release.yml`）：**Windows x86_64（windows-2025）** 与 **macOS arm64（macos-15）**，Python **3.13**。CI 不跑 pytest，不做代码签名或公证，也没有 Intel/universal2 macOS 包。本计划维持这两个平台，不取消任何现有平台。
- 依赖均为 `>=` 下限，没有锁文件：PyQt6≥6.6、Pillow≥10、numpy≥2.0（BirdStamp 抬高了下限）、rawpy≥0.20、pillow-heif≥0.16、piexif、ExifRead、torch/ultralytics（仅 BirdStamp）、imageio-ffmpeg；ExifTool 以二进制形式随包分发。CI 会把 `pip freeze` 写进 `DEPENDENCIES.txt`。
- Qt 绑定是 **PyQt6**，代码里保留了 PyQt5/PySide6 的 ImportError 回退。原生核心**不得**链接 Qt（见 4.6）。
- 仓库**没有任何 C/C++/Cython/CMake/pyproject**，所以原生构建链是全新引入。
- `SuperViewer/SuperViewer_mac.spec` 设置了 **`upx=True`**（EXE 和 COLLECT 两处）。其余 spec 都是 `upx=False`。这对原生 `.so` 是一个风险点，见第 6 节。
- `resolve_video_render_workers` 用可选的 `psutil` 估算内存预算，但 `psutil` 不在 requirements 中，可能是经 ultralytics 间接安装的 [待验证]。缺失时回退为固定 4。
- 许可：两应用均为 AGPL-3.0，`app_common` 沿用上游许可。

---

## 2. 现有架构与关键调用链

线程记号：**GUI** = Qt 主线程；**QT** = 专属 QThread；**POOL** = `BrowserWorkPool` 线程（Viewer 默认约 `cpu_count` 个）；**TPE** = `ThreadPoolExecutor`；**PROC** = 外部进程。

### 2.1 SuperViewer：普通点击一张照片

```
FileListPanel.file_selected
 └ MainWindow._on_file_selected_from_list (main.py:572)            [GUI]
    └ MediaPreviewPanel.set_image → PreviewPanel.set_image (preview_panel.py:444)  [GUI]
       ├ 同路径复用 / request token++ / 取消旧 full loader
       ├ RAW: _load_raw_embedded_preview_qimage (preview_panel.py:282)   [GUI, 同步]
       │    ├ thumb_stream.get_raw_preview_jpeg: 逐个 tag 调 run_exiftool_once
       │    │    (JpgFromRaw → PreviewImage → ThumbnailImage，最多 3 次新进程)  [PROC]
       │    ├ 回退 rawpy.extract_thumb / piexif 缩略图
       │    ├ _get_orientation_from_file（piexif/exifread 再读文件）
       │    └ Image.open(bytes) 全尺寸解码 → transpose → convert → tobytes → QImage.copy()
       ├ 非 RAW 且 ≤40MP: _load_full_preview_qimage                      [GUI, 同步]
       │    ├ 普通格式: QImageReader(autoTransform).read() → .copy()（多余的整图拷贝）
       │    └ HEIF: PIL.open → exif_transpose → convert → tobytes → QImage.copy()（整幅 HEVC 解码）
       ├ 否则: 当前档位缓存（内存 QPixmap / 持久 JPEG 由 QPixmap(path) 解码）      [GUI]
       │    └ 缓存未命中且非 HEIF: _load_quick_preview_pixmap → thumb_stream.load_thumbnail_rgb [GUI]
       ├ QPixmap.fromImage（RGB888 → 原生格式转换）                             [GUI]
       ├ canvas.set_source_pixmap
       └ 若未完整: 80ms 计时器 → _FullPreviewLoader.run（单任务+最新 pending） [QT]
            └ loaded(QImage) → GUI 槽 QPixmap.fromImage → set_source_pixmap → full_preview_ready
```

**[代码事实]** `_should_load_full_preview_sync()` 对 RAW 恒返回 False，测试 `test_sync_full_preview_policy_never_syncs_raw` 也这样断言。但 `set_image()` 在调用这个判定**之前**，就无条件在 GUI 线程同步执行 `_load_raw_embedded_preview_qimage`（preview_panel.py:486-487）。所以“RAW 不同步解码”只对解马赛克成立：内嵌 JPEG 的提取、进程启动和全尺寸解码仍然阻塞 GUI。

### 2.2 SuperViewer：缩略图与持久缓存

```
ThumbnailLoader(QThread 协调) → BrowserWorkPool.submit(ThumbnailAction)   [QT → POOL]
 └ _load_single → thumb_stream.load_thumbnail_rgb / iter_thumbnail_rgb_progressive  [POOL]
     ├ RAW: get_raw_preview_jpeg（每 tag 一个 exiftool 新进程；没有复用池线程已有的 stay-open 会话）
     │   → Image.open(bytes)，没有 draft（全尺寸内嵌 JPEG 被整幅解码）
     ├ JPEG: Image.open + draft("RGB",(size,size))
     ├ PNG/TIFF/WebP/HEIF: 整幅解码
     └ _pil_to_rgb_thumb: exif_transpose（先于 thumbnail，使 thumbnail 内部 draft 失效）
         → thumbnail(LANCZOS) → 合成到 (45,45,45) → tobytes
   渐进式 JPEG: ImageFile.Parser 全分辨率喂数据出中间帧，最后**再完整解码一次**
 └ _rgb_bytes_to_qimage: QImage(RGB888).copy()                               [POOL]
 └ ThumbnailMemoryCache.put（深拷贝）/ get（深拷贝），预算 min(25% RAM, 16GB)
 └ 磁盘缓存: QImage.save(JPEG, 85)（单线程 executor）                        [TPE]
 └ GUI: 30ms 批量 _flush_thumb_pending_batch → 逐项 QPixmap.fromImage          [GUI]
持久缓存: PersistentThumbCacheWorker → 按 max(missing tier) 解码一次 → 小档用 Qt Smooth 缩放 → JPEG 85
```

### 2.3 SuperViewer：显示、缩放、拖拽

`PreviewCanvas.paintEvent`（canvas.py:870）[GUI]：
- `draw_checker_background`：**纯 Python 双重 while 循环**，每 8×8 格一次 `fillRect`。1200×900 的视图约 1.7 万次 Python 调用，每次重绘都画，不透明图也画。**[代码事实]**
- `drawPixmap(draw_rect, 全尺寸 source, 全源矩形)` 开启 `SmoothPixmapTransform`：每次滚轮、拖拽或叠加变化都对整幅全分辨率 pixmap 做平滑缩放，不裁剪到可见区域，也没有缩放缓存。**[代码事实]** 实际耗时 **[待验证]**，Qt raster 对 drawPixmap 的内部优化程度需要实测。

### 2.4 SuperBirdStamp：选图与参数变更后的预览

```
选图: _begin_photo_selection → EditorPreviewDecodeWorker.run                  [QT]
   ├ 复用 Viewer 逐文件缩略图缓存 → quick_decoded
   └ decode_image_for_preview(≤2048 长边): draft → thumbnail(LANCZOS, reducing_gap=3) → exif_transpose → RGB
       RAW: get_raw_preview_jpeg（同上，最多 3 个 exiftool 进程；内嵌 JPEG 无 draft）→ 回退 rawpy half_size
   → GUI 两次 render_preview（quick + decoded）
参数变更: widget valueChanged → _on_output_settings_changed（每次 tick 都做设置深拷贝/规范化等）[GUI]
   → 250ms 去抖 → render_preview (editor_renderer.py:1545)                        [GUI]
      ├ current_source_image.copy()
      ├ crop plan（preview_only，鸟体检测改为异步 BirdDetectWorker [QT]）
      ├ pad_image(ImageOps.expand 新整图)
      ├ _resolve_template_payload_for_render：**每次重读并规范化模板 JSON**
      ├ render_template_overlay: image.convert("RGBA") → 逐字段 load_font（**无缓存**，逐候选字号 truetype）
      │     → 文字层 LANCZOS 缩放 → alpha_composite；渐变 banner 另建整幅 RGBA overlay
      │     → canvas.convert("RGB")
      ├ 焦点框 ImageDraw
      └ pil_to_qpixmap: (可选 LANCZOS) → convert("RGBA") → tobytes → QImage.copy() → QPixmap
```

模板管理对话框的 `_refresh_preview`：全分辨率源图、GUI 线程、没有去抖（14 处调用点）。**[代码事实]**

### 2.5 SuperBirdStamp：导出

```
_build_export_render_jobs                                                    [GUI]
   └ (可选) prepare_uniform_auto_crop_plans: 逐张 decode_image 全尺寸 + YOLO   [GUI, 串行]
_export_render_jobs_to_images → TPE(resolve_video_render_workers: ≤8, 内存 20%/≤4GiB ÷ 24B/px)
   └ render_video_frame: decode_image(**再次**全尺寸解码)                        [TPE]
        ├ 普通: exif_transpose().convert("RGB").copy()（约 3 份整图）
        ├ RAW: rawpy.postprocess(全尺寸, 8bit) → fromarray → convert
        └ pipeline: pad→crop→resize_fit(LANCZOS)→template_overlay→focus_overlay → convert("RGB")
   GUI 在 next(as_completed) 阻塞，靠 processEvents(ExcludeUserInputEvents) 维持响应
   每次导出新建 bird_box_cache={}，YOLO 在全分辨率上执行，并被锁串行化
保存: JPEG quality=92/optimize/progressive；PNG optimize=True；**不写 exif，不写 icc_profile**
GIF: 源帧先存 PNG(optimize=True) → export_gif 在 **GUI 线程**逐变体重新打开 PNG、LANCZOS、Pillow 自适应调色板
视频: 源帧 PNG(compress 1) → 第二池重开 PNG 归一化再写 PNG → ffmpeg 读 PNG 序列
      write_frame_manifest 在**每帧完成后**重写整份 JSON(indent=2)+os.replace → O(N²)
```

### 2.6 公共事实

- **色彩管理：两款应用都没有。** 仓库中没有 `ImageCms`，`icc_profile` 在解码、转换、保存任一环节都不传递；Qt 路径只做 `setAutoTransform`。**[代码事实]** 迁移必须保持“ICC 不参与像素换算”的现有行为，新增色彩管理不算等价迁移。
- **方向**：Qt 路径用 `setAutoTransform`；PIL 路径用 `exif_transpose`；RAW 内嵌预览的方向从源文件读取，而不是从内嵌 JPEG 读取。
- **截断图像**：`thumb_stream` 在导入时全局设置 `ImageFile.LOAD_TRUNCATED_IMAGES = True`，属于进程级副作用。损坏的 JPEG 会部分显示而不是报错。原生实现必须对齐这一行为，或报可恢复错误交回 Python。
- **现有性能探针**：`app_common/perf_probe.py`，由 `SuperViewer_PERF_PROBES` / `BIRDSTAMP_PERF_PROBES` 或用户选项开启，输出 `[PERF_PROBE]`、`[PERF][image_switch]`、`[preview.full]`、`[preview.fast.summary]`、`SuperViewer_THUMB_PROFILE` 的 p95 汇总、`BrowserWorkPool.snapshot()` 等。BirdStamp 的 `perf.span()` 已覆盖 select/decode/render_preview 各子段和拖拽。**导出、GIF、视频、模板叠加没有探针**，canvas 的 `paintEvent` 也没有计时。
- **测试**：约 578 个测试函数（build_tools 7 / SuperViewer 136 / BirdStamp 214 / app_common 221），全部是功能或策略测试，**没有基准测试**。

---

## 3. 候选热点表与处理判断

判断列含义：**保持**＝暂不改；**P**＝仅优化 Python 调用和数据流；**N?**＝P 之后仍有剩余时作为原生候选，受 G1 约束。

| ID | 位置（调用者 / 线程） | 候选瓶颈 | 已有优化 | 证据 | 验证方法 | 判断 |
| --- | --- | --- | --- | --- | --- | --- |
| V1 | `PreviewPanel.set_image` → `_load_raw_embedded_preview_qimage` / `thumb_stream.get_raw_preview_jpeg`（GUI） | RAW 普通点击在 GUI 线程上：最多 3 次 ExifTool 冷启动（Windows 版是打包的 Perl，启动成本高 [待验证]）、方向二次读文件、内嵌 JPEG 全尺寸解码、3–4 次拷贝 | 不解马赛克；有 20s 超时 | [代码事实] 同步执行；耗时 [待验证] | M0 分段计时：进程启动、提取、解码、转换各自多少 ms | **P**：改用 stay-open 会话或 LibRaw `extract_thumb` 的进程内提取，并缓存路径到字节和方向。已决定移出 GUI 线程，先显示档位缓存再异步替换（见 7.3、P-10）|
| V2 | `_load_full_preview_qimage`，≤40MP 同步（GUI） | JPEG：QImageReader 解码后多余的 `.copy()`；HEIF ≤40MP：整幅 HEVC 在 GUI 线程解码（复审报告测得 49.8MP HIF 约 1.5 s）| 阈值可配 | [代码事实]；40MP 以下 HIF 的耗时 [待验证] | 按 24/33/40MP 的 JPEG 与 HIF 分别测 `set_image` 的 load_ms | **P**：去掉多余拷贝、在工作线程转成原生像素格式。HEIF 改用独立的同步阈值（见 7.3、P-11） |
| V3 | `_load_quick_preview_pixmap`（GUI，缓存未命中的大图兜底） | PNG/TIFF/WebP >40MP 没有 DCT 缩放，整幅 PIL 解码加 LANCZOS 在 GUI 线程 | JPEG 用 draft；HEIF 延后 | [代码事实] | 大 PNG/TIFF 冷缓存点击计时 | **P**：优先 `QImageReader.setScaledSize`（它已经是后备路径），把 PIL 放到后面 |
| V4 | `PreviewCanvas.paintEvent`（GUI） | Python 棋盘格循环；每帧全尺寸平滑缩放 | 无 | [代码事实]；耗时 [待验证] | 新增 paint 计时探针，在 45MP 图上测滚轮缩放和拖拽，统计每帧 ms 与 FPS | **P**：棋盘格改为缓存的 `QBrush` 纹理（不透明图直接跳过）；按缩放级别缓存缩放后的 pixmap，或只绘制可见的源矩形。**不原生化**（Qt 已是原生实现）|
| V5 | `thumb_stream._pil_to_rgb_thumb` 等（POOL） | RAW 内嵌 JPEG 不用 draft；`exif_transpose` 放在 thumbnail 之前；渐进式 JPEG 二次全解码；内存缓存 put/get 深拷贝；GUI 线程逐项 RGB888 转原生格式 | 视口优先、档位缓存、批量刷新 | [代码事实] | 缩略图吞吐（张/秒）、`decode_ms` p50/p95、GUI flush 耗时 | **P** 先行；残差为 **N?（N1）** |
| V6 | 持久缩略图生成（POOL） | 按最大档解码，小档 Qt 缩放，JPEG 85 编码 | 按最大缺失档只解码一次 | [代码事实] | 目录后台生成总耗时 | **P**（复用 V5 的修复）；编码保持 Qt |
| V7 | RAW 缩略图提取（POOL） | 池线程各自有 stay-open ExifTool 会话，RAW 预览提取却仍用 `run_exiftool_once` 新开进程 | 有取消和超时 | [代码事实] | 统计 RAW 目录的进程启动次数与耗时 | **P** |
| B1 | `render_preview` 与 `_on_output_settings_changed`（GUI） | 每次渲染都重读模板 JSON、字体不缓存、整画布 RGBA↔RGB 往返、渐变 banner 建整幅 overlay、`pil_to_qpixmap` 约 4 次拷贝；每个滑块 tick 在去抖前就做设置深拷贝 | 250ms 去抖；预览 ≤2048；像素预算 | [代码事实]；各子段 ms 可用现有 span 测 [待验证] | 开 `BIRDSTAMP_PERF_PROBES`，统计 `render_preview.*` 各子段 | **P**：字体 LRU、模板内容缓存（按 mtime 失效）、只合成裁切区域、减少模式转换。残差为 **N?（N2）** |
| B2 | 模板对话框 `_refresh_preview`（GUI） | 全分辨率源图、无去抖 | 鸟框每个对话框缓存一次 | [代码事实] | 连续调整数值时的卡顿 | **P**：加去抖并使用预览尺寸源图 |
| B3 | 导出解码：`decode_image` / `prepare_uniform_auto_crop_plans` | 普通格式约 3 份整图拷贝；统一裁切或去抖时每张图全尺寸解码两次（RAW 两次完整解马赛克）；帧缓存全部命中也会先跑预计算；预计算与 YOLO 在 GUI 线程 | 帧缓存签名 | [代码事实] | 统一裁切批量导出的总时长与峰值 RSS | **P**：预计算移入工作线程；在内存预算内复用解码结果或只解一次；`_decode_standard` 去掉多余拷贝 |
| B4 | `render_video_frame` 管线（TPE） | pad 产生整图拷贝（`ImageOps.expand`）后才 crop；结尾再 `convert("RGB")`；24B/px 的内存估算限制了并发 | 内存与 CPU 双限流、在途 ≤2×workers | [代码事实] | 单张导出分段耗时、峰值 RSS、实际 worker 数 | **P**：只在越界区域补边，已是 RGB 时跳过 convert。残差为 **N?（N2，融合裁切/缩放/合成以降低峰值内存）** |
| B5 | 图片导出编排（GUI） | GUI 在 `as_completed` 阻塞；每次导出重建 bird_box_cache；YOLO 在全分辨率上跑且串行 | 线程池 | [代码事实] | 导出期间 GUI 心跳延迟 | **P**：复用预览阶段已有的鸟框（签名一致时）。编排改为 QThread 属于中等改动，单独评审 |
| B6 | GIF：`export_gif`（GUI） | GIF 编码在 GUI 线程；缓存帧 PNG optimize=True 很慢；每个变体都重新解码 PNG；每帧自适应调色板 | 时间轴共享 | [代码事实]；量化耗时 [待验证] | 大批量 GIF 的总时长、峰值 RSS，并区分量化占比 | **P**（缓存帧改为 compress_level=1，编码移出 GUI）。若量化仍占主导，原生量化器（libimagequant，GPLv3，需核对与 AGPL 的兼容性）作为**后续独立选项**，不纳入首批 |
| B7 | 视频帧缓存 manifest | 每帧重写整份 JSON 并 `os.replace`，O(N²) | 原子写 | [代码事实] | 1000 帧导出中 manifest 的累计耗时 | **P**：按时间或帧数批量写入，结束和取消时各写一次（需保持取消恢复语义）|
| B8 | 导出元数据与色彩 | 不写 EXIF 和 ICC | — | [代码事实] | — | **保持**：这是现有行为，A/B 必须保持一致；是否增加属于产品决策，不纳入本计划 |
| C1 | PIL→QImage 转换在三处重复（`preview_panel`、`_browser_core`、`editor_utils`） | 格式各不相同（RGB888 / RGBA8888），都在 GUI 线程转成原生格式 | — | [代码事实] | 转换 ms 与拷贝次数 | **P**：统一成 `app_common/imaging` 适配层，这也是 N 轨唯一的接入缝 |
| — | 渐变行循环、去抖 FFT、焦点框 | O(H) 或小补丁 | — | [代码事实] | — | **保持** |
| — | HEVC/RAW 解马赛克本身 | 已在 libheif/LibRaw 中 | — | [代码事实] | — | **保持**（不自研解码器） |

**三种处理方式的归因原则**：减少拷贝、缓存命中、去掉重复进程或解码、把工作移出 GUI 线程，这些收益都记在 **P 轨**。只有 P 轨完成之后仍然存在、且原生融合才能消除的那部分耗时，才能记到 N 轨名下。

### 3.1 原生候选（N 轨，仅当 G1 通过时启动）

| 候选 | 范围 | 复用面 | 预期收益来源 | 主要风险 |
| --- | --- | --- | --- | --- |
| **N1 `decode_scaled`**（首选） | JPEG 文件或 RAW 内嵌 JPEG 字节 → libjpeg-turbo DCT 缩放解码 → 精缩放 → 方向变换 → 打包为 `RGB32` 显示格式，一次分配，全程释放 GIL | Viewer 缩略图（V5/V6）、Viewer 快速和完整预览（V1/V3 的解码段）、BirdStamp 预览解码 | 融合后少 2–4 次整图拷贝，GUI 线程不再需要格式转换 [待验证] | 与 Pillow LANCZOS 的像素差；截断 JPEG 的行为；方向语义 |
| N2 `compose_frame` | 按需补边的裁切 + 缩放 + 预渲染 RGBA 层合成 → 单一输出缓冲 | BirdStamp 预览与导出 | 降低峰值内存，从而允许更多 worker [待验证] | 文字仍由 Pillow/FreeType 渲染，只迁移合成；排版必须保持一致 |
| N3 GIF 量化 | 调色板量化 | BirdStamp GIF | 待 M0 数据 | 许可；画质差异 |

**明确不做**：重写 JPEG/RAW/HEIF 解码器；重新封装 Pillow 已经暴露的能力并当作成果；GPU 路径（没有证据；若将来考虑，须作为独立选项并单列传输、兼容与部署成本）。

---

## 4. 推荐的最小架构、共享库归属与接口草图

### 4.1 归属与复用（主方案）

```
SuperBirdTools/
├─ native/sbt_imaging/          # 拟新增：C++20 + pybind11 扩展，独立 pyproject（scikit-build-core + CMake）
│   ├─ pyproject.toml, CMakeLists.txt
│   ├─ src/…                    # 只有通用像素能力，无模板/DB/UI 逻辑
│   └─ tests/                   # C++ 单元 + 模糊测试语料
└─ app_common/imaging/          # 拟新增（子模块内，纯 Python）：缓冲契约、后端选择、Python 参考实现
    ├─ __init__.py              # 公共 API：decode_scaled(...)、to_qimage(...)
    ├─ buffer.py                # PixelBuffer 契约（Python dataclass）
    ├─ backend.py               # auto / python / native 解析与诊断
    └─ _python_impl.py          # 现行 Pillow 路径的等价实现（A/B 对照 + 回退）
```

理由：
- `app_common` 是**纯 Python、按源码被 pathex 引用**的独立仓库。把 CMake 构建放进去，每个消费者都得有编译工具链，子模块提交流程也会被编译产物拖累。所以原生源码放在**超级项目**的 `native/`，`app_common/imaging` 只做 `try: import sbt_imaging` 的可选导入。缺失时走 Python 实现，两款应用的行为保持一致。
- 模板、`report.db`、XMP 与 UI 逻辑都不进入原生层。BirdStamp 专属的合成（N2）以通用原语的形式暴露（“把 RGBA 层合成到指定矩形”），模板语义仍留在 `birdstamp`。
- 如果将来要给独立仓库复用，可以把 `native/sbt_imaging` 拆成独立仓库或 wheel。**本计划不做这一步。**

### 4.2 首批 API 草图（只有 N1；P 轨先以 Python 实现同一签名）

```python
# app_common/imaging/__init__.py（草图，非实现）
def decode_scaled(
    source: str | os.PathLike | bytes,     # 文件路径（UTF-8，含中文）或内存 JPEG 字节
    *,
    max_long_edge: int | None,             # None = 原尺寸；>0 = 长边上限（不放大）
    orientation: int | Literal["auto"],    # "auto"=读源自身 EXIF；1..8=调用方指定（RAW 内嵌用源文件方向）
    background_rgb: tuple[int,int,int] = (45, 45, 45),  # 与现有 alpha 合成底色一致
    out_format: Literal["RGB32", "RGB888"] = "RGB32",
    max_source_pixels: int,                # 解码前的尺寸闸门（读取头部后、分配前检查）
    cancel: CancelToken | None = None,     # 协作取消；在各阶段之间及按行块轮询
    backend: Literal["auto", "python", "native"] | None = None,  # None=全局配置
) -> DecodeResult: ...

@dataclass(frozen=True)
class DecodeResult:
    buffer: PixelBuffer
    source_size: tuple[int, int]           # 已按方向换算的原图尺寸（与现有 birdstamp_source_properties 一致）
    orientation_applied: int               # 实际应用的 1..8
    icc_profile: bytes | None              # 仅透传，不参与像素换算（保持现有行为）
    truncated: bool                        # 源数据截断但按 LOAD_TRUNCATED_IMAGES 语义部分解码
    backend_used: Literal["python", "native"]
    timings_ms: Mapping[str, float]        # decode/scale/orient/pack，用于探针

def to_qimage(buf: PixelBuffer) -> "QImage":  # 仅工作线程调用；一次深拷贝，返回自有 QImage
```

**错误**（Python 异常层级，定义在 `app_common/imaging`）：
- `ImagingUnsupported`：格式或特性不支持，例如 CMYK JPEG、12-bit JPEG、算术编码。`auto` 模式下回退到 Python 实现。
- `ImagingDecodeError`：数据损坏且无法部分解码。`auto` 模式回退到 Python，以保持现有容错。
- `ImagingLimitExceeded`：超过尺寸或内存闸门。不回退，按现有“无法预览”处理。
- `ImagingCancelled`：已取消，调用方丢弃结果。
- `ImagingInternalError`：原生内部断言失败。`auto` 模式回退并记 error 日志，`native` 模式直接抛出。

**资源释放**：`PixelBuffer` 持有原生分配，由引用计数释放；不需要显式 `close()`，但提供 `release()` 以便大图尽早归还。原生层不持有任何全局可变状态、线程或文件句柄，调用返回时文件已关闭。

### 4.3 像素缓冲区契约 `PixelBuffer`

| 字段 | 约定 |
| --- | --- |
| `width, height` | >0，`int32` 范围；`width*height*bpp` 用带溢出检查的 64 位乘法计算 |
| `format` | `RGB32`：每像素 4 字节，内存序与 `QImage.Format_RGB32` 一致（小端 B,G,R,0xFF），可直接 `QPixmap.fromImage` 而不做格式转换 [待验证：PyQt6/Qt6 raster 下是否确实免转换]；`RGB888`：3 字节 R,G,B，对应 Pillow `"RGB"` |
| `stride` | 字节，≥ `width*bpp`，按 4 字节对齐；消费者必须按 stride 读取 |
| 位深 | 首批仅 8-bit/通道。16-bit（PSD/TIFF/RAW 解马赛克）不进入 N1 |
| 透明度 | 首批输出不带 alpha：有 alpha 的源按现有语义合成到 `background_rgb`（与 `_qimage_from_pil_image` / `_pil_to_rgb_thumb` 一致）。N2 另行定义预乘 `ARGB32_Premultiplied` |
| 颜色空间 | 标记为“未管理”（`color_managed=False`）；`icc_profile` 原样透传，不做换算 |
| 方向 | `orientation_applied` 记录已应用的变换；缓冲区像素已是显示方向 |
| 所有权 | 由原生层分配并拥有；Python 通过 buffer protocol 暴露只读 `memoryview` |
| 可写性 | 只读（`readonly=1`），防止 NumPy 或 PIL 视图意外修改共享缓冲 |
| 有效期 | 与 `PixelBuffer` Python 对象同寿命。任何视图（memoryview/NumPy/QImage 零拷贝包装）存活期间必须持有该对象的引用；**跨线程交给 GUI 之前必须转换为自有 `QImage`（一次拷贝）** |

### 4.4 各边界的拷贝规则

| 边界 | 是否拷贝 | 条件 |
| --- | --- | --- |
| 文件 → 原生 | 否（原生直接读取 UTF-8 路径；Windows 用 `_wfopen` 或宽字符 API） | 路径编码正确性由测试覆盖中文路径 |
| Python `bytes` → 原生 | 否（`py::buffer` 在调用期间持有引用） | 调用期间不释放源对象 |
| 原生 → Python | 否（buffer protocol，只读） | — |
| Python → PIL | 需要时用 `Image.frombuffer(..., "raw", ...)` 零拷贝 | 仅在 PixelBuffer 存活期间有效；需要长期持有时 `.copy()` |
| Python → NumPy | `np.frombuffer` 零拷贝只读 | 同上 |
| PixelBuffer → QImage | **默认一次深拷贝，在工作线程完成**（`QImage(ptr,…).copy()`） | 零拷贝包装只允许在同一线程、同一作用域内短期使用；PyQt6 的 `QImage(bytes)` 是否持有 Python 引用 [待验证]，在验证之前一律拷贝 |
| QImage → QPixmap | GUI 线程（Qt 要求）；RGB32 时免格式转换 | — |

对照现状：Viewer PIL 路径约 5 份整图缓冲，BirdStamp `pil_to_qpixmap` 约 4 份。目标是“解码缓冲 + 1 次 QImage 拷贝 + QPixmap”。

### 4.5 GIL、线程、内存、背压、取消

- **原生层不创建线程**，也不启用 libjpeg-turbo 以外的内部并行（没有 OpenMP）。并发完全由现有所有者提供：Viewer 的 `BrowserWorkPool` 与 `_FullPreviewLoader`，BirdStamp 的 `EditorPreviewDecodeWorker` 与导出 TPE（≤8）。不新增线程池。
- **GIL**：参数校验和结果对象构造时持有 GIL；打开文件、解码、缩放、打包期间 `py::gil_scoped_release`。回调 Python 时不持有原生锁。
- **取消**：`CancelToken` 是原生原子标志的包装，由 Python 端 `request_token` 或 `requestInterruption()` 转换设置。原生层在阶段之间以及每 N 行扫描轮询，返回 `ImagingCancelled`。过期结果仍由现有 token 或路径校验丢弃，原生层不替代这套机制。
- **内存**：`max_source_pixels` 在读取头部之后、分配之前检查，初值沿用 Pillow `MAX_IMAGE_PIXELS` 的语义（项目未修改它 [代码事实]）。BirdStamp 的 worker 预算继续使用 `resolve_video_render_workers`；N2 通过后再用实测的每像素字节数替换 24B/px 的估算。
- **背压**：沿用现有的在途上限（Viewer 的小批次和优先级；BirdStamp 的 `workers*2`），原生层不排队。

### 4.6 GUI 与 Qt 绑定边界

原生模块**不链接 Qt、不 include Qt 头文件**，只返回普通缓冲区。`QImage` 构造（在工作线程）与 `QPixmap.fromImage`（在 GUI 线程）留在 Python/PyQt6 层，因此不会引入第二套 Qt 运行时，也不会与 PyQt6 捆绑的 Qt 发生 ABI 冲突。

### 4.7 校验、异常、退出与诊断

- 输入校验：路径存在性和类型、`max_long_edge` 范围、方向取值 1..8、尺寸溢出、头部声明尺寸与实际扫描行数的一致性。
- 原生异常：全部在 pybind11 边界映射为 4.2 中的 Python 异常，不让 C++ 异常越过边界。
- **段错误或进程崩溃无法被 Python `try/except` 恢复。** 缓解手段是：输入闸门；CI 中的 ASan/UBSan 构建；用损坏 JPEG 语料做模糊测试（libFuzzer 或确定性语料）；默认后端在 M4 通过之前保持 `python`。本计划不做子进程隔离（成本高，且没有证据表明需要）。
- 退出：原生层无全局资源，退出时无需清理；现有 ExifTool stay-open 的关闭流程不变。
- 诊断：`app_common.imaging.backend.describe()` 返回模块路径、版本、构建配置（编译器、CMake 构建类型、SIMD 基线、libjpeg-turbo 版本）、Python ABI 标签和加载失败原因。启动时如果 perf probe 开启，则记一行 `[imaging.backend]`。

### 4.8 后端模式与配置入口

沿用 `perf_probe._ENV_VARS` 的多前缀模式：
- 环境变量 `SuperViewer_IMAGING_BACKEND` / `BIRDSTAMP_IMAGING_BACKEND`，取值 `auto|python|native`，按 `perf_probe` 的顺序解析。
- 可选用户选项 `imaging_backend`，放在 `app_common/superviewer_user_options.py`（与 `perf_probes_enabled` 同一机制）。M4 之前不在 UI 中暴露。
- **默认值在 M4 门槛通过之前为 `python`**，之后才考虑改为 `auto`。

| 情形 | `auto` | `python` | `native` |
| --- | --- | --- | --- |
| 扩展缺失 / ImportError | 用 Python；启动时记 info 一次 | Python | **抛错并记 error**（不静默回退） |
| ABI 或架构不匹配（导入时报错） | 同上，并在日志中记录 `sys.version`、平台标签和模块路径 | Python | 抛错 |
| 不支持的格式或特性 | 当次回退 Python，按类型聚合计数日志 | Python | 抛 `ImagingUnsupported` |
| 可恢复的解码错误 | 当次回退 Python（保持部分解码容错） | Python | 抛错 |
| 超限 / 取消 | 与 Python 后端相同的语义 | — | 相同 |

测试中强制 `native` 模式时，任何回退都必须让测试失败。

---

## 5. M0–M4 任务表与依赖顺序

依赖顺序：`M0-1 → M0-2 → M0-3 → M0-4 → M0-5(G0)` → P 轨（P-1…P-11，可并行评审；P-10 依赖 P-1）→ `M0-6(G1)` → 通过才进入 `M1 → M2(G2) → M3 → M4(G3)`。
所有改动遵守 AGENTS 的“每轮一个分支一个 worktree”、先提交 `app_common` 再提交 gitlink 的规则。

### M0：现状、基线、样本与选型

| ID | 阶段 | 目标 | 实际/拟新增文件 | 依赖 | 接口变化 | 测试命令/步骤 | 验收标准 | 回退方式 | 复杂度 | 证据置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M0-1 | M0 | 可重复的离线基准脚本与环境记录（见第 8 节）。**状态：已完成首轮**（Linux 容器，见 7.5）| `benchmark/README.md`、`benchmark/bench_imaging.py`、`benchmark/env_report.py`、`benchmark/results/` | 无 | 无（只调用现有函数） | `.venv` 解释器运行 `bench_imaging.py --manifest <外部样本清单> --out <临时目录>` | 见第 8 节 | 删除 `benchmark/` | M | 高 |
| M0-2 | M0 | 补齐缺失的探针：canvas paint 耗时、GUI 事件循环心跳延迟、导出/GIF/视频/模板叠加分段，以及 RAW 提取的进程数与耗时 | `app_common/preview_canvas/canvas.py`、`app_common/thumb_stream.py`、`app_common/perf_probe.py`（加心跳辅助）、`birdstamp/export_stage/core.py`、`birdstamp/gif_export.py`、`gui/editor_template.py`，全部走 `perf_probe` 门控 | M0-1 | 无（探针关闭时零行为变化） | `QT_QPA_PLATFORM=offscreen` 跑 `test_preview_canvas_hot_path.py`、`test_image_pipeline.py`、`test_gif_export.py`、`test_video_export.py`；关闭探针时比对日志为空 | 探针关闭时行为和输出字节与改动前一致；开启时每段都有可解析的单行日志 | revert 单个 commit | M | 高 |
| M0-3 | M0 | 建立样本集（在仓库外，记录 SHA-256）：高像素 JPEG（24/45/61MP）、ARW（含 JpgFromRaw 与仅有 PreviewImage 两类）、其他 RAW（CR3/NEF 各至少 1 个，如有）、HIF 24/45MP、PNG/TIFF >40MP、PSD 8/16-bit、渐进式 JPEG、方向 1..8、嵌入 ICC（sRGB/P3/AdobeRGB）、截断或损坏文件、中文路径和目录名、含 `.superpicky/report.db` 的根目录副本 | `benchmark/`（用户样本）；清单文件按需补充 | M0-1 | 无 | 清单校验脚本逐项检查存在性与哈希 | 每类样本“已覆盖”或明确标注“未覆盖” | — | S | 中（取决于用户能提供的样本） |
| M0-4 | M0 | 在 Windows x64 与 macOS arm64 各采集一次基线：冷/热缓存 × 每个场景（见 6.1）| 结果放仓库外；仓库内只提交 `benchmark/results/<日期>-<平台>.json` 摘要（可选，需用户同意）| M0-2、M0-3 | 无 | 按 6.1 协议执行 | 每个指标都有 P50/P95、峰值 RSS 和样本标识；未跑到的平台标“未测” | — | M | — |
| M0-5 (G0) | M0 | 热点排序与 P 轨优先级确认 | 本文 §3 增补“已测结论”列 | M0-4 | 无 | 评审 | 每个 P 任务都有“基线值 + 目标阈值”；无收益的 P 任务删除 | — | S | — |
| M0-6 (G1) | M0 | 原生可行性判定：在 P 轨完成后的新基线上，对 N1 目标段做原型微基准（在仓库外的一次性原型，或临时分支，不合并）| 临时目录 | P 轨中相关任务（P-1、P-5、P-6）| 无 | 同样本、同输出尺寸、同滤波质量档，对比“P 轨版 vs 原型” | 满足 G1（见 6.3）才启动 M1；否则把 N 轨标记为“暂停”，并在本文记录数据 | 不启动 M1 | M | — |

### P 轨：仅优化现有 Python 调用与数据流（每项独立评审、独立回退）

| ID | 阶段 | 目标 | 实际/拟新增文件 | 依赖 | 接口变化 | 测试命令/步骤 | 验收标准 | 回退方式 | 复杂度 | 证据置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P-1 | P | RAW 内嵌 JPEG 进程内或会话内提取：优先复用当前线程的 stay-open 会话，一次命令请求多个 tag，**优先 LibRaw `rawpy.extract_thumb()`**（容器实测 0.6 ms 对 ExifTool 进程 158 ms，取得同一张 JpgFromRaw，见 7.5），校验尺寸不小于 ExifTool 候选，失败时再走 stay-open 会话；按 (path,size,mtime) 缓存字节与方向 | `app_common/thumb_stream.py`、`app_common/exif_io/exiftool_runner.py`（只读调用）、`SuperViewer/superviewer/focus_preview_loader.py`（方向缓存）| M0-5 | `get_raw_preview_jpeg` 签名不变 | 新增单测：每个 RAW 的进程启动次数 ≤1（或 0），分辨率优先级保持“高清相机预览 > piexif 小图”；`test_fast_preview_policy.py`、`test_preview_panel_policy.py`、`test_thumb_stream.py` | RAW 点击 load_ms P50/P95 达到 G0 设定的目标；选中的内嵌预览分辨率与改动前一致（逐样本比对宽高） | revert | M | 高（进程数）/ 待验证（耗时）|
| P-2 | P | 预览缓冲拷贝收敛：去掉 QImageReader 之后多余的 `.copy()`；工作线程直接产出 `RGB32`（`tobytes("raw","BGRX")` 或 `convertToFormat`，二选一以实测为准）；统一为 `app_common/imaging.to_qimage` | `SuperViewer/superviewer/preview_panel.py`、`app_common/file_browser/_browser_core.py`、`birdstamp/gui/editor_utils.py`，拟新增 `app_common/imaging/` | M0-5 | 新增内部 API，旧函数保留为薄包装 | 像素逐一比对（RGB 与改动前完全相同）；预览、缩略图、BirdStamp 预览相关测试 | GUI 线程 `QPixmap.fromImage` 耗时下降；像素完全一致 | revert | M | 中 |
| P-3 | P | 缩略图解码顺序与重复解码：`thumbnail` 放在 `exif_transpose` 之前（对齐 BirdStamp 已有做法）；RAW 内嵌 JPEG 使用 `draft`；JPEG `draft` 的方框按宽高比计算（现为 `(size,size)`，横图 2048 档因此失去 DCT 缩放，见 7.5）；RAW ≤1024 档优先使用 PreviewImage；渐进式 JPEG 的最终帧不再二次全解码（或仅在视口可见时使用渐进路径）；内存缓存 put/get 深拷贝改为只读共享（QImage 隐式共享）| `app_common/thumb_stream.py`、`app_common/file_browser/_thumbnail.py`、`_browser_core.py` | P-2 | 无 | 缩略图像素与现状比对：定义容差（DCT 缩放 + LANCZOS 顺序变化导致的差异，逐通道 max≤2、≥99.5% 像素 ≤1，另附目视抽查）；`test_thumb_stream.py`、`test_thumbnail_memory_cache.py`（字节计量不变）| 缩略图吞吐和 decode p95 达到目标；方向 1..8 全部正确 | revert | M | 中 |
| P-4 | P | 画布绘制：棋盘格改为缓存的 `QBrush` 纹理（不透明图跳过）；按缩放级别缓存缩放后的 pixmap，或只绘制可见源矩形；平移时复用缓存 | `app_common/preview_canvas/canvas.py` | M0-2 | 无 | `test_preview_canvas_hot_path.py`；构图网格与焦点叠加回归（AGENTS 受保护流程）；截屏比对（offscreen 渲染到 QImage）| paint ms p95 与拖拽 FPS 达到目标；叠加导出路径输出不变 | revert | M | 中 |
| P-5 | P | BirdStamp 预览渲染：`load_font` 的 LRU（路径、字号）、模板 payload 按 (path,mtime) 缓存、渐变只合成 banner 区域、减少 RGBA↔RGB 往返 | `birdstamp/render/typography.py`、`gui/editor_renderer.py`、`gui/editor_template.py` | M0-5 | 无 | `test_template_text_scale.py`、`test_template_overlay_banner_gradient.py`、`test_template_overlay_unicode_fallback.py`、`test_image_pipeline.py`；预览与导出像素比对（应完全一致）| `render_preview` P50/P95 达到目标；输出像素完全相同 | revert | S–M | 高（缓存缺失）/ 待验证（占比）|
| P-6 | P | 导出解码与预计算：`_decode_standard` 去掉多余拷贝；统一裁切或去抖预计算移出 GUI 线程；在内存预算内复用解码结果，避免每图两次全解码；帧缓存全命中时跳过不必要的解码（前提是签名语义不变）| `birdstamp/decoders/image_decoder.py`、`birdstamp/export_stage/core.py`、`gui/editor.py` | M0-5 | 无 | `test_video_export_uniform_crop.py`、`test_video_export_dejitter.py`、`test_video_export.py`、`test_gif_export.py`；输出逐字节比对（PNG）| 批量导出总时长、峰值 RSS 达标；输出与改动前逐字节一致 | revert | M | 高（重复解码）|
| P-7 | P | 导出管线的整图拷贝：pad 只在越界时对裁切区域补边；已是 RGB 时跳过 `convert("RGB")`；复用预览阶段的鸟框（签名一致时）| `birdstamp/gui/editor_core.py`、`export_stage/core.py`、`gui/editor_exporter.py` | P-6 | 无 | `test_crop_coordinate_regressions.py`、`test_crop_*`、`test_image_pipeline.py`；逐字节比对 | 单张导出峰值 RSS 下降；输出一致 | revert | M | 中 |
| P-8 | P | GIF 与视频编排：缓存帧 PNG 改为 `compress_level=1`；GIF 编码移出 GUI 线程（复用 VideoExportWorker 模式）；manifest 批量写入，并保持取消恢复语义 | `birdstamp/gif_export.py`、`gui/editor_exporter.py`、`export_frame_cache.py`、`export_stage/core.py` | M0-5 | 无 | `test_gif_export.py`、`test_gif_timing.py`、`test_video_export_cleanup.py`、`test_editor_video_worker.py`；取消恢复用例 | GIF/视频总时长达标；GIF 帧与时间轴逐帧一致；取消后保留目录语义不变 | revert | M | 高（O(N²)/GUI 线程）|
| P-9 | P | 模板对话框预览：加去抖并改用预览尺寸源图 | `birdstamp/gui/editor_template_dialog.py` | M0-5 | 无 | 模板编辑相关测试 + 手动连续调参 | 无卡顿；保存的模板 JSON 不变 | revert | S | 高 |

说明：
| P-10 | P | **RAW 普通点击改为两段式**（用户已确认，2026-09-26）：`set_image` 不再在 GUI 线程同步调用 `_load_raw_embedded_preview_qimage`。先显示当前档位缓存（`_cached_quick_preview_pixmap`，只用精确档位）；未命中时显示“正在加载预览”占位，与大 HIF 一致，不在 GUI 线程做任何 RAW 提取。然后由现有 `_FullPreviewLoader`（单任务 + 最新 pending + token 校验）提取内嵌高清 JPEG 并替换。内嵌预览的优先级不变（JpgFromRaw/PreviewImage > rawpy > piexif 小图），仍然不解马赛克。`source_pixmap_for_path()` 与 `full_preview_ready` 只在内嵌预览到达后成立 | `SuperViewer/superviewer/preview_panel.py`；测试 `SuperViewer/tests/test_preview_panel_policy.py`（新增：RAW 点击时 GUI 线程零 RAW 提取调用、缓存命中先显示、内嵌到达后替换、快速连续点击只处理最新请求）；同步更新 `AGENTS.md` 的“Protected SuperViewer Preview Loading Flow”、`ai_rules/AI_CODING_RULES.md` §10/§15 与 `SuperViewer/docs/ARCHITECTURE.md` §3 表格 | P-1（先让提取本身变快，两段式的第二段才短）| `set_image` 签名不变；RAW 行为从“同步直接完整”变为“档位 → 异步完整” | `test_preview_panel_policy.py`、`test_fast_preview_policy.py`、`test_directory_selection_responsiveness.py`、`app_common/tests/test_file_browser_key_navigation.py`；手动或日志检查 RAW 点击、按住方向键、释放后单次提交、焦点框在完整预览到达后出现 | RAW 点击时 `set_image` 的 load_ms P95 与小 JPEG 同一量级（目标值由 M0 基线确定）；GUI 心跳 max 不再出现 RAW 提取尖峰；内嵌预览到达时间不比现状差；最终显示的分辨率与现状逐样本相同 | revert；或把 RAW 临时加回同步分支（单处改动）| M | 高（代码事实：当前同步）|
| P-11 | P | **HEIF 独立同步阈值**：新增 `SuperViewer_SYNC_FULL_PREVIEW_HEIF_MAX_MP`，与 JPEG 的 `SuperViewer_SYNC_FULL_PREVIEW_MAX_MP`（默认 40）分开。建议默认 **4 MP**（理由见 7.3），M0 有数据后按“GUI 同步解码 P95 ≤100 ms”校准。超过阈值的 HEIF 沿用现有大 HIF 路径（精确档位缓存 → 占位 → 工作线程完整解码），不新增路径 | `SuperViewer/superviewer/preview_panel.py`（`_should_load_full_preview_sync` 按扩展名取阈值）；测试 `test_directory_selection_responsiveness.py::test_small_hif_keeps_synchronous_full_preview` 改为以新阈值参数化，另加“24 MP HIF 默认异步”用例；同步更新 `AGENTS.md`、`SuperViewer/docs/ARCHITECTURE.md` 中“默认 40 MP”的 HEIF 描述 | M0-4（校准默认值；样本到位前先用 4 MP）| 新增环境变量，原变量语义只对非 HEIF 生效 | 同上 + `test_preview_panel_policy.py` | 常见相机 HIF（24–61 MP）点击不再在 GUI 线程解码 HEVC；≤阈值的小 HIF 仍同步完整显示；JPEG 行为不变 | revert；或把 HEIF 阈值设为 40 恢复原行为（无需改代码）| S | 中（阈值默认值待验证）|

说明：P-10、P-11 改变的是受保护流程，用户已于 2026-09-26 确认方向。实施时必须在同一提交中更新 AGENTS.md 与架构文档对应条款，否则会与现有规则冲突。
- 视频由 ffmpeg 读 PNG 序列的设计服务于帧复用和取消恢复，**保持**。

### M1：最小共享扩展与后端开关（仅当 G1 通过）

| ID | 阶段 | 目标 | 实际/拟新增文件 | 依赖 | 接口变化 | 测试命令/步骤 | 验收标准 | 回退方式 | 复杂度 | 证据置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M1-1 | M1 | 构建骨架：scikit-build-core + CMake + pybind11；只含 `version()`、`describe()` 和 `PixelBuffer` 类型；可以 `pip install -e native/sbt_imaging` 进开发 `.venv` | 拟新增 `native/sbt_imaging/{pyproject.toml,CMakeLists.txt,src/module.cpp,src/pixel_buffer.hpp}` | G1 | 新模块 | 开发态导入；C++ 单测（Catch2 或 doctest，许可需核对）| Windows x64 与 macOS arm64 本地都能构建并导入 | 删除目录 | M | — |
| M1-2 | M1 | `app_common/imaging` 适配层：后端解析、异常层级、Python 参考实现（即 P-2 的实现），`describe()` 诊断 | `app_common/imaging/*` | P-2、M1-1 | 新 API（P-2 已引入）| 强制 `python` / `native` / `auto` 三模式的单测；扩展缺失与 ABI 错误的模拟 | 缺失扩展时 `auto` 可用，`native` 抛错；日志满足 4.8 | revert | M | — |
| M1-3 | M1 | 依赖与许可：libjpeg-turbo 静态链接（CMake FetchContent 固定到 tag 与哈希），更新 `THIRD_PARTY_NOTICES.md`；SIMD 策略按 6.2 | `native/sbt_imaging/cmake/*`、`THIRD_PARTY_NOTICES.md` | M1-1 | 无 | 许可清单审阅；`objdump`/`otool` 检查链接依赖 | 发布包中只多出一个扩展文件，没有额外的动态库依赖（或已被正确收集）| revert | S | — |
| M1-4 | M1 | CI 构建验证：在现有 workflow 中，于 `init_dev.py` 之后增加扩展构建与导入检查步骤（不改变 `build_all.* --clean` 的调用）| `.github/workflows/build-release.yml`、`init_dev.py`（增加可选的 `--with-native`）| M1-1..3 | 无 | 手动触发 workflow_dispatch | 两个平台 job 都通过导入与 `describe()` 检查 | revert | M | — |

### M2：首个热点（N1）端到端接入与 A/B（Viewer 先行）

| ID | 阶段 | 目标 | 实际/拟新增文件 | 依赖 | 接口变化 | 测试命令/步骤 | 验收标准 | 回退方式 | 复杂度 | 证据置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M2-1 | M2 | 原生 `decode_scaled`：JPEG 文件或字节 → DCT 缩放 → 精缩放 → 方向 → RGB32；支持截断、CMYK 判定为 Unsupported、取消、尺寸闸门 | `native/sbt_imaging/src/decode_scaled.cpp` 等 | M1 | `app_common.imaging.decode_scaled(backend="native")` | C++ 单测、模糊语料、ASan 构建；与 Python 后端的像素比对 | 6.4 的正确性矩阵全部通过 | 后端开关切到 `python` | L | — |
| M2-2 | M2 | Viewer 接入：`thumb_stream` 的 JPEG 与 RAW 内嵌路径、`preview_panel` 的快速预览与 `_FullPreviewLoader` 的 JPEG 路径，都改为调用 `app_common.imaging` | `app_common/thumb_stream.py`、`SuperViewer/superviewer/preview_panel.py` | M2-1 | 无（内部）| AGENTS §15 全部相关回归 + 受保护预览流程手动检查（小图、大图、RAW、按住方向键、释放）| 在 `python` 模式下与 P 轨版逐像素一致；在 `native` 模式下满足 G2 | 开关 | M | — |
| M2-3 (G2) | M2 | 三方 A/B：现有版（M0 基线）vs P 轨版 vs 原生版，在同样本、同分辨率、同缓存条件下对比 | `benchmark/` 结果 | M2-2 | 无 | 6.1 协议 | 满足 G2；未满足则**停止扩大迁移**，保留开关默认 `python`，评估是否移除原生代码 | — | M | — |

### M3：BirdStamp 复用与双应用回归

| ID | 阶段 | 目标 | 实际/拟新增文件 | 依赖 | 接口变化 | 测试命令/步骤 | 验收标准 | 回退方式 | 复杂度 | 证据置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M3-1 | M3 | BirdStamp 预览解码（`decode_image_for_preview` 的 JPEG 与 RAW 内嵌分支）复用 N1 | `birdstamp/decoders/image_decoder.py` | G2 | 无 | `test_decode_image_for_preview.py`、`test_raw_decoder.py`、`test_editor_selection_preview.py`；`source_size` 与 `birdstamp_source_properties` 一致 | 预览尺寸、原尺寸、裁切坐标完全一致 | 开关 | S–M | — |
| M3-2 | M3 | 双应用资源释放与并发：长时间切图和批量导出下的 RSS 曲线、线程数、句柄数；关闭流程 | benchmarks 场景 | M3-1 | 无 | 30 分钟浸泡测试（每平台一次）；`closeEvent` 相关测试 | 无单调增长；关闭时无悬挂线程 | 开关 | M | — |
| M3-3 | M3 | （可选，需单独过 G1）N2 `compose_frame` 评估 | 待定 | M3-2 | 待定 | 同 G1 | 同 G1 | 不启动 | L | 低 |

### M4：发布包验证与默认启用条件

| ID | 阶段 | 目标 | 实际/拟新增文件 | 依赖 | 接口变化 | 测试命令/步骤 | 验收标准 | 回退方式 | 复杂度 | 证据置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M4-1 | M4 | PyInstaller 收集：各 spec 与 merged spec 显式加入 `sbt_imaging` 的 hiddenimport 与 binary；macOS spec 用 `upx_exclude` 排除扩展（或经评审把 `upx` 改为 False）| `SuperViewer/*.spec`、`SuperBirdStamp/*.spec`、`build_all_win_merged.spec` | M3 | 无 | `build_all.bat --clean`、`build_all.sh --clean` | 包内扩展存在，`describe()` 从冻结路径加载 | revert spec | M | — |
| M4-2 | M4 | 发布包 smoke：启动两款应用，打开 JPEG、ARW、HIF、中文路径样本，按住方向键浏览，BirdStamp 导出 1 张 JPG、1 个 GIF；日志中出现 `[imaging.backend] native` | 拟新增 `build_tools/tests` 中的冻结包导入检查（仅导入与 `describe`）| M4-1 | 无 | 两平台各执行一次；未执行的平台标“未验证” | 见 6.5 | 回退到上一个 Release | M | — |
| M4-3 (G3) | M4 | 默认启用：两个平台的发布包都通过 6.5，且 G2 在两个平台都成立时，默认值改为 `auto` | `app_common/imaging/backend.py` | M4-2 | 默认值变更 | — | 见 6.3 G3 | 默认改回 `python`（单行改动）| S | — |
| M4-4 | M4 | 诊断文档：开发态可用而发布包失效时的排查路径、后端开关说明；更新两份 ARCHITECTURE 与 AGENTS 的定位表 | `docs/`、`*/docs/ARCHITECTURE.md`、`AGENTS.md` | M4-2 | 无 | 文档评审 | 与实际配置一致 | revert | S | — |

---

## 6. 性能、正确性与双平台发布包验收矩阵

### 6.1 基准协议

**环境记录（每次运行自动写入）**：CPU 型号与核数（物理/逻辑）、内存、磁盘类型（SSD/NVMe/HDD/网络盘）、OS 版本、Python 版本与 ABI、`pip freeze` 摘要（Pillow、PyQt6/Qt、rawpy/LibRaw、pillow-heif/libheif、numpy）、ExifTool 版本、后端模式、`sbt_imaging.describe()`、线程数配置（`SuperViewer_THUMB_WORKERS` 等）、电源模式、样本清单哈希。

**冷/热缓存**：
- 热：同一进程内先预热 1 轮再测。
- 冷（应用缓存）：删除临时副本目录下的 `.superpicky/thumb_cache` 以及本地应用缓存副本。**只作用于样本副本，不触碰用户照片库。**
- 冷（OS 页缓存）：macOS 用 `sudo purge`；Windows 没有可靠的无特权方法，使用“每轮复制到新的临时目录”替代，并在报告中注明。
- 两种条件分别报告，不混合。

**统计**：每个场景预热 1 轮 + 至少 10 次重复（交互场景至少 30 次采样）；报告 P50/P95/max 和样本数；在同一机器、同一时段交替运行各版本（ABAB）以抵消漂移；只剔除有明确记录的异常（例如后台杀毒扫描），不按数值剔除。**峰值 RSS** 用 `psutil` 以固定间隔采样整个进程（覆盖原生分配），或读取 OS 峰值（Windows `PeakWorkingSetSize`，macOS `ru_maxrss`）；不用 `tracemalloc` 代替。

**场景与指标**：

| 应用 | 场景 | 指标 | 测量方法 |
| --- | --- | --- | --- |
| Viewer | 首张可见预览 | 点击到 `set_source_pixmap` 的 ms | 现有 `[PERF][image_switch]` 探针 |
| Viewer | 完整质量就绪 | 点击到 `full_preview_ready` 的 ms | `[preview.full]` 加上点击时间戳 |
| Viewer | 连续切图（按住方向键，10/20/30 FPS 设定）| 实际 fps、丢帧率、avg/max | `[preview.fast.summary]` |
| Viewer | 缩放与拖拽 | paint ms P50/P95、FPS | M0-2 新增的 paint 探针 |
| Viewer | 缩略图吞吐（1000 张 JPEG / 300 张 ARW / 300 张 HIF 目录）| 张/秒、decode_ms p95、视口首屏完成时间 | `SuperViewer_THUMB_PROFILE`、pool snapshot |
| Viewer | GUI 事件循环阻塞 | 心跳延迟 P95/max（16ms 定时器）| M0-2 心跳探针 |
| BirdStamp | 参数变更后预览延迟 | 最后一次 valueChanged 到 `refresh_label` 的 ms（扣除 250ms 去抖后单列）| 现有 `render_preview.*` span |
| BirdStamp | 单张完整导出（JPEG 45MP / ARW）| 总 ms 与分段 | M0-2 导出探针 |
| BirdStamp | 批量吞吐（50/200 张，含统一裁切开与关）| 总时长、张/秒、峰值 RSS、实际 worker 数 | 同上 + RSS 采样 |
| BirdStamp | GIF / 视频 | 总时长、各阶段占比、峰值 RSS | 同上 |
| 公共 | 解码/转换/缩放分段 | 各段 ms、拷贝次数、缓存命中率、队列等待 | `bench_imaging.py` + pool snapshot |

**对照组**：“现有版”（M0 基线的 commit）、“只优化数据流版”（P 轨完成的 commit）、“原生核心版”（同一 commit，后端设为 `native`）。三者使用相同的输出分辨率、滤波质量档、预览策略和缓存条件，并且各自预热。

### 6.2 SIMD 与分发基线

- 编译基线：Windows x64 为 `x86-64` 基线（SSE2，**不加** `/arch:AVX2`）；macOS arm64 为 `armv8-a`（NEON 天然可用）。**禁止 `-march=native`**。macOS 部署目标设为 `12.0`，与 BirdStamp `LSMinimumSystemVersion` 一致（`SuperViewer_mac.spec` 的 `info_plist` 只设置了版本号，没有 `LSMinimumSystemVersion` [代码事实]，需要确认 SuperViewer 实际支持的最低 macOS 版本）。
- libjpeg-turbo 自带运行时 CPU 检测（x86 上的 SSE2/AVX2 分派，arm64 上的 NEON）。自研缩放或打包代码首批使用可移植标量实现 + 编译器自动向量化；只有 M2 数据表明缩放段是剩余瓶颈时，才评估 Highway（Apache-2.0）的运行时分派。

### 6.3 门槛（建议值；M0-5 根据基线噪声最终确定）

- **G0（P 任务是否值得做）**：该场景基线 P95 超过交互预算（首张可见预览 >100ms、paint >16ms、心跳 max >100ms、预览渲染 >150ms），或批量场景有明确的重复工作。否则删除该 P 任务。
- **G1（是否启动原生）**，必须同时满足：
  1. 在 P 轨完成后的新基线上，N1 可覆盖的段（解码 + 缩放 + 方向 + 打包）占目标场景 P50 端到端时间的 ≥40%；
  2. 一次性原型在相同样本上对该段取得 ≥1.5× 的 P50 加速，或 ≥30% 的峰值 RSS 下降；
  3. 按占比推算，端到端 P50 或 P95 可改善 ≥20%，且超过测量噪声的 3 倍。
- **G2（原生是否保留并扩大）**：在 Windows x64 与 macOS arm64 上都满足 G1-3 的端到端改善，6.4 正确性全部通过，峰值 RSS 不上升，并且在 native 模式下强制测试零回退。
- **G3（默认启用）**：G2 成立、两平台发布包 smoke（6.5）通过、至少一个 Release 周期内 `auto` 模式没有用户可见的回归。
- **回退条件（任何阶段）**：任何画质或元数据不一致、任一平台发布包无法加载扩展、或出现原生崩溃报告，都立即把默认值切回 `python`；在根因修复之前不继续扩大范围。

### 6.4 正确性矩阵

| 维度 | 方法 | 容差 |
| --- | --- | --- |
| 尺寸、方向、裁切坐标 | 方向 1..8 × JPEG/HIF/RAW 内嵌；比较 `source_size`、输出尺寸、BirdStamp 裁切框换算 | **完全一致** |
| 像素（确定性路径：P 轨的拷贝收敛、合成、PNG 导出）| 逐像素或逐字节 | **完全一致** |
| 像素（缩放算法可能变化：P-3、N1）| 逐通道绝对差直方图 | max ≤2，≥99.5% 像素 ≤1，另报告 PSNR；**不能只用整体 SSIM 判定**；另附边缘和高频区域的裁块目视对比 |
| 有损编码输出（JPEG 导出）| 编码参数完全一致；解码后像素差 | 输入一致时字节应相同；输入允许 N1 容差时，单独报告编码后的差异 |
| 颜色与位深 | 嵌入 sRGB/P3/AdobeRGB 的样本；16-bit PSD/TIFF | 与现状一致：ICC 不参与换算；16-bit 不进入 N1（走 Python）|
| 透明度 | RGBA PNG、带 alpha 的 PSD、P 模式 GIF 源 | 背景 (45,45,45) 合成结果一致 |
| 截断或损坏文件 | 截断 50%/90% 的 JPEG、损坏的 RAW 内嵌 | 与 `LOAD_TRUNCATED_IMAGES` 语义一致（部分显示）；不崩溃 |
| 字体与模板排版 | 全部内置模板 × 中文/英文/数字 × 文本缩放 25%/100%/300% | 与现状逐字节一致（N 轨不涉及文字渲染）|
| EXIF/XMP/中文字段 | 在样本**副本**上执行标题、描述、标签、评级写入，再读回（AGENTS 要求）| 完全一致；不写原图、不写 `report.db` |
| `report.db` 兼容 | AGENTS §15 的根目录、子目录、stale path、actual-path repair 用例 | 行为不变；数据库文件哈希不变 |
| 交互（受保护流程）| 小图同步、大图两段式、RAW 内嵌优先、按住方向键只用快帧、释放后单次提交 | 与 AGENTS 描述完全一致 |

### 6.5 双平台发布包验收

| 检查项 | Windows x64（merged spec）| macOS arm64（build_all.sh + dedupe）|
| --- | --- | --- |
| 扩展文件已收集 | `dist/SuperViewer/_internal/…/sbt_imaging*.pyd`；MERGE 之后 BirdStamp 能引用到 | 两个 `.app/Contents/Frameworks` 或 `Resources` 中都有 `.so`；dedupe 之后仍可加载 |
| 运行库与搜索路径 | MSVC 运行库：静态链接 `/MT`，或确认与 PyQt6/Torch 使用的 `vcruntime140` 共存（参考 `pyinstaller_bootstrap` 已有的 MSVC 冲突经验）| `otool -L` 无 `/usr/local`/Homebrew 绝对路径；`@rpath`/`@loader_path` 正确 |
| UPX | 已是 `upx=False` | `SuperViewer_mac.spec` 为 `upx=True`：必须 `upx_exclude` 扩展，或确认构建机没有 upx；对扩展做 `codesign --verify` 检查（ad-hoc 签名在 arm64 上是必需的）|
| 启动 smoke | 两款应用启动，`[imaging.backend]` 日志正确 | 同左 |
| 图像 smoke | JPEG/ARW/HIF/中文路径预览；BirdStamp 导出 JPG/GIF | 同左 |
| 未执行 | 必须标“未验证”，不能用另一平台的结果代替 | 同左 |

**“开发态可用、发布包失效”的排查路径**：`describe()` 输出模块路径 → 检查冻结包内文件是否存在 → Windows 用 Dependencies 或 `dumpbin /dependents`、macOS 用 `otool -L` 检查动态依赖 → 核对 ABI 标签与包内 Python 版本 → 检查 UPX 和签名 → 用 `native` 强制模式启动，拿到原始 ImportError。

---

## 7. 风险、回退方案与暂不纳入范围

### 7.1 主要风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 原生收益在 P 轨之后不足 | 投入浪费 | G1 前置；原型放在仓库外；G1 不通过时 N 轨停止（预期内的结果）|
| 缩放算法差异导致画质变化 | 违反硬约束 | 6.4 容差和目视抽查；必要时移植 Pillow 的重采样实现（HPND 许可，需保留声明），换取逐像素一致 |
| 原生崩溃 | 进程退出，Python 无法捕获 | 输入闸门、ASan、模糊测试；默认 `python` 直到 G3；崩溃报告即回退 |
| 截断 JPEG 行为不一致 | 损坏文件由“部分显示”变为“无法预览” | 专项语料；`auto` 模式回退 Python |
| 新增构建链使 CI 与开发环境变复杂 | 贡献者门槛 | 扩展可选；缺失时自动使用 Python；`init_dev.py --with-native` 为显式开关 |
| macOS UPX 或签名破坏 `.so` | 发布包加载失败 | 6.5 专项检查 |
| Windows MSVC 运行库冲突（已有先例）| 发布包崩溃 | 静态运行库或共存验证；冻结包导入测试 |
| 依赖未锁定导致基线漂移 | A/B 不可比 | 每次记录 `pip freeze`；A/B 在同一环境内交替运行 |
| P 轨改动触及受保护流程 | 交互回归 | 每个 P 任务附带 AGENTS §15 与受保护流程回归；阈值类策略不擅自改动 |

### 7.2 回退方案

- P 轨：每项一个 commit，可以单独 revert。
- N 轨：运行时开关（环境变量或用户选项）立即切回 `python`。默认值改动（M4-3）是单行变更，可以单独 revert。
- 发布：保留上一个 Release 资产（现有 workflow 不会覆盖已有资产）。

### 7.3 用户决策记录（2026-09-26）

1. **RAW 普通点击改为两段式：已确认。** 先显示档位缓存，再异步替换为内嵌高清预览，对应 P-10。
2. **HEIF 同步阈值与 JPEG 分开：采纳推荐。** 对应 P-11，推荐理由：
   - JPEG 的 QImageReader 解码由 libjpeg-turbo 完成，40 MP 同步可以接受；HEVC 解码单位像素成本高得多。复审报告测得 49.8 MP HIF 在 GUI 线程约 1.5 s，按此推算约 30 ms/MP [待验证]，40 MP 阈值意味着常见的 24–33 MP 相机 HIF 仍可能在 GUI 线程卡住 0.7–1 s。
   - 两者共用一个阈值，只能在“JPEG 过度异步”和“HEIF 卡顿”之间二选一。分开后 JPEG 行为完全不变，HEIF 默认走已有的大 HIF 异步路径，不新增代码路径。
   - 默认 4 MP：按 100 ms 的交互预算和上面的推算得出，只保留缩小导出的小 HIF 同步显示。这个值是临时的，M0 样本到位后按实测 P95 校准。设为 40 即可恢复原行为，回退成本为零。
3. **真实样本：已部分到位（2026-09-26）。** 用户在 `main` 的 `04bd8c1` 提交了 `benchmark/` 下的 JPG/ARW/HIF 各一张（JPG 走 Git LFS）。M0-1 已据此在 Linux 云容器跑出第一份结果，见 [2026-09-26 基线](../../benchmark/results/2026-09-26-linux-container.md)。影响：
   - 目标平台（Windows x64 / macOS arm64）的基线（M0-4）仍需在用户机器上运行，G0/G1/G2/G3 的数值门槛以目标平台数据为准。
   - 仍未覆盖的样本类别见结果文件，M0-3 继续补齐。
   - 不依赖实测的 P 任务（P-5、P-8、P-9、P-10、P-11）可以先实施；P-1 与 P-3 已有容器内的相对证据（见 7.5）。

### 7.5 已测结论摘要（Linux 容器，仅相对关系）

详见 [2026-09-26 基线](../../benchmark/results/2026-09-26-linux-container.md)。对计划的影响：

- **P-1 方向修正**：`rawpy.extract_thumb()` 取得与 ExifTool `-JpgFromRaw` 相同的 5616×3744 JPEG，耗时 0.6 ms，ExifTool 进程约 158 ms。P-1 改为“先 LibRaw `extract_thumb`（校验尺寸不小于 ExifTool 候选），失败时再走 ExifTool”。需要在更多机型上确认 LibRaw 挑选的是最高清的内嵌预览。
- **P-3 新增项**：JPEG `draft((size,size))` 的方框让横图在 2048 档失去 DCT 缩放，应改为按宽高比计算方框；RAW 小档（≤1024）可直接用 PreviewImage（1616×1080，解码 8.8 ms）。
- **HEIF**：21 MP HIF 完整解码约 1 s，内嵌缩略图在当前 libheif 下无法解码。P-11 的依据得到加强；HEIF 的持久缩略图缓存命中率需要作为 M0-2 的重点指标。
- **N1（原生 `decode_scaled`）的预期收益下调**：20.5 MB 的 32.7 MP JPEG 在 DCT 缩放生效后仍需 229 ms，瓶颈是熵解码，换用 C++ 调用同一个 libjpeg-turbo 无法消除。G1 评估时 JPEG 与 RAW 内嵌 JPEG 需要分开计算可得收益。

### 7.6 macOS arm64 目标平台基线与 G0 判定（2026-09-26）

详见 [macOS arm64 基线](../../benchmark/results/2026-09-26-macos-arm64.md)（M2 Max，Python 3.13，与发布环境一致）。**[已测结论]**：

- 需要 GUI 线程同步执行的段都超出 100 ms 交互预算：RAW 点击 238 ms（其中 ExifTool 进程 110 ms）、HIF 21 MP 379 ms、JPG 32.7 MP 448 ms。
- **G0 通过**：P-1、P-3、P-10、P-11 进入实施。P-11 默认 4 MP 维持不变（实测约 18 ms/MP，100 ms 预算约对应 5.5 MP，留出余量）。
- **新增待决策项**：JPEG 同步阈值（当前 40 MP）。32.7 MP 高质量 JPEG 每次点击约 0.45 s 卡顿。建议 P-2 完成后复测，再决定是否按 100 ms 预算降到约 8 MP（大图先显示档位缓存，再异步替换）。属于受保护交互，需要用户确认。
- Windows x64 基线仍未测。

### 7.7 P-1 / P-10 / P-11 实施记录（2026-09-26）

- **P-1**：`app_common/thumb_stream.get_raw_preview_jpeg` 先用进程内 LibRaw（`rawpy.extract_thumb`），长边 ≥1600 直接采用，否则按 ExifTool 标签顺序补查并取较大者；Windows 非 ASCII 路径改用文件对象交给 LibRaw。样本 ARW：ExifTool 进程 0 次，与 ExifTool `JpgFromRaw` 解码后逐像素一致（字节不同是 ExifTool 重写头部所致）。
- **P-10**：`PreviewPanel.set_image` 对 RAW 不再在 GUI 线程提取或解码；只显示精确档位缓存或“正在加载预览”，由 `_FullPreviewLoader` 替换为内嵌高清预览。容器端到端：ARW `set_image` GUI 耗时 0.2 ms（原同步段 447 ms）。
- **P-11**：新增 `SuperViewer_SYNC_FULL_PREVIEW_HEIF_MAX_MP`（默认 4 MP），JPEG 阈值不变。容器端到端：21 MP HIF `set_image` GUI 耗时 11.8 ms（仅读头；原 938 ms），随后 worker 完整显示。
- 回归：相关 68 项定向测试通过；SuperViewer/app_common 全量与改动前基线相比没有新增失败（既有失败均为 Linux 容器环境问题：Windows 路径断言、主题测试段错误、缺 ffmpeg/ExifTool 配置）；BirdStamp 与 build_tools 全部通过。AGENTS.md、AI_CODING_RULES、Viewer 架构文档与 README 已同步受保护流程描述。
- **待用户在 Mac/Windows 复测**：`bench_imaging.py --cases viewer.raw,thumb` 看 ExifTool 进程数应为 0；手动检查 RAW 点击（先档位图/加载提示，再高清图）、按住方向键浏览 RAW、释放后单次提交、焦点框在高清图到达后出现、中文路径 RAW（Windows）。

### 7.4 暂不纳入范围

GUI 重写或框架更换；Rust；GPU/Metal/CUDA 图像路径；自研 JPEG/RAW/HEIF 解码器；新增色彩管理或导出 EXIF/ICC（属于产品功能，不算等价迁移）；YOLO 或推理优化；ExifTool 替换；`_panel.py` 拆分等无关重构；独立仓库 SuperBirdViewer/SuperBirdStamp 的同步；Intel 或 universal2 macOS 包；锁文件引入（建议另立议题）。

---

## 8. 首个执行任务：M0-1 基准脚本与环境记录

**目标**：用一条命令在 Windows 或 macOS 上，对现有代码路径做可重复的离线分段测量。不改任何生产代码。

**内容**：
1. `benchmark/env_report.py`：输出 6.1 要求的环境 JSON（CPU/内存/OS/Python/依赖版本/ExifTool 版本/线程配置/样本清单哈希）。`psutil` 缺失时记为“不可用”，不自动安装。
2. ~~`benchmark/samples.example.json`~~（实施时简化）：脚本直接扫描 `--samples` 目录，按扩展名分类并记录 SHA-256，识别 Git LFS 指针并标为“未覆盖”。需要逐样本期望值（例如方向）时，再补清单文件。
3. `benchmark/bench_imaging.py`：在 `QT_QPA_PLATFORM=offscreen` 下直接调用现有函数，逐样本记录分段耗时与峰值 RSS：
   - `thumb_stream.get_raw_preview_jpeg`（记录进程启动次数，通过包装 `run_exiftool_once` 计数）、`thumb_stream.load_thumbnail_rgb`（128/512/2048）
   - `preview_panel._load_full_preview_qimage`、`_load_raw_embedded_preview_qimage`、`_load_quick_preview_pixmap`
   - `birdstamp.decoders.image_decoder.decode_image_for_preview`、`decode_image`
   - `birdstamp.export_stage.core.render_video_frame`（用内置模板构造 `VideoFrameJob`，输出写到临时目录）
   - 模式：`--repeat N --warmup 1 --cold-copy`（每轮把样本复制到新的临时目录）
   - 输出：每个函数、每个样本的 P50/P95/max、峰值 RSS 和样本 ID 写入 JSON，另生成一份汇总表 Markdown
4. `benchmark/README.md`：两平台的运行命令（PowerShell 用 here-string 传给 `.venv\Scripts\python.exe`，macOS 用 `.venv/bin/python3`）、冷热缓存说明、安全约束（只用样本副本；绝不写原图、XMP 或 `report.db`；临时目录用完即删）。

**完成标准**：
- 真实样本到位前（见 7.3）：用 `--synthetic` 在临时目录生成测试图，脚本端到端跑通并输出 JSON 与 Markdown，输出中明确标记 `synthetic=true`、不作为基线。云端 Linux 会话可以在新建的 `.venv` 中安装 requirements 做这一步，但结果只证明脚本可用。
- 真实样本到位后：在至少一个目标平台（Windows x64 或 macOS arm64）用 repo 根目录的 `.venv` 跑通，输出 JSON 与 Markdown。另一平台未运行时标“未验证”。
- 对同一样本连续两次运行，P50 差异在报告的噪声范围内；报告写明噪声大小。
- 运行前后样本目录（含 `.superpicky/report.db` 副本和 XMP）的哈希不变。`SuperBirdStamp/config/editor_export_state.json` 与 `config/templates/*.json` 无改动（AGENTS 要求检查）。
- 新增文件 `-m py_compile` 通过，`git diff --check` 干净；除 `benchmark/` 外没有任何 diff。
- 缺失的样本类别在输出中列为“未覆盖”，不编造数据。

完成 M0-1 后按依赖顺序执行 M0-2（补探针）与 M0-3、M0-4（样本与双平台基线），再以 G0 决定 P 轨的实际范围。
