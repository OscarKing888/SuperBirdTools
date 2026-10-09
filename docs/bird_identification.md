# SuperViewer 调用 SuperPicky 识鸟

## 使用

先启动同一台电脑上的 SuperPicky BirdID 服务（默认 `http://127.0.0.1:5156`）。
SuperPicky 的 `birdid_server.py` 提供 `GET /health` 和 `POST /recognize`；也可在
SuperPicky 自己的开发环境运行 `birdid_server.py --host 127.0.0.1 --port 5156`。
SuperViewer 不载入 SuperPicky 的 Python 包或模型，不自动启动/关闭该外部服务。

- 照片列表/缩略图右键 **识别鸟种…**；多选时为 **批量识别鸟种…**。
- 照片右键 **选择候选鸟名…** 可重新打开已保存候选，支持单张和多选，不需要启动识鸟服务。
  优先读取 `birdid_candidates`；旧照片缺少此字段时读取 `birdid_response.results`。
  读取不写侧车；缺记录、损坏数据或照片不存在时在表格中显示原因。
  候选按置信度降序显示，最高置信度鸟名/分数加粗；无已采纳项时默认选中最高分，
  已手动选过其它鸟种则保留当前选择。“已采纳”表示当前主鸟名，点击其它行“采纳”可改选。
  旧照片当前鸟名行显示“采纳并补存”，点击后也可补写新字段而不改鸟名。
- 目录树右键 **识别鸟种（SuperPicky）** → **识别当前目录…** 或 **识别目录及子目录…**。
- 开始前可调整本机地址、鸟种确认阈值（默认 50%）和“跳过已有鸟种或识别记录”。
  参数在本窗口会话中保留；跳过依据 XMP 中的鸟种/识别记录。
- 达到阈值的最高置信度候选更新鸟名和标题；低于阈值只写待确定候选，保留已有鸟名/标题。
  已有说明、关键词、评级和照片分析评分保持原样。
- 进度窗使用可横向/纵向滚动的表格，每个候选一行，列出文件名、状态、采纳操作、候选序号、
  中文/英文鸟名、置信度、拼音、学名、稀有度、保护等级、说明、地理筛选提示及定位/检测信息。
  最前面的“分组”列用连续括线连接同一照片的所有候选，不同照片之间断开；每组只在首行上端和末行下端画横线，单候选组也仅有上下两条横线。选中单行和滚动时仍保持分组线连续。
  照片列显示等比照片预览和文件名，复用文件列表的 `ThumbnailLoader`、256 档内存/磁盘缓存、
  报告 JPEG 路径解析及共享工作池；缺缓存时后台加载，RAW/HEIF 沿用列表的解码流程。
  只请求可见照片，同照片多个候选共用图片；隐藏窗口或预览列滚出视口时取消请求，
  切批次拒绝旧结果，损坏图片显示“暂无预览”。本窗最多保留 64 张预览。
  缺失字段显示 `—`，稀有度 0 正常显示；可拖动列宽，悬停查看完整内容和原文件路径。
  照片按结果到达顺序追加，每张照片内按置信度降序显示候选；向上查看旧结果时不会强制滚回底部。完成后窗口保留，供核对。
- 每个未确认候选都有 **采纳** 按钮；当前确认结果显示 **已采纳**。可在识别期间或完成后
  采纳低置信度结果，或改选同照片的其它候选（选中操作单元格后也可按空格/回车）。
  手动采纳使用该候选的鸟名、置信度、拼音（有值时）、稀有度和保护等级，同步标题并清除
  待确定字段；完整响应保持原样。单次后台保存期间禁用其它采纳操作，保存成功后更新统计、
  照片列表与信息面板，不重新请求识鸟服务。
- 照片或 XMP 在识别后发生变化时，采纳会提示重新识别并禁用该照片的候选，保留新编辑。
  保存失败显示原因并允许重试；停止/关闭时保留已经提交的结果。
  批量中单张失败继续处理其它照片；服务连接检查失败则结束整批。

