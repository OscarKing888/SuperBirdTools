# 鸟清晰度计算流程图

本文按代码的实际执行顺序，用流程图说明一张照片从入队到写入 XMP 的每个环节。算法的背景、标定数据和取舍理由见 [bird_sharpness.md](bird_sharpness.md)；本文只讲“先做什么、再做什么、什么条件走哪条分支”。所有阈值都标注了代码位置，以代码为准。

版本：`sbt-blur-v14`（[scoring.py](../bird_sharpness/scoring.py) `ALGORITHM_VERSION`），非默认参数会追加后缀（见文末）。

## 目录

1. [总流程](#1-总流程)
2. [入口与任务调度](#2-入口与任务调度)
3. [解码全分辨率图像](#3-解码全分辨率图像)
4. [鸟体识别：首遍、鸟群补检、去重与限数](#4-鸟体识别首遍鸟群补检去重与限数)
5. [无鸟复检：提亮、1024 px、焦点处弱候选与放大](#5-无鸟复检提亮1024-px焦点处弱候选与放大)
6. [增强找鸟（可选）](#6-增强找鸟可选)
7. [逐只鸟测量](#7-逐只鸟测量)
8. [排除假鸟 / 局部，取最好的一只](#8-排除假鸟--局部取最好的一只)
9. [无鸟时的区域测量：焦点 → 手动对焦焦平面 → 全图](#9-无鸟时的区域测量焦点--手动对焦焦平面--全图)
10. [边缘模糊半径 σ 的估计（所有区域共用）](#10-边缘模糊半径-σ-的估计所有区域共用)
11. [判定与打分](#11-判定与打分)
12. [写入 XMP sidecar](#12-写入-xmp-sidecar)
13. [参数、版本后缀与关键阈值速查](#13-参数版本后缀与关键阈值速查)

---

## 1. 总流程

一张照片 = 一个 `BirdSharpnessAction`（[actions.py](../bird_sharpness/actions.py)），核心在 `BirdSharpnessAnalyzer._analyze()`（[analyzer.py](../bird_sharpness/analyzer.py)）。

例外：计算过程窗口里模型链的「测清晰度」传入 `given`（`GivenBirds`，临时图上的给定鸟）时走 `_analyze_given()`：① 用传入的临时图（不解码）→ 直接 ⑤ 逐只鸟测量 → ⑥ 排除 → 取最好的一只；跳过 ②–④ 的识别、复检、增强找鸟和 ⑦ 无鸟分块，也不读焦点框（临时图不是相机画幅）。临时图（`preview.analysis_input()`）是原图在这些鸟周围外扩 `ANALYSIS_MARGIN` 的一块像素；按钮箭头可选「涂灰抠图（对比用）」（`fill=True`，`GivenBirds.filled`）：鸟以外的 `rgb8` 涂成 `MASK_FILL`、测量用灰度涂成 `MASK_FILL_GRAY`，灰色与轮廓之间的人工锐利边缘在测量区域碰到轮廓时会被测进去，结果可能偏锐。见 [鸟清晰度检测](bird_sharpness.md) 的「测清晰度」。

```mermaid
flowchart TD
    A["任务入队<br/>BirdSharpnessAction.execute()"] --> B{"跳过已检测？<br/>sidecar 已有同版本 verdict"}
    B -->|是| Z0["skipped，不计算"]
    B -->|否| C["① 解码全分辨率<br/>stage = decode"]
    C --> D["② 首遍识别<br/>1024 px 副本，网络输入 640 px，conf ≥ 0.25<br/>stage = detect"]
    D --> D1["去重：一框 ≥ 70% 落在另一框内视为同一只"]
    D1 --> D2{"有鸟框短边 < 64 px？"}
    D2 -->|是| D3["鸟群补检：2048 px 副本 / 2048 px 输入<br/>只补首遍漏掉的鸟"]
    D2 -->|否| DS
    D3 --> DS["忽略小鸟：min_bird_side > 0 时丢掉鸟框长边 < N px（全分辨率）的鸟<br/>复检 / 增强 / 模型链给定的鸟同样过滤"]
    DS --> D4{"设置了每张最多鸟数？"}
    D4 -->|是且超出| D5["压在焦点框上的鸟优先，其余按 置信度×面积 截断"]
    D4 -->|否| E
    D5 --> E{"有鸟？"}
    E -->|否| F["③ 无鸟复检 _recheck<br/>提亮 → 1024 px 输入 → 焦点处弱候选 → 焦点放大"]
    F --> G{"有鸟？"}
    G -->|否| H{"增强找鸟开启？<br/>模式 nobird，或 manual 且为手动对焦"}
    H -->|是| I["④ 增强找鸟 _enhanced_search<br/>中心区域 N×N 重叠窗口"]
    H -->|否| N
    I --> J{"有鸟？"}
    E -->|是| K
    G -->|是| K
    J -->|是| K["⑤ 逐只鸟测量 _measure_bird<br/>stage = measure"]
    J -->|否| N["⑦ 无鸟区域测量 _no_bird_result<br/>焦点窗口 → 手动对焦焦平面 → 全图分块"]
    K --> L["⑥ 排除假鸟 / 鸟的局部 excluded_birds"]
    L --> M["取最好的一只：分数最高，其次 σ 最小，再次置信度<br/>region = bird"]
    N --> N1["verdict = no_bird<br/>region = focus / manual / full"]
    M --> P["⑧ 结果 BirdSharpnessResult<br/>verdict、score、sigma、region、birds…"]
    N1 --> P
    P --> Q{"write_xmp 且未取消且非 error？"}
    Q -->|是| R["⑨ 写同名 .xmp sidecar<br/>stage = write"]
    Q -->|否| S["只返回结果（CLI 不加 --write-xmp、计算过程窗口）"]
```

进度窗口显示的 4 个阶段即 `decode → detect → measure → write`（`check` 是写入前的版本检查，`queued` 为排队）。

---

## 2. 入口与任务调度

```mermaid
flowchart LR
    subgraph 入口
        V1["SuperViewer 目录树右键<br/>鸟清晰度检测（本目录 / 含子目录）"]
        V2["SuperViewer 文件列表 / 缩略图右键<br/>检测鸟清晰度（N 张）"]
        V3["CLI<br/>python -m bird_sharpness [--write-xmp] 路径"]
        V4["计算过程窗口<br/>BirdSharpnessTraceAction（只读，不写 XMP）"]
    end
    V1 --> P["AnalysisParams<br/>来源：用户选项 / 窗口参数页 / CLI 参数"]
    V2 --> P
    V3 --> P
    V4 --> P
    P --> AN["BirdSharpnessAnalyzer<br/>共享模型 shared_models(detector)"]
    AN --> POOL["BrowserWorkPool，WorkKind.ANALYSIS<br/>最低优先级，并发 ≤ CPU/2（最多 6）"]
    POOL --> ACT["每张一个 BirdSharpnessAction<br/>模型推理在锁内串行，解码/测量并行"]
```

- 三处入口共用同一套 `AnalysisParams`（[params.py](../bird_sharpness/params.py)），只是来源不同。默认值下结果和版本号不变。
- 模型推理（YOLO / 关键点 / SAM）在 `BirdSharpnessModels` 的锁内串行执行，RAW 解码、预处理和 σ 计算并行。

---

## 3. 解码全分辨率图像

代码：[image_source.py](../bird_sharpness/image_source.py) `load_analysis_image()`。

```mermaid
flowchart TD
    A["输入文件"] --> B{"RAW？"}
    B -->|是| C["LibRaw 解码（rawpy）<br/>相机白平衡、16 位、LINEAR 去马赛克、不自动提亮"]
    C --> C1["gray = 绿色通道 / 65535（用于测量）<br/>rgb8 = 高 8 位（用于识别）"]
    C --> C2["camera_crop：相机 JPEG 画幅在 RAW 输出中的位置<br/>焦点框按它映射，测量只在画幅内"]
    B -->|否| D["Pillow 解码 JPEG / TIFF 等<br/>gray 取亮度"]
    C1 --> E["AnalysisImage（rgb8, gray, is_raw, camera_crop）"]
    C2 --> E
    D --> E
    E --> F["计算过程窗口可改用：相机内嵌 JPEG / 降噪成片<br/>仅用于对比，从不写 XMP"]
```

要点：不使用 RAW 内嵌预览（机内锐化、降噪和 8 位压缩会改变边缘宽度）。`valid_bounds()` 按 `camera_crop` 裁掉 RAW 输出的黑边，否则黑边的硬边界会被测成“清晰”。

---

## 4. 鸟体识别：首遍、鸟群补检、去重与限数

代码：`_analyze()`、`dedupe_detections()`、`_small_bird_pass()`、`prefer_focus_birds()`、`_limit()`。

```mermaid
flowchart TD
    A["rgb8 缩到长边 1024 px<br/>DETECT_LONG_EDGE"] --> B["YOLO detect_birds<br/>imgsz 640（DETECT_IMGSZ），conf ≥ 0.25（BIRD_CONFIDENCE_MIN）"]
    B --> C["按置信度降序<br/>去重 dedupe_detections：<br/>两框任一有 ≥ 70% 面积落在另一框内 → 只留强的"]
    C --> D{"有任一鸟框短边 < 64 px？<br/>（1024 px 副本像素，FLOCK_BIRD_SIDE）"}
    D -->|是| E["鸟群补检 _small_bird_pass：<br/>rgb8 缩到 2048 px，imgsz 2048<br/>新鸟 found_by = full_small"]
    E --> F["首遍鸟按比例放到 2048 坐标系（掩膜不动，测量结果不变）<br/>+ 补检鸟 → 再次去重"]
    D -->|否| H0
    F --> H0["_drop_small：min_bird_side > 0 时丢掉框长边 < N px 的鸟<br/>（识别步骤注明忽略只数；全部丢掉 → 走复检 / 无鸟分支）"]
    H0 --> G{"max_birds > 0 且鸟数超出？"}
    G -->|是| H["prefer_focus_birds：与焦点框相交的鸟排前<br/>其余保持 置信度×面积 顺序 → 截取前 max_birds 只"]
    G -->|否| I
    H --> I["detections（每只带 box、mask 或 None、confidence、found_by）"]
    I --> J{"模型类型"}
    J -->|分割模型 yolo11l-seg 等| K["每只鸟带像素掩膜"]
    J -->|检测模型 yolo11n 等| L["无掩膜；测量时用框内核（四边各内缩 8%）"]
```

- 检测模型由 `AnalysisParams.detector` 决定（`auto` = 内置选择：分割模型优先）。
- 首遍网络输入保持 640 px 不变：这样有鸟的照片结果不随新增逻辑改变。

---

## 5. 无鸟复检：提亮、1024 px、焦点处弱候选与放大

代码：`_recheck()`。仅在首遍（含鸟群补检）没有鸟时执行。

```mermaid
flowchart TD
    A["首遍无鸟"] --> B["lift_midtones：按亮度中位数算 γ<br/>目标中位数 100，γ ≥ 0.35"]
    B --> C{"γ < 0.9（画面暗）？"}
    C -->|是| D["提亮副本再识别<br/>imgsz 640，conf ≥ 0.25"]
    D --> E{"有鸟？"}
    E -->|是| E1["采纳 found_by = full_lifted<br/>_focus_part 只留压在焦点框上的掩膜连通块"]
    C -->|否| F
    E -->|否| F["原副本再识别<br/>imgsz 1024（RECHECK_IMGSZ），conf ≥ 0.05 作为候选"]
    F --> G{"有 conf ≥ 0.25 的候选？"}
    G -->|是| G1["采纳 found_by = full_fine"]
    G -->|否| H{"有相机焦点框？"}
    H -->|否| Z["复检失败：无鸟"]
    H -->|是| I["near = 与焦点框相交的候选"]
    I --> J{"规则一：有 conf ≥ 0.10 且<br/>overlap（交集/较小框）≥ 0.5 的候选？"}
    J -->|是| J1["取置信度最高的一只<br/>found_by = focus_weak"]
    J -->|否| K{"anchors = overlap ≥ 0.2 的候选，有吗？"}
    K -->|否| Z
    K -->|是| L["规则二：以焦点为中心取方形窗口<br/>边长 = 长边 / 6、/ 4、/ 2.5 依次"]
    L --> M["窗口裁切 → imgsz 640，conf ≥ 0.25"]
    M --> N{"放大后的鸟 overlap ≥ 0.2<br/>且与某 anchor 的 IoU ≥ 0.3？"}
    N -->|是| N1["采纳置信度最高者<br/>found_by = focus_zoom，坐标映射回全图"]
    N -->|否 且还有窗口| L
    N -->|否 且窗口用尽| Z
```

- 提亮图只用于识别，测量永远用原始像素。
- 单凭放大结果不采信（放大后的暗叶子常被认成 0.3–0.8 的“鸟”），必须与一个弱候选位置一致。
- SuperViewer 预览“显示鸟体”首遍无鸟时调用同一函数（`find_missed_bird()`）。

---

## 6. 增强找鸟（可选）

代码：`_enhanced_search()`，参数 `AnalysisParams.enhanced`（默认关闭）。触发条件：复检后仍无鸟，且模式为 `nobird`，或模式为 `manual` 且照片为手动对焦。

```mermaid
flowchart TD
    A["中心区域：画幅每边 region_percent（默认 70%）<br/>有焦点框时以焦点为中心"] --> B["切成 grid×grid（默认 2×2）个窗口，重叠 25%"]
    B --> C{"lift 开启且画面暗（γ < 0.9）？"}
    C -->|是| D["窗口裁切先按 γ 提亮（仅识别）"]
    C -->|否| E
    D --> E["逐窗口识别：imgsz（默认 1024），conf ≥ 0.10 列为候选"]
    E --> F{"候选 conf ≥ min_conf（默认 0.50）？"}
    F -->|是| G["接受，记录是否被窗口边界切到"]
    F -->|否| H["只在计算过程窗口中显示（灰色）"]
    G --> I["排序：未被切到优先 → 框更大优先 → 置信度<br/>去重（保留整只鸟而非碎片）→ 限数 → _focus_part"]
    I --> J["found_by = enhanced，之后与普通鸟一样逐只测量"]
```

---

## 7. 逐只鸟测量

代码：`_measure_bird()`，每只鸟独立得到一组 `BirdMeasurement`。

### 7.1 取该鸟的像素

```mermaid
flowchart TD
    A["鸟框映射到全分辨率，四周外扩 15%（CROP_PAD_RATIO）作为 ROI"] --> B{"有分割掩膜，且 bird_pixels = outline？"}
    B -->|是| C["掩膜缩放到 ROI，并裁到该鸟框内<br/>（检测分辨率的掩膜可能溢到邻鸟）"]
    B -->|否（无掩膜，或 bird_pixels = box）| D["框内核：四边各内缩 8%（BOX_INSET_RATIO）<br/>box 模式下 masked = False"]
    C --> E{"SAM 精修适用？<br/>bird_pixels = outline，sam_model 已设，且（scope = all，或该鸟来自复检 / 增强）"}
    D --> E
    E -->|是| F["SAM 按鸟框抠图 → 只保留框外扩 10% 内的像素"]
    F --> G{"保留像素 ≥ 检测掩膜的 20%？"}
    G -->|是| H["用 SAM 掩膜替换（refined_by = 模型名）"]
    G -->|否| I["视为抠错对象，保留检测掩膜"]
    E -->|否| J
    H --> J["bird_px = 该鸟像素"]
    I --> J
    J --> GF{"grey_fill 开启？"}
    GF -->|是| GF1["ROI 的 gray / rgb8 复制一份，bird_px 以外涂成灰 114（MASK_FILL）<br/>之后高光检测、梯度场、鸟眼定位都用这份抠图；原图不动"]
    GF -->|否| GF2["直接用 ROI 的原像素"]
    GF1 --> K
    GF2 --> K
    GF1 --> K2
    GF2 --> K2
    K["body = bird_px 腐蚀 15 px（BODY_MASK_ERODE_PX）<br/>腐蚀后为空则用 bird_px"]
    K2["core = bird_px 内缩约一个识别掩膜像素<br/>（W / 掩膜宽，限 4–12 px；无掩膜 4 px）− 高光排除区<br/>为空则用 bird_px − 高光排除区"]
    K2 --> K3["高光排除区 specular_highlights：<br/>白顶帽（核 ≈ 0.2×头部尺度，≥ 15 px）比周围高 ≥ 0.10 且 ≥ 3× 周围的小亮斑<br/>面积 ≤ π(0.15×头部尺度)²，外扩 8 px"]
    K --> L["EdgeBlurField(ROI 的 gray)：预计算梯度场（见第 10 节）"]
```

- 头部尺度 = max(40 px, 15% × 鸟框长边)，与关键点无关，先于头部定位算出。
- `bird_pixels` / `grey_fill`（`AnalysisParams`，设置 / 参数页 / CLI `--pixels` `--grey-fill`）：「整个鸟框区域」忽略分割与 SAM 轮廓、只测框内核；「鸟以外涂灰再测量」复刻模型链「抠出像素 + 涂灰」：SAM 在涂灰前用原图像素抠图，涂灰后的裁切交给第 7.2–7.4 节的全部测量和鸟眼定位（`locate_head` 看到的是抠图）。灰与轮廓之间的人工锐利边缘因 core / body 都向内收而通常测不到。非默认值记入版本后缀 `-box` / `-grey`。
- 为什么内缩而不是外扩：掩膜来自 1024 px 识别副本，轮廓有约一个掩膜像素的误差；原来外扩 9 px 时，轮廓上的背景树皮裂缝会成为头部“最强边缘”（DSC06285：38 个头部测点里 13 个在树皮上）。内缩会丢掉鸟的轮廓边缘，头内的羽毛、眼、喙仍在。
- 为什么排除高光：脱焦的点光源不是高斯模糊，而是硬边的光斑（bokeh 圆），阶跃边缘估计会把它的边缘测成 0.3–0.9 px。DSC06285 整只鸟脱焦（羽毛 2.2–2.7 px），眼睛里 7 px 的高光提供了 38 个头部测点中的 16 个，头部中位数 0.70 判成“清晰”；排除后头部 2.75，判“模糊”。

### 7.2 身体 σ 与运动模糊方向比

```mermaid
flowchart LR
    A["body 区域内 Canny(20,60) 边缘<br/>强度 > 4 × 区域内梯度中位数"] --> B{"边缘数 ≥ 60？"}
    B -->|否| C["body_sigma = None，motion_ratio = None"]
    B -->|是| D["每条边缘算 σ，剔除线状结构"]
    D --> E["body_sigma = 所有有效边缘 σ 的中位数"]
    D --> F["按梯度方向分 8 个 bin<br/>每 bin ≥ 15 条才计中位数"]
    F --> G{"有效 bin ≥ 4？"}
    G -->|是| H["motion_ratio = max / min（min 至少 0.3）"]
    G -->|否| I["motion_ratio = None"]
```

### 7.3 头部定位（关键点 + 镜像复核）

代码：`locate_head()`。

```mermaid
flowchart TD
    A{"有 CUB-200 关键点模型？"} -->|否| Z["keypoints = None → 走 7.4 的“无眼模型”分支"]
    A -->|是| B["ROI 裁切（grey_fill 时为涂灰抠图）跑一次：左眼、右眼、喙 坐标 + 可见度<br/>取可见度高的眼"]
    B --> C["ROI 左右镜像再跑一次，坐标映射回来<br/>（左右眼互换）"]
    C --> D["eye_gap = 两次眼距 / 鸟长边<br/>beak_gap = 两次喙距 / 鸟长边"]
    D --> E{"eye_gap ≤ 0.10？（EYE_MIRROR_MAX）"}
    E -->|是| F["眼位置、可见度取两次平均 → eye_reliable = True"]
    E -->|否| G["eye_reliable = False（头部位置不可信）"]
    D --> H{"beak_gap ≤ 0.15？（BEAK_MIRROR_MAX）"}
    H -->|是| I["喙取平均"]
    H -->|否| J["喙不可用"]
```

### 7.4 头部 σ 与该鸟的判定

```mermaid
flowchart TD
    A{"keypoints 情况"} -->|无关键点模型| B["整只鸟 core 的最强边缘 σ 当头部 σ<br/>eye_visible = True，不封顶（准确度下降）"]
    A -->|眼可见 ≥ 0.5 但 eye_reliable = False| B2["同上：整只鸟 core 最强边缘，不封顶<br/>head_sigma 留空"]
    A -->|有关键点| C["头部半径 radius"]
    C --> C1{"喙可用？"}
    C1 -->|是| C2["radius = 1.2 × 眼喙距"]
    C1 -->|否| C3["radius = 15% × 鸟框长边"]
    C2 --> C4["radius = max(radius, 40 px)"]
    C3 --> C4
    C4 --> D{"眼可见度 ≥ 0.5？（EYE_VISIBLE_MIN）"}
    D -->|否| E["head_sigma = None → 判定走 no_eye 分支<br/>用 body_sigma，分数封顶 299"]
    D -->|是| F["head = 以眼为圆心 radius 的圆 ∩ core（第 7.1 节：内缩掩膜 − 高光）"]
    F --> G["head_stats = head 内最强边缘 σ 的统计（第 10 节）"]
    G --> H{"测到 σ 且鸟框长边 < 450 px？（SMALL_BIRD_SIDE）"}
    H -->|是| I["再算 6 个变体圆（同样 ∩ core）：圆心沿上下左右各移 20% 半径，半径 ×0.8、×1.25<br/>head_sigma = 7 个值的中位数"]
    H -->|否| J["head_sigma = head_stats.sigma"]
    I --> K
    J --> K{"head_blank？<br/>（眼可见，但头部没有任何高于噪声的边缘）"}
    K -->|是| L["sigma = max(body_sigma, 1.55)<br/>判为 soft / motion"]
    K -->|否| M["sigma = head_sigma（为空则 body_sigma）"]
    B --> N["classify()（第 11 节）"]
    B2 --> N
    E --> N
    L --> N
    M --> N
    N --> O["BirdMeasurement：verdict、score、sigma、head_sigma、body_sigma、<br/>motion_ratio、eye_visibility、confidence、box、found_by、refined_by…"]
```

---

## 8. 排除假鸟 / 局部，取最好的一只

代码：`excluded_birds()`、`BirdMeasurement.rank()`、`_bird_result()`。只有 ≥ 2 只鸟时才排除；没有关键点模型时（无可见度）不排除。

```mermaid
flowchart TD
    A["所有 BirdMeasurement"] --> B["对每只“看不到眼”（可见度 < 0.5）的鸟："]
    B --> C{"其鸟框 ≥ 50% 落在某只“看得到眼”的鸟框内？"}
    C -->|是| D["排除：鸟的局部（翅膀 / 尾羽），标 并入 #n"]
    C -->|否| E
    A --> E["anchor = 置信度最高的那只"]
    E --> F{"anchor 置信度 ≥ 0.50？"}
    F -->|是| G{"其它鸟：置信度 < 0.40 且看不到眼？"}
    G -->|是| H["排除：误识别（树叶、树干），标 已排除"]
    G -->|否| I
    F -->|否| I
    D --> I["kept = 未排除的鸟（永不为空）"]
    H --> I
    I --> J["best = max(kept, rank)<br/>rank = (score 降序, sigma 升序, confidence 降序)"]
    J --> K["整张照片 = best 的 verdict / score / sigma / head_sigma / body_sigma…<br/>bird_count = len(kept)，birds = 全部 kept 的明细<br/>region = bird"]
```

---

## 9. 无鸟时的区域测量：焦点 → 手动对焦焦平面 → 全图

代码：`_no_bird_result()`、[focus.py](../bird_sharpness/focus.py)、[metrics.py](../bird_sharpness/metrics.py) 的 `sharpest_tiles_blur()` / `full_image_blur()`。

```mermaid
flowchart TD
    A["无鸟"] --> B["读相机焦点框<br/>RAW MakerNote 优先，否则 EXIF/XMP；Viewer 另有 report.db 兜底<br/>按 camera_crop 映射到全分辨率像素"]
    B --> C{"有焦点框？"}
    C -->|是| D["focus_window：<br/>两边都 ≤ 128 → 以焦点为中心 128×128<br/>任一边 > 128 → 用框本身，短边补到 128<br/>贴边时平移不缩小，且在画幅内"]
    D --> E["窗口内最强边缘 σ（第 10 节）<br/>region = focus"]
    E --> F{"测到 σ？（≥ 8 条有效边缘）"}
    F -->|是| Z["输出"]
    C -->|否| G
    F -->|否| G{"mf_center 开启 且 相机记录为手动对焦？<br/>FocusMode 文字含 manual / MF，或 Sony 数值 0"}
    G -->|是| H["中心区域：画幅每边 mf_center_percent（默认 50%）"]
    H --> I["按 mf_tile（默认 256 px）分块，每块带 16 px 真实邻域计算、只留块内边缘"]
    I --> J["只保留落在真实细节的块：<br/>≥ 15 条实测边缘，且 ≥ 50% 的最强边缘是阶跃边缘"]
    J --> K["按块中位 σ 升序，取最清晰的 mf_sharpest_percent（默认 10%，至少 3 块）"]
    K --> L["这些块的边缘 σ 汇总取中位数<br/>region = manual"]
    L --> M{"测到 σ？"}
    M -->|是| Z
    G -->|否| N
    M -->|否| N["全图：只在 camera_crop 画幅内<br/>按 full_tile（默认 1024 px）分块，同样带 16 px 邻域"]
    N --> O["每块最强边缘 σ 全部汇总取中位数<br/>region = full"]
    O --> Z
    Z --> Y["verdict = no_bird，score = sigma_to_score(σ)，bird_count = 0<br/>region_box = 窗口 / 中心区域 / 画幅"]
```

- 手动对焦为什么“取最清晰的块”而不是“全部取中位数”：前景虚化和背景占多数分块，中位数会偏低；焦平面只是画面里最清晰的那一小部分。
- 中心区域没有可测分块时退回全图。

---

## 10. 边缘模糊半径 σ 的估计（所有区域共用）

代码：[metrics.py](../bird_sharpness/metrics.py) `EdgeBlurField`。头部、整鸟、焦点窗口、分块都调用 `select_strongest_edges()`；身体用 `body_blur_detail()`（门槛略不同，见 7.2）。

### 10.1 预计算（每个 ROI 一次）

```mermaid
flowchart LR
    A["gray ROI（float32）"] --> B["g0 = 高斯预平滑 σa = 1.0（PRE_SIGMA）"]
    A --> C["g1 = 高斯 hypot(1.0, 1.5)（预平滑 + 再模糊 1.5）"]
    B --> D["Sobel 梯度 → mag0"]
    C --> E["Sobel 梯度 → mag1"]
    B --> F["g0 按 0.5%–99.5% 拉伸成 uint8 → Canny 用"]
    A --> G["noise_sigma = Immerkær 噪声估计"]
```

### 10.2 单次区域查询 `select_strongest_edges(region)`

```mermaid
flowchart TD
    A["candidates = Canny(30, 90) ∩ region"] --> B["passed = candidates 且 mag0 > 4 × noise_sigma<br/>（NOISE_EDGE_FACTOR，弱边缘被噪声抬高后会测得过“锐”）"]
    B --> C{"passed 数 ≥ 20？（MIN_HEAD_EDGES）"}
    C -->|否| D["selected 为空 → σ 数组为空"]
    C -->|是| E["keep = max(5%, min_kept / n)<br/>standard：min_kept 30；dense：60"]
    E --> F["selected = passed 中 mag0 位于前 keep 的边缘"]
    F --> G["每像素：R = mag0 / mag1<br/>total = 1.5 / sqrt(R² − 1)"]
    G --> H{"R > 1.02 且 total ≥ 1.0？"}
    H -->|否| I["line_like：比预平滑还“锐”的线状结构（眼圈高光、细枝）→ 舍弃"]
    H -->|是| J["σ = sqrt(total² − 1.0²)（扣除预平滑）"]
    J --> K["EdgeSelection：ys, xs, sigma[], noise_sigma, threshold<br/>（计算过程窗口按此着色）"]
    K --> L["_edge_stats：样本 < 8 → None；否则<br/>standard 取中位数，dense 取第 40 百分位；另给 p25 / p75 / 数量"]
```

原理：Zhuo & Sim (2011) 梯度再模糊比值法。σ 与对比度、曝光、羽色无关。像素采样与 Sobel 差分带来约 0.6–0.73 px 的固有下限，阈值已按实测值标定。

---

## 11. 判定与打分

代码：[scoring.py](../bird_sharpness/scoring.py) `classify()`、`sigma_to_score()`。

### 11.1 verdict

```mermaid
flowchart TD
    A["输入：head_sigma, body_sigma, motion_ratio, eye_visible, head_blank"] --> B["motion_like = body_sigma ≥ 1.55 且 motion_ratio ≥ 1.5"]
    B --> C{"eye_visible 且 head_sigma 为空 且 head_blank？"}
    C -->|是| D["σ = max(body_sigma, 1.55)<br/>motion_like ? motion : soft"]
    C -->|否| E{"eye_visible 且 head_sigma 有值？"}
    E -->|是| F{"head_sigma ≤ 0.85？"}
    F -->|是| F1["sharp"]
    F -->|否| G{"head_sigma ≤ 1.05？"}
    G -->|是| G1["usable"]
    G -->|否| G2["motion_like ? motion : soft"]
    E -->|否| H{"body_sigma 有值？"}
    H -->|否| H1["no_eye，score = None"]
    H -->|是| I["score = sigma_to_score(body_sigma) 封顶 299"]
    I --> J{"motion_like？"}
    J -->|是| J1["motion"]
    J -->|否| K{"body_sigma ≥ 1.55？"}
    K -->|是| K1["soft"]
    K -->|否| K2["no_eye"]
```

无鸟时不经过 `classify()`，verdict 固定为 `no_bird`，分数由区域 σ 直接映射。

### 11.2 σ → 0–1000 分（分段线性，SuperPicky 刻度）

| σ (px) | 分数 | 含义 |
| --- | --- | --- |
| ≤ 0.50 | 1000 | |
| 0.85 | 500 | 清晰门槛 |
| 1.05 | 300 | 可用门槛（SuperPicky 三星线） |
| 1.55 | 100 | 明显模糊（SuperPicky 判废线） |
| ≥ 2.55 | 0 | |

```mermaid
flowchart LR
    A["σ"] --> B{"σ ≤ 0.50"} -->|是| C["1000"]
    B -->|否| D["在相邻锚点 (0.50,1000) (0.85,500) (1.05,300) (1.55,100) (2.55,0) 间线性插值"]
    D --> E["四舍五入为整数"]
```

---

## 12. 写入 XMP sidecar

代码：[xmp_store.py](../bird_sharpness/xmp_store.py)、`BirdSharpnessResult.to_xmp_fields()`、字段名 [app_common/bird_sharpness_fields.py](../app_common/bird_sharpness_fields.py)。

```mermaid
flowchart TD
    A["BirdSharpnessResult"] --> B{"verdict = error？"}
    B -->|是| Z["不写"]
    B -->|否| C["to_xmp_fields()"]
    C --> D["XMP-photoshop:City = 分数 %06.2f（现有锐度槽，无分数时不改）"]
    C --> E["XMP-superpicky:bird_sharpness_verdict / _score / _sigma / _region /<br/>_bird_count / _head_sigma / _body_sigma / _motion_ratio / _eye_visibility / _version"]
    D --> F["PhotoMetaDataXMP.write()：同一次提交<br/>ExifTool 字段 + 直接编辑 XML 的 superpicky 字段<br/>非 ASCII 值走 UTF-8 临时文件"]
    E --> F
    F --> G["invalidate_metadata_cache(path)"]
    G --> H["SuperViewer 刷新：列表“鸟清晰”列、缩略图底栏标签、右侧信息行"]
    A --> I["birds[]、found_by、head_samples 等明细<br/>只在 CLI --json 与计算过程窗口可见，不写 XMP"]
```

“跳过已检测”在任务开头比较 sidecar 里的 `bird_sharpness_version` 与当前版本（含参数后缀），相同才跳过。

---

## 13. 参数、版本后缀与关键阈值速查

### 可调参数（`AnalysisParams`，三处入口共用）

| 参数 | 默认 | 影响环节 | 版本后缀 |
| --- | --- | --- | --- |
| `max_birds` | 0（不限） | 第 4 节限数 | 无 |
| `edge_estimator` | standard | 第 10 节 min_kept / 分位 | `-dense` |
| `detector` | auto | 第 4、5、6 节识别模型 | `-<模型名>` |
| `sam_model` / `sam_scope` | 空 / all | 第 7.1 节掩膜精修：设了 SAM 模型时每只检测到的鸟都经 SAM 抠一次；rechecked = 只有复检 / 增强找到的鸟 | `-<sam名>-all` / `-rechecked` |
| `min_bird_side` | 0（不忽略） | 第 4–6 节：丢掉框长边 < N px 的鸟 | `-min<N>` |
| `bird_pixels` | outline | 第 7.1 节：轮廓内 / 整个鸟框内核（box 时不做 SAM） | `-box` |
| `grey_fill` | False | 第 7.1 节：鸟以外涂灰 114 后再测（含鸟眼定位） | `-grey` |
| `enhanced.*` | off | 第 6 节 | `-enh-<mode><region>g<grid>i<imgsz>c<conf>[-nolift]` |
| `tiles.full_tile` | 1024 | 第 9 节全图分块 | `-t<n>` |
| `tiles.mf_center` / `mf_center_percent` / `mf_tile` / `mf_sharpest_percent` | True / 50 / 256 / 10 | 第 9 节手动对焦 | `-mf<pct>-<tile>-<sharp>` 或 `-mf-off` |

### 固定阈值（代码常量）

| 常量 | 值 | 位置 | 用途 |
| --- | --- | --- | --- |
| `DETECT_LONG_EDGE` / `DETECT_IMGSZ` | 1024 / 640 | analyzer.py | 首遍识别 |
| `BIRD_CONFIDENCE_MIN` | 0.25 | models.py | 采纳为鸟的置信度 |
| `DUPLICATE_CONTAINMENT` | 0.70 | analyzer.py | 去重 |
| `FLOCK_BIRD_SIDE` / `SMALL_DETECT_LONG_EDGE` | 64 / 2048 | analyzer.py | 鸟群补检触发与分辨率 |
| `LIFT_MEDIAN_TARGET` / `LIFT_GAMMA_MIN` / `LIFT_DARK_GAMMA` | 100 / 0.35 / 0.9 | analyzer.py | 暗部提亮 |
| `RECHECK_IMGSZ` | 1024 | analyzer.py | 复检网络输入 |
| `FOCUS_WEAK_CONFIDENCE` / `FOCUS_WEAK_OVERLAP` | 0.10 / 0.5 | analyzer.py | 复检规则一 |
| `FOCUS_ZOOM_DIVISORS` / `FOCUS_ZOOM_OVERLAP` / `FOCUS_ZOOM_AGREEMENT_IOU` | (6, 4, 2.5) / 0.2 / 0.3 | analyzer.py | 复检规则二 |
| `ENH_CANDIDATE_CONFIDENCE` / `ENH_WINDOW_OVERLAP` | 0.10 / 0.25 | analyzer.py | 增强找鸟 |
| `CROP_PAD_RATIO` / `BOX_INSET_RATIO` | 0.15 / 0.08 | analyzer.py | ROI 外扩 / 框内核 |
| `ANALYSIS_MARGIN` | 0.3 | preview.py | 测清晰度临时图：给定鸟范围每边外扩 |
| `MASK_FILL` / `MASK_FILL_GRAY` | 114 / 114÷255 | preview.py | 涂灰抠图（及模型链抠图）的灰色：rgb8 / 测量灰度 |
| `MIN_KEEP_FRACTION` | 0.2 | refine.py | SAM 掩膜可信下限 |
| `EYE_VISIBLE_MIN` / `BEAK_VISIBLE_MIN` | 0.5 / 0.3 | analyzer.py | 关键点可见度 |
| `EYE_MIRROR_MAX` / `BEAK_MIRROR_MAX` | 0.10 / 0.15 | analyzer.py | 镜像复核 |
| `HEAD_RADIUS_BEAK_RATIO` / `HEAD_RADIUS_BOX_RATIO` / `HEAD_RADIUS_MIN_PX` | 1.2 / 0.15 / 40 | analyzer.py | 头部半径 |
| `SMALL_BIRD_SIDE` / `HEAD_SAMPLE_SHIFT` | 450 / 0.2 | analyzer.py | 小鸟 7 圆中位数 |
| `HEAD_MASK_ERODE_MIN_PX` / `HEAD_MASK_ERODE_MAX_PX` / `BODY_MASK_ERODE_PX` | 4 / 12 / 15 | analyzer.py | 头部掩膜内缩（约一个识别掩膜像素）/ 身体掩膜 |
| `HIGHLIGHT_MIN_RISE` / `HIGHLIGHT_CONTRAST` / `HIGHLIGHT_KERNEL_RATIO` / `HIGHLIGHT_MAX_RADIUS_RATIO` / `HIGHLIGHT_MARGIN_PX` | 0.10 / 3.0 / 0.2 / 0.15 / 8 | metrics.py | 高光（眼睛反光）排除 |
| `EXTRA_BIRD_CONFIDENCE_MAX` / `EXTRA_BIRD_ANCHOR_MIN` / `PART_OF_BIRD_OVERLAP` | 0.4 / 0.5 / 0.5 | analyzer.py | 排除假鸟 / 局部 |
| `PRE_SIGMA` / `REBLUR_SIGMA` | 1.0 / 1.5 | metrics.py | 再模糊比值法 |
| `NOISE_EDGE_FACTOR` | 4.0 | metrics.py | 边缘信噪门槛 |
| `MIN_HEAD_EDGES` / `MIN_BODY_EDGES` | 20 / 60 | metrics.py | 最少边缘数 |
| `DIRECTION_BINS` / `MIN_EDGES_PER_DIRECTION` | 8 / 15 | metrics.py | 运动模糊方向统计 |
| `FOCUS_MIN_SIDE` | 128 | focus.py | 焦点窗口 |
| `TILE_MARGIN` / `MF_MIN_TILES` / `MF_TILE_MIN_EDGES` / `MF_TILE_MIN_STEP_FRACTION` | 16 / 3 / 15 / 0.5 | metrics.py | 分块 |
| `SIGMA_SHARP_MAX` / `SIGMA_USABLE_MAX` / `SIGMA_BLURRED_MIN` | 0.85 / 1.05 / 1.55 | scoring.py | 判定门槛 |
| `MOTION_RATIO_MIN` / `NO_EYE_SCORE_CAP` | 1.5 / 299 | scoring.py | 运动模糊 / 无眼封顶 |
