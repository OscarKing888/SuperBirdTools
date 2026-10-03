# 鸟清晰度检测（bird_sharpness）

判断“对上焦了，但鸟本身是否清晰”。常规 Laplacian 方差 / Tenengrad 在高 ISO、暗色羽毛、叶子遮挡的鸟片上失效：像素级梯度主要反映噪点和对比度，框内的清晰树叶也会冒充鸟。本模块改为在**全分辨率**下测量**鸟头部边缘的模糊半径**（像素），与人眼 100% 查看的结论一致。

## 算法

代码：[analyzer.py](../bird_sharpness/analyzer.py)、[metrics.py](../bird_sharpness/metrics.py)、[focus.py](../bird_sharpness/focus.py)、[scoring.py](../bird_sharpness/scoring.py)。整套流程在一个 `BirdSharpnessAction`（WorkerAction）里执行。

1. **解码全分辨率**：RAW 用 LibRaw（LINEAR 去马赛克，绿色通道），不用内嵌预览（Sony 仅 1616 px，100% 下 1 px 的软在缩略图上看不出）。同时记录相机 JPEG 画幅在 RAW 输出中的位置（`camera_crop`），焦点框按它映射。见 [image_source.py](../bird_sharpness/image_source.py)。
2. **鸟体识别（全部鸟）**：在 1024 px 副本上找出所有 bird（置信度 ≥ 0.25，最多 8 只）。有分割模型（`yolo11l-seg.pt` 等）时用每只鸟的像素掩膜；只有检测模型（`yolo11n.pt`，打包版 Viewer 自带）时用鸟框内核（四边各内缩 8%）。
3. **逐只鸟只算自己的像素**：每只鸟单独得到一组清晰度。
   - 有 CUB-200 关键点模型（与 SuperPicky 同一权重）且看得到眼：头部区域 = 以眼为圆心、半径 1.2×眼喙距的圆 ∩ 该鸟像素，取头部模糊半径；看不到眼：用身体 σ，分数封顶 299（`no_eye`）。
   - 看得到眼、但头部没有任何高于噪声的边缘：说明头部严重模糊（有羽毛和眼的头部不清晰时才会没有边缘），判为失焦/运动模糊，σ 取 max(身体 σ, 1.55)。例：DSC04757（ISO 6400，焦点在旁边树叶上），旧版把仅剩的几条贴着噪声的弱边缘测成 0.72“清晰”。
   - 没有关键点模型：用整只鸟的像素代替头部。**准确度明显降低**（翅膀/尾羽运动、遮挡树叶会主导结果），进度窗口会提示。
   - 身体：腐蚀后的鸟像素内所有高于噪声的边缘取 σ 中位数，并按 8 个边缘方向统计；方向最大/最小比 ≥ 1.5 且身体 σ ≥ 1.55 判为运动模糊。
   - **多只鸟取最好的一只**（分数最高，其次 σ 最小）作为整张照片的值；每只鸟的结果保留在 `birds`（CLI `--json`）。
4. **没有鸟 → 焦点区域**：焦点框（Viewer 用与预览叠加相同的读取逻辑，含 report.db 兜底；CLI 读文件元数据）映射到全分辨率像素后：两边都 ≤ 128 px 时取以焦点为中心的 128×128；任一边 > 128 px 时取焦点框本身（另一边不足 128 时补到 128，以保证有足够边缘）。窗口贴边时平移而不缩小。
5. **没有焦点（或焦点区内无可测边缘）→ 全图**：按 1024 px 分块，每块取最强边缘的模糊半径，汇总取中位数，内存有界。
6. **模糊半径**：在最强 5% 的 Canny 边缘上用 Zhuo & Sim (2011) 梯度再模糊比值法估计高斯等效模糊半径 σ：`σ = σ0 / sqrt(R² − 1)`（扣除预平滑）。与对比度、曝光、羽毛深浅无关。排除两类伪边缘：边缘强度低于 4×传感器噪声（Immerkær 估计）的弱边缘——噪声会抬高再模糊前的梯度，弱边缘因此被测得过“锐”；以及比预平滑还“锐”的线状结构（眼圈高光、细枝）。像素采样与 Sobel 差分带来约 0.6–0.73 px 的固有下限。
7. **判定与打分**：

| 条件 | 判定 | 说明 |
| --- | --- | --- |
| σ ≤ 0.85 | 清晰 `sharp` | 鸟：头部（或整只鸟）σ |
| σ ≤ 1.05 | 可用 `usable` | |
| σ > 1.05 | 失焦 `soft` / 运动模糊 `motion` | 身体同时有方向性模糊时为 motion |
| 眼不可见（可见度 < 0.5） | `no_eye` / `soft` / `motion` | 用身体 σ，分数封顶 299 |
| 无鸟 | `no_bird` | 仍写入焦点区域 / 全图的分数与 σ，区域见 `bird_sharpness_region` |

分数按分段线性映射到 SuperPicky 0–1000 锐度刻度：σ 0.50→1000、0.85→500、1.05→300（SuperPicky 三星门槛）、1.55→100（SuperPicky 判废门槛）、2.55→0。

阈值在一组 Sony ILCE-1M2、ISO 2500–3200、420 mm 的鹰鹃连拍上与人眼 100% 判断校准（v3：清晰 0.58–0.84，可用 0.89 左右，软 1.11 起，头部无可测边缘的 04752/04757 为 1.55；阈值沿用 v2 的 0.85 / 1.05 / 1.55）。不同机身/镜头可能需要重新标定；同一连拍内按 σ 相对排序最稳。