整图识别的文件来自共享图片扩展名集合；实际解码能力由服务端决定，不支持/损坏的照片按单张失败报告。
请求使用解析后的原文件绝对路径，RAW/HEIF 由服务解码，不提交 Viewer 临时缩略图。
目录扫描排除隐藏目录和目录符号链接，不递归时只处理该层。
同目录同名 RAW/JPEG 共用 XMP，批次内只识别一次，优先 RAW；进度按唯一侧车数量计数。

## 逐只识别

「前置检测」页可勾选覆盖全局清晰度参数，选择 YOLOv8 xlarge 分割等模型、输入/副本尺寸、检测置信度、框与掩膜去重门槛、鸟群补检和假鸟排除。覆盖仅作用于逐只识别，本会话记住选择；取消勾选恢复跟随全局，不写入用户选项。缺少所选模型时沿用现有确认下载流程。模型切换在任务工作线程加载，不在界面线程推理。

密集鸟群可先用默认修复后的自动补检；需要复现 `DSC03934.jpg` 实验的 26 个候选时，选 `yolov8x-seg.pt`、副本 2048、网络 640、置信度 10%、补检关闭、取消「测量后排除疑似假鸟 / 鸟的局部」。更低门槛不保证鸟种识别准确。已有记录须取消「跳过已有逐只识别记录」后重跑。

同一组检测参数也可在 CLI 指定：`--detector yolov8x-seg.pt --detect-long-edge 2048 --detect-imgsz 640 --detect-conf-percent 10 --flock-mode off --keep-bird-candidates`；去重用 `--duplicate-box-percent` / `--duplicate-mask-percent`（默认均 70）。


照片列表/缩略图右键 **逐只识别…**，多选时显示 **批量逐只识别…（N 张）**；目录树的识鸟子菜单提供 **逐只识别当前目录…** 和 **逐只识别目录及子目录…**。参数只确认一次，整批照片进入同一个后台顺序队列；同名 RAW/JPEG 共用侧车时仍优先 RAW 并去重。

当前 SuperPicky 的 HTTP 入口虽然启用了多线程，但 `birdid/bird_identifier.py` 的 `predict_bird()` 使用 `_CLASSIFIER_INFER_LOCK` 串行执行模型推理，`/health` 也未声明并发推理能力。因此本客户端按照片、再按鸟体顺序请求；不会根据 HTTP 可并发连接就同时启动多份检测模型。CLI 同样顺序处理多路径或递归目录。

整批共用一个 **批量逐只识别 · 结果汇总** 窗口，不逐张弹出结果。复用原识鸟表格的照片预览、分组括线、横纵滚动与可见缩略图缓存，每只鸟一行，编号对应原图检测框；展示确认/待确定/过滤/失败状态、中文/英文鸟名、置信度、拼音、学名、稀有度、保护等级、说明和检测框。待确定或含失败鸟体的照片优先显示；照片内保持鸟编号顺序。无鸟、整张失败、取消也各留一行；已有记录被跳过时显示已保存列表并标注“已有”。本表只汇总逐只结果，不提供整图候选的“采纳”按钮。

顶部同时汇总照片数和本批保存的鸟体状态，进度显示已处理/总照片数；单张或单只失败继续后续任务。工作线程通过最多 32 项的结果队列交付，界面每次最多取 8 张、8 ms 时间预算，避免大量跳过项阻塞窗口。停止不再启动下一张，保留已保存结果；关闭主程序取消任务并等真实线程完成及结果队列排空。原图、整图主鸟名和清晰度评分不因汇总展示而修改。
设置窗口显示当前清晰度参数，并提供以下会话内设置：

| 设置 | 默认值 | 含义 |
| --- | --- | --- |
| 鸟框最小宽度/高度 | 各 64 px | 宽或高任一低于门槛则不请求服务；恰好 64 × 64 会识别 |
| 裁图每边外扩 | 0% | 按鸟框宽高分别外扩 0–50%，裁到图像边界；保存区域仍是原鸟框 |
| 鸟种确认阈值 | 50%（与整图识别共用） | 较低分结果仍保存并显示“待确定” |
| 跳过已有记录 | 关闭 | 仅检查逐只列表，不因整图已有鸟名而跳过 |

后台调用 `BirdSharpnessAnalyzer.analyze()`，直接使用其最终保留的 `birds`，包含小鸟补检、无鸟复检、
增强找鸟及假鸟排除的结果。模型、图像来源及其它分析参数默认沿用“设置 → 鸟清晰度”，可在「前置检测」覆盖；本次调用设置
`max_birds=0`、`min_bird_side=0`，完整分析后再按上表过滤送识别的框，不改变清晰度算法和用户默认设置。
因此会执行清晰度计算，但不会写回清晰度评分。无鸟时的焦点/全图评分区域不会被当作鸟送识别。

像素仅解码一次；使用所选来源的原尺寸、已按 EXIF/LibRaw 旋转的图像裁切 JPEG（质量 95，4:4:4）。
无论原图是 HIF/HEIF/HEIC、RAW、PNG、TIFF 还是 JPG，传给逐只识别服务的均为临时 `.jpg`，不直接传原图。
若裁图前检查失败，错误会区分“不支持的照片格式”和“找不到照片原文件”，并显示完整原图路径；找不到原文件时无法通过转 JPG 恢复。
RAW 的相机 JPEG 来源沿用全尺寸内嵌图/RAW 回退规则；降噪来源查找当前降噪设置的已有成片，缺失按照片失败。
尺寸门槛按这份图像的像素计算，不按缩略图像素计算。每只裁图调用现有 `POST /recognize`，
`top_k=3, use_yolo=false, use_gps=false`：鸟体已定位，裁图没有 GPS；服务自己的地区设置仍由服务决定。
每次请求后删除临时 JPEG，目录在异常/取消时也清理，原始照片不写入。

结果以 UTF-8 JSON 存在同名 XMP 内：

- `XMP-superpicky:birdid_individuals`：数组；每项包含零起始 `index`、`cn_name`、`en_name`、
  `confidence`（鸟种 0–100）、`detection_confidence`（鸟体检测 0–1）、`status`、
  `box_px`（未外扩鸟框）、`box`（相机画幅归一化框）、`crop_px`（实际送识别的外扩裁框），
  成功请求另存完整 `response`（保留所有候选、服务返回的版本等字段）和零起始 `selected_candidate_index`
  （对应 `response.results` 的原始顺序，与鸟体 `index` 无关）。所选候选同时保存
  `scientific_name`、`pinyin_name`、`description`、`gbif_rarity_100`、`iucn_category`、
  `china_protection_level` 快照；缺失明确为 null，0 分保留，不从整图鸟种回填。
  状态为 `confirmed/candidate/skipped/failed`；过滤/失败项保留区域及原因。
- `XMP-superpicky:birdid_individuals_info`：schema=1，坐标系 `oriented_camera_normalized_xyxy`，
  图像宽高、实际来源、RAW camera crop、源文件大小/mtime/扩展名、分析版本/参数及本次识别设置，
  另存 UTC `recognized_at` 和 `recognition_request`（服务名、接口、top_k、use_yolo/use_gps）。
  这些字段是 schema=1 的兼容扩展；旧记录可从已保存响应中匹配同名、同置信度候选读取鸟种属性，
  不需联网或重新识别，不会自动改写旧 XMP。无法匹配、响应损坏或明确 null 时显示未知。

`box_px` / `crop_px` 均为所选来源中 `[left, top, right, bottom]`，右/下界不包含；
`box` 经 RAW 相机裁切逆映射，便于相机 JPEG、全 RAW 和同侧车 JPEG 之间定位。
RAW 相机默认裁切外的鸟可能带超出 0–1 的值；绘制时按各视口实际像素几何映射并裁到可见范围。
重新识别替换本照片列表，成功的无鸟结果保存空列表。单只失败继续其它鸟并保存部分结果；全部可识别鸟失败、
取消、XMP 损坏或原图/侧车在请求期间变化均保留旧列表。整图鸟名、候选、标题、备注、评分保持原样。