## 存储（XMP sidecar，不改原图）

字段定义的唯一来源：[app_common/bird_sharpness_fields.py](../app_common/bird_sharpness_fields.py)。

| 键 | 内容 |
| --- | --- |
| `XMP-photoshop:City` | 现有锐度字段，写入分数，格式 `%06.2f`（与 SuperPicky 一致）；无分数时不改 |
| `XMP-superpicky:bird_sharpness_verdict` | `sharp` / `usable` / `soft` / `motion` / `no_eye` / `no_bird` |
| `XMP-superpicky:bird_sharpness_score` | 0–1000 整数 |
| `XMP-superpicky:bird_sharpness_sigma` | 得出分数的模糊半径（px）：最佳鸟的头部/整鸟/身体 σ，或焦点区域、全图 σ |
| `XMP-superpicky:bird_sharpness_region` | `bird`（鸟体）/ `focus`（焦点区域）/ `full`（全图） |
| `XMP-superpicky:bird_sharpness_bird_count` | 识别到的鸟数 |
| `XMP-superpicky:bird_sharpness_head_sigma` / `_body_sigma` | 模糊半径（px） |
| `XMP-superpicky:bird_sharpness_motion_ratio` / `_eye_visibility` | 方向比、鸟眼可见度 |
| `XMP-superpicky:bird_sharpness_version` | 算法版本（当前 `sbt-blur-v3`），目录检测“跳过已检测”按版本判断 |

`XMP-superpicky:*` 由 `PhotoMetaDataXMP.write()` 直接编辑 XML（ExifTool 不认识该私有命名空间）；与 `XMP-photoshop:City` 等 ExifTool 字段同一次提交。空字符串删除旧值。

## 模型

不入 git。至少需要一个鸟体识别模型（分割模型优先，否则 `yolo11n.pt` 等检测模型）；鸟眼关键点模型可选但强烈建议（没有它准确度明显降低）。按顺序查找：`$SUPERBIRD_SHARPNESS_MODEL_DIR` → 打包资源 `models/` → `SuperViewer/models`、`SuperBirdStamp/models` → 已安装的 SuperPicky（macOS `/Applications/SuperPicky.app/Contents/Resources/models`）→ 同级 `SuperPicky/models`。分割模型 `yolo11l-seg.pt`（或 m/s/n-seg），检测模型 `yolo11n.pt` / `yolo11s.pt` / `yolov8n.pt`，关键点模型 `cub200_keypoint_resnet50_slim.pth`。设备：CUDA → Apple Silicon MPS → CPU，可用 `SUPERBIRD_SHARPNESS_DEVICE` 强制；GPU 推理失败自动回退 CPU。

## 使用

- SuperViewer：目录树右键「鸟清晰度检测」（本目录 / 含子目录，默认跳过同版本已检测；或全部重新检测）；文件列表/缩略图右键「检测鸟清晰度（N 张）」。后台运行、可停止；结果写入 sidecar 后立即刷新列表「鸟清晰」列、缩略图底栏彩色标签（悬停提示含模糊半径）和右侧「鸟清晰度」行。
- CLI：

```bash
.venv/bin/python3 -m bird_sharpness --write-xmp /path/to/folder
```

（`-r` 递归，`--json` 逐行 JSON；不加 `--write-xmp` 只输出不写入。）

单线程约 1.1–1.3 s/张（6144×4096 Sony ARW，Apple Silicon MPS，含 RAW 解码）。

## 并行

每张照片是一个 `BirdSharpnessAction`（[actions.py](../bird_sharpness/actions.py)，`app_common` 的 `WorkerAction`）：分析 + 写 sidecar。模型推理在 `BirdSharpnessModels` 的锁内串行（GPU/MPS 不并发），RAW 解码、预处理和模糊半径计算并行，结果与串行逐张一致。实测 4 线程约 3–4 倍吞吐（瓶颈是 LibRaw 解码）。

- SuperViewer：动作提交到文件浏览器共享的 `BrowserWorkPool`，类型 `WorkKind.ANALYSIS`，优先级最低：只用元数据/缩略图此刻用不上的线程，并发上限默认 CPU 核数的一半（最多 6，且总会给元数据保留额度、给缩略图留 1 个线程），可用环境变量 `SuperViewer_ANALYSIS_WORKERS` 覆盖。每个并行任务持有一张全分辨率解码（约 0.3–0.5 GB），内存紧张时调小。协调线程只保留 2× 并发数的在途任务；停止时撤回排队任务，正在分析的照片做完后丢弃结果、不写 sidecar。
- 进度窗口（[bird_sharpness_progress.py](../SuperViewer/superviewer/bird_sharpness_progress.py)）：总进度、已用时间/速度/剩余时间；「工作线程 x / y 个在工作」及分段负载条；并发数 ≤ 8 时逐线程显示当前文件、阶段（解码 → 识别 → 测量 → 写入，4 格进度点）和已用秒数，空闲线程标“空闲”，超过 8 个只显示汇总负载条；底部注明排队张数与共享线程池里缩略图/元数据的占用（浏览优先时提示“空闲后自动补满”）；结果按判定显示彩色计数。负载快照由协调线程约 5 次/秒发布（`WorkerLoad`），动作阶段来自 `BirdSharpnessAction.stage`；颜色取自窗口调色板，随深浅色主题变化。
- CLI：`-j/--workers N`（默认同上）。