“图片信息”最下方的“逐只识别”区域复用清晰度 Debug 的 `TraceBirdList`，分列显示同色编号、
鸟名与鸟种置信度、稀有度/IUCN 徽章及检测置信度和原尺寸框大小。徽章复用图片信息的用户配色/名称，
编号色块采用该鸟稀有度徽章的背景色（含用户自定义配色），缺失使用未知色；预览悬停框独立使用用户选项颜色（默认红色），无内部填充。清晰度 Debug 仍按鸟序号配色。
待确定鸟种的徽章也属于候选信息；缺失单独显示未知。当前识别接口不返回中国国家保护等级，
该字段保留为 null；IUCN 徽章不代表国家保护等级。仅悬停鸟名时在同源 A/B 视口高亮对应区域，编号、置信度、徽章和检测信息不触发，
不依赖“显示鸟体”开关；移出、隐藏列表、切图或开始长按播放时清除。RAW 异步升级后重新映射。
此临时高亮不参与叠加导出。读取来自后台元数据缓存，悬停不会触发解码、检测或 XMP I/O。

右键逐只列表的鸟名可选“设为本照片主要鸟名”。已确认和待确定的条目均可手动指定，
无鸟名、已过滤或失败条目禁用。后台从 XMP 核对菜单绑定的逐只列表快照，离线复用
`candidate_fields()` 更新主鸟中英文名、标题、识别置信度、学名、简介、拼音、稀有度及保护等级；
缺失属性清除旧主鸟的数据，`bird_species_source=individual` 标记来源。整图候选/响应改为该鸟的
候选/响应，逐只列表及其原始完整响应、照片备注、标签、评分不变，不把低置信度伪造为高置信度。
通过 `MetadataResultSync` 更新同侧车照片、预览缓存别名和图片信息，不重新选择或加载预览。
列表过期、文件替换、损坏 XMP、取消或保存失败均不覆盖旧数据；关闭窗口等待工作线程真正完成。

离线设置主鸟的 CLI（`--bird` 使用列表显示的编号）：

```bash
.venv/bin/python3 -m SuperViewer.superviewer.per_bird_primary /path/to/photo.jpg --bird 4
```

CLI（默认使用相机 JPEG，Windows 换为根 `.venv\\Scripts\\python.exe`）：

```bash
.venv/bin/python3 -m SuperViewer.superviewer.per_bird_identification /path/to/photos --recursive --min-width 64 --min-height 64 --padding 0 --threshold 50
```

CLI 的其它清晰度参数使用库默认值，支持 `--source raw/jpeg`；GUI 使用当前 Viewer 参数。
核心与界面回归为 `test_per_bird_identification.py`、`test_per_bird_identification_ui.py`，
主窗口/关闭时序另见 `test_bird_identification_controller.py`；测试使用离线模型和服务替身。

## 手动搜索并指定鸟名

先启动已更新的 SuperPicky BirdID 服务，在照片列表或缩略图选中一张/多张照片，右键 **手动指定鸟名…**。窗口打开后焦点直接位于过滤框，服务 URL 位于同一行右侧。输入中文、英文、学名、带/不带声调拼音或缩写进行过滤，支持分页。选中一个鸟种后再向服务查询详情，显示英文、学名、拼音、全球稀有度、IUCN 与简介；双击列表鸟名或点击 **应用到所选 N 张照片的 XMP** 即可保存，全部成功后自动关闭，不再弹出进度或成功窗口。详情尚未返回时双击会等待该鸟种详情；切换过滤/选择或关闭窗口会取消这次待应用操作。保存失败或部分跳过时原窗口保留并显示原因。

沿用识鸟设置中的本机服务地址，也可在搜索窗口编辑。服务需要新增的 `GET /birds/search` 和 `GET /birds/detail` 路由；旧版服务会提示 HTTP 404，需要升级并重启。现有 `POST /recognize` 协议不变。搜索每页 100 条，以返回的版本 ID 继续翻页；详情绑定 `bird_id` 和 `version_id`，不会用同名的另一条记录代替。

- 鸟名、带声调拼音、稀有度和 IUCN 沿用下表 XMP 字段及兼容映射；稀有度来自全球参考库，与识鸟时按拍摄国家获取的分数可能不同。
- 另存 `XMP-superpicky:scientific_name`、`pinyin_plain`、`bird_species_abbreviation`、`bird_species_description`、`china_protection_level`；简介不会覆盖照片备注。
- `bird_species_source=manual` 标记手动指定；`bird_catalog_id`、`bird_catalog_version` 记录来源，完整详情存入 `bird_catalog_response` JSON 文本。没有模型置信度，清空旧 `birdid_confidence` 和待确定字段；原识别响应和候选列表保留，仍可用“选择候选鸟名…”重新采纳。
- 之后重新识别并确认或采纳识别候选时，学名和简介改为该候选的信息，来源改为 `recognition`，清除目录来源及识别服务未提供的无声调拼音/缩写/国家保护等级，避免保留另一鸟种的资料。
- 未知拼音/等级清除对应旧值，来源标记避免旧 report.db 回填其它鸟种的数据。**IUCN 不等于中国国家一、二级保护等级**；目前服务没有国家保护等级资料，该项显示未知，不推算。
- 同名 RAW/JPEG 共用侧车只保存一次；后台写入、逐项报告失败，保存后同步当前照片列表和信息面板，保留预览视角与未提交备注。网络请求期间照片或 XMP 已变化则跳过该项。应用前再次核对详情，资料变化须重新选择；取消保留已提交结果。

核心可通过 CLI 使用（Windows 换为根 `.venv\Scripts\python.exe`）：

```bash
.venv/bin/python3 -m SuperViewer.superviewer.bird_catalog --query 白头鹎
.venv/bin/python3 -m SuperViewer.superviewer.bird_catalog --bird-id 126247 --version-id 10
.venv/bin/python3 -m SuperViewer.superviewer.bird_catalog --bird-id 126247 --version-id 10 /path/to/photo.ARW
```

ID 示例来自当前库，调用方应使用列表响应中的实际 ID。

## 服务返回与 XMP 字段

请求 `top_k=3`、`use_yolo=true`、`use_gps=true`；国家/省州和地理过滤设置沿用
SuperPicky 服务端设置。服务会按相对置信度差距裁掉较弱候选，因此实际返回 1–3 项。
置信度为 0–100%，客户端不将其误作 0–1。

| 信息 | 保存位置 |
| --- | --- |
| 已确认中文鸟名 | 原有 `XMP-superpicky:bird_species_cn`；标题同步到 `XMP-dc:Title` 和 `XMP-superpicky:title` |
| 已确认鸟名拼音（服务返回时） | `XMP-superpicky:pinyin_name`；`pinyin_name_source` 保存对应鸟名 |
| 已确认英文鸟名 | 原有 `XMP-superpicky:bird_species_en` |
| 已确认 GBIF 稀有度 | `XMP-superpicky:gbif_rarity_100`；兼容 SuperPicky 的 `XMP-iptcExt:Event`（两位小数） |
| 已确认 IUCN 保护等级 | `XMP-superpicky:iucn_category`；兼容 `XMP-iptcCore:IntellectualGenre` |
| 已确认置信度 | 原有 `XMP-superpicky:birdid_confidence` |
| 低于阈值的中文/英文候选与置信度 | 原有 `XMP-superpicky:alt_species_cn`、`alt_species_en`、`alt_confidence` |
| 全部候选鸟名与置信度 | `XMP-superpicky:birdid_candidates`，UTF-8 JSON 数组，每项含 `cn_name`、`en_name`、`confidence`（0–100） |
| 完整服务响应 | 新增补充记录 `XMP-superpicky:birdid_response`，UTF-8 JSON 文本，存在 XMP 内，不另建 JSON 侧车 |

`birdid_candidates` 是服务实际返回的全部候选的精简列表，保留原始顺序和有效的 0 分，不再次按确认阈值过滤。自动确认、低置信度待确定和手动采纳都同步保存此字段；改选主鸟名不会删掉其它候选。再次识别用新列表替换旧列表，不累计历史。显示排序不改变 XMP 中的原始候选顺序。重新打开时已有手动主鸟名优先保留；初次识别仍沿用确认阈值和最高置信度规则。原有 XMP 的完整候选仍可从 `birdid_response.results` 读取，新字段在下次识别或采纳时写入，不自动批量迁移旧侧车。

例如（示例分数）：

```json
[
  {"cn_name": "白头鹎", "en_name": "Light-vented Bulbul", "confidence": 95.5},
  {"cn_name": "红耳鹎", "en_name": "Red-whiskered Bulbul", "confidence": 51.2}
]
```

完整响应保留每个候选的 `rank`、`cn_name`、`en_name`、`display_name`、`scientific_name`、
`confidence`、`description`、`ebird_match`、`pinyin_name`、`gbif_rarity_100`、`iucn_category`，以及 `yolo_info`、`gps_info`、`geo_info`、`warning`。
这些辅助信息可以是空值；`yolo_info` 在当前服务中可能是文本或对象，不保证含检测框坐标。
GPS 只作为识鸟响应保存，不改写照片原有 GPS。`ebird_match` 是兼容字段，当前服务可能固定为
false；实际地理过滤采用 `geo_info` 和 `warning`，不要仅据 `ebird_match` 判断是否过滤。

目前 HTTP 接口**没有透传**模型内部的 `class_id`、`ebird_code` 或 `aesthetic_index`，
因此本功能不生成或更新这些指标，也不从说明文字猜测。
原有这些字段若存在，仍来自此前元数据，不代表这次识别的返回值。
已确认后移除 XMP 中旧的待确定候选；历史 `report.db` 仍是只读兼容数据，不被修改。

## 本地更新拼音

SuperPicky HTTP 响应现已提供可选字符串 `pinyin_name`，Viewer 接收并保存，例如 `{"cn_name":"白头鹎","pinyin_name":"bái tóu bēi"}`（完整识鸟候选仍需 `confidence`）。最高候选达到确认阈值时，拼音与鸟名一起保存；低置信度候选的拼音只保留在完整响应中，不覆盖已确认拼音。

无需启动服务也能补全拼音：

- 照片信息在鸟名下显示“拼音”；有鸟名但缺拼音时，旁边出现 **更新拼音**。
- 照片右键 **更新拼音**，支持多选；目录树右键 **更新拼音** → 当前目录或目录及子目录。
- 使用从 SuperPicky 移植的 11,388 个鸟名带声调词表，保留鸟类多音字读法，如“白头鹎” → `bái tóu bēi`。未收录名称跳过，不按普通汉字猜读音。
- 仅补缺失拼音，已有 `pinyin_name` 或旧别名（`bird_species_pinyin`、`bird_pinyin`、`pinyin`）保留。新写入附带 `pinyin_name_source`；之后鸟名变更时允许重新补全。
- 只写同名 XMP，保留原图、report.db 和其它元数据。进度窗报告更新、跳过及失败数量；停止保留已完成结果。
- BirdStamp 的“鸟种拼音”模板字段可直接读取 Viewer 写入的 `pinyin_name`。

```bash
.venv/bin/python3 -m SuperViewer.superviewer.bird_pinyin_cli /path/to/photo.ARW
.venv/bin/python3 -m SuperViewer.superviewer.bird_pinyin_cli /path/to/folder --recursive
```

## 稀有度徽章与用户配置

服务每个候选返回 `gbif_rarity_100`（数值 0–100，越高越稀有，拍摄地国家优先、全球回退）和 `iucn_category`（如 LC、NT、VU、EN、CR）。`0` 是有效分数；`null`/缺失表示未知，不默认填 0 或 LC。

达到确认阈值时，原始数值和 IUCN 字符串写入上表的原有 XMP 字段；徽章文案和配色属于用户配置，不写入照片。`XMP-superpicky:birdid_rarity_source` 记录对应鸟名，`birdid_rarity_missing` 记录本次缺失的字段名：返回空值时清除上次识别的稀有度/保护等级，标记阻止旧 report.db 再回填；鸟名手动变更后旧等级也不继续展示。低置信度候选的稀有度只保存于完整 `birdid_response`，不覆盖已确认等级。

照片信息面板在拼音下显示“稀有度”徽章，悬停可看原始分数；“保护等级”单独显示 IUCN 编码与中文含义。无数据为“未知”徽章，保护等级为 `-`。现有 XMP 私有字段、SuperPicky 兼容字段与只读 report.db 均可作为显示来源。

**设置 → 用户选项 → 稀有度徽章** 可逐档修改显示名称、背景色、文字色，有即时预览和恢复默认按钮。保存后当前照片立即更新，保持预览与备注草稿。分界沿用 SuperPicky `core/rarity_tier.py`，默认方案：

| 分数 | 配置档位 | 默认显示 | 背景色 | 文字色 |
| --- | --- | --- | --- | --- |
| 0 ≤ 分数 < 8 | common | 普通 | `#64748B` | `#FFFFFF` |
| 8 ≤ 分数 < 25 | uncommon | 少见 | `#15803D` | `#FFFFFF` |
| 25 ≤ 分数 < 50 | rare | 稀有 | `#2563EB` | `#FFFFFF` |
| 50 ≤ 分数 < 75 | epic | 史诗 | `#9333EA` | `#FFFFFF` |
| 75 ≤ 分数 ≤ 100 | legendary | 传奇 | `#C2410C` | `#FFFFFF` |
| 缺失/无效 | unknown | 未知 | `#6B7280` | `#FFFFFF` |

配置保存在 `SuperViewerUser.cfg`（用户选项顶部显示完整路径）。也可在退出应用后编辑 UTF-8 JSON，例如把传奇档改成“传说”：

```json
{
  "rarity_badge_legendary_text": "传说",
  "rarity_badge_legendary_background": "#C2410C",
  "rarity_badge_legendary_foreground": "#FFFFFF"
}
```

保留文件其它选项；六个档位都使用相同的 `_text`、`_background`、`_foreground` 键。颜色为 `#RRGGBB`，名称最多 32 个字符，缺省或无效配置逐项回退到内置值。

## 实现与数据保护

- 无 Qt 核心 [`bird_identification.py`](../SuperViewer/superviewer/bird_identification.py)：
  本机 HTTP 协议、格式校验、扫描去重、置信度分流和 XMP 写入。
- [`BirdIDController`](../SuperViewer/superviewer/bird_identification_controller.py)：
  菜单、参数、进度和一个顺序执行的 `BirdIDWorker`；服务端拥有推理模型。
  `BirdIDAdoptWorker` 负责后台采纳，并保有到真实线程完成；无 Qt 的 `adopt_candidate()`
  与自动确认共用字段映射及原子 XMP 写入。采纳入口依赖本次结果及文件指纹，当前作为 GUI
  交互提供；现有 CLI 识别/阈值确认行为不变。
  控制器保有 worker 至实际 `QThread.finished`，主窗口关闭请求取消并等待完成。
- [`BirdIDResultsTable`](../SuperViewer/superviewer/bird_identification_table.py)：
  模型保留本批结果及候选索引，委托按可见单元格绘制按钮，不为每个候选创建常驻控件。
  拼音/拍摄地更新继续使用原有文本进度窗。
- [`load_saved_candidates()`](../SuperViewer/superviewer/bird_identification_candidates.py)：
  后台只读载入候选与照片/XMP 指纹；新字段决定候选集合，完整响应仅按中英文名和分数
  精确匹配补充详情，采纳后保持原始完整响应。无服务的候选选择属于 GUI 交互，复用原有
  采纳写入和局部元数据同步；CLI 识别行为不变。
- [`BirdIDThumbnails`](../SuperViewer/superviewer/bird_identification_thumbnails.py)：
  控制器持有可见区缩略图协调器到真正线程结束；表格绘制只读有界内存，缩略图加载器
  共用列表缓存与工作池，目录/报告上下文按批次快照。此能力仅用于 GUI 表格。
- 连接超时 3 秒，响应读取超时 120 秒；停止会中断本客户端套接字，返回后不写待取消照片。
  外部服务已开始的推理可能继续到该张结束，但不会通过本客户端写入文件。
- 网络等待期间不持 XMP 锁。提交前验证原图及侧车的文件身份/大小/纳秒修改时间；被移动、
  替换或编辑则跳过。损坏 XMP 不覆盖，缺失原图不创建孤立侧车。
- 标准标题和识别字段通过 `PhotoMetaDataXMP.write()` 在同一锁内一次原子发布；写失败保留旧侧车。
  无鸟、无候选、无效响应或网络错误不产生识别 XMP。原图与 `report.db` 均不写入。
- 保存后只同步当前目录中受影响的同侧车行，使用本地编辑覆盖层抵御晚到元数据批次；不重新选图，
  不触发完整预览加载，信息页保留未提交备注。窗口已切到其它目录时只保留磁盘结果。

命令行也使用同一核心，在仓库根目录执行（macOS）：

```bash
.venv/bin/python3 -m SuperViewer.superviewer.bird_identification_cli /path/to/photo.ARW
.venv/bin/python3 -m SuperViewer.superviewer.bird_identification_cli /path/to/folder --recursive --threshold 70 --skip-existing
```

Windows 64 位将解释器换为 `.venv\Scripts\python.exe`；路径作为参数传递，不经 shell 拼接。
每张输出一行 JSON；任一失败返回非零退出码，CLI 退出关闭共享 ExifTool。
新增模块使用标准库，通过现有 `collect_submodules("superviewer")` 打包收集，无额外模型资源。

验证入口：`SuperViewer/tests/test_bird_identification.py`、
`SuperViewer/tests/test_bird_identification_controller.py`、`app_common/tests/test_xmp_native_atomic.py`，
以及现有 XMP、Viewer 元数据/预览和 BirdStamp 元数据回归。自动测试不访问真实识鸟服务。

### 手动修改稀有度

SuperViewer 选中一张或多张照片后，右键 → **修改稀有度**，直接选择档位；单张也可使用右侧图片信息的 **修改稀有度** 按钮。快捷档位普通/少见/稀有/史诗/传奇分别设为 0/8/25/50/75 分，名称跟随徽章配置。**自定义分数…** 支持 0–100，保留两位小数；多选时同一分数应用于所有所选照片。

保存只写 XMP，原图和 report.db 不变，保留当前鸟种有效的保护等级；旧鸟名绑定和缺失稀有度标记不会遮蔽新的手动值。RAW/JPEG 共用侧车只写一次，并同步相关行。批量处理可取消，保留已经保存的结果，失败文件会显示原因。

命令行同样支持：使用根 `.venv` 执行 `python -m SuperViewer.superviewer.rarity_edit <照片...> --score 72.25`。

鸟名悬停居中始终开启，预览工具栏不再提供开关：鼠标移到逐只识别鸟名上会将对应鸟体移到同源预览中心，保持当前缩放比例。移开清除空心框，视野留在当前位置。“用户选项 → 浏览与性能 → 浏览行为 → 鸟名悬停框颜色”保存 `bird_hover_color`（默认 `#FF0000`，无效配置回退红色）。
