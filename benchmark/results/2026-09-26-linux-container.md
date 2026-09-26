# 2026-09-26 基线（Linux 云容器，非目标平台）

> **适用范围**：本结果来自 Linux x86_64 云容器，不是 Windows x64 / macOS arm64 发布平台。绝对毫秒数**不能**当作用户体验数据；只用来比较同一环境下的相对大小、调用次数和数据流形态。两个目标平台均为“未测”。

## 环境

- Linux 6.18 x86_64，Intel Xeon @ 2.80GHz，4 逻辑核，16 GB 内存
- Python 3.11.15（CI 用 3.13），Pillow 12.3.0（libjpeg-turbo 3.1.4.1），PyQt6 6.11.0 / Qt 6.11，rawpy 0.27.1（LibRaw 0.22.1），pillow-heif 1.8.0（libheif 1.23.4），ExifTool 13.59（Perl 版，从官方 GitHub 仓库取得；仓库自带的 macOS 版缺少 `lib/`，在 Linux 下不可用）
- 命令：`bench_imaging.py --repeat 10`（warm），以及 `--repeat 5 --cold-copy --cases viewer,thumb.load_thumbnail_rgb_128`；两者 P50 差异在 ±15% 内，说明这些路径没有按路径键控的隐藏缓存

## 样本

| 样本 | 内容 | SHA-256 |
| --- | --- | --- |
| DSC08031.jpg | Sony ILCE-7CM2 → DxO PureRAW 6 导出，7008×4672（32.7 MP），baseline，文件 20.5 MB，方向 1 | `0bb3dee9…` |
| DSC01182.ARW | Sony ILCE-1M2，6144×4096；内嵌 JpgFromRaw 5616×3744（1.1 MB）、PreviewImage 1616×1080（68 KB）、ThumbnailImage（2.9 KB），方向 1 | `8af2fd27…` |
| DSC00286.HIF | 5616×3744（21 MP），HEVC Main 10，nclx：BT.709 原色 + sRGB 传递曲线；声明了 1616/320/160 内嵌缩略图 | `b301b38a…` |

未覆盖：PNG、TIFF、PSD、渐进式 JPEG、方向 2–8 的真实样本、嵌入 P3/AdobeRGB ICC、损坏文件、中文路径、其他品牌 RAW。

## 结果（warm，P50 / P95，ms）

| 用例 | JPG 32.7MP | ARW | HIF 21MP |
| --- | --- | --- | --- |
| Viewer 同步完整预览解码 `_load_full_preview_qimage` | 698 / 724 | — | 938 / 1106 |
| Viewer RAW 点击同步段（提取 + 方向 + 解码 + QPixmap） | — | 447 / 492 | — |
| └ 其中 ExifTool 进程（1 次，JpgFromRaw 首个 tag 命中） | — | 158 | — |
| └ 其中方向读取 | — | 5.8 | — |
| Viewer 快速预览兜底 512 | 240 / 263 | 349 / 361 | —（HEIF 走延后路径） |
| 缩略图 128 | 232 / 248 | 337 / 360 | 863 / 1065 |
| 缩略图 512 | 245 / 257 | 362 / 414 | 832 / 1074 |
| 缩略图 2048 | 477 / 504 | 600 / 652 | 1143 / 1472 |
| BirdStamp 预览解码 2048 | 471 / 506 | 656 / 722 | 1022 / 1185 |
| BirdStamp 全尺寸解码（导出） | 587 / 643 | 1531 / 1557（rawpy 解马赛克） | 889 / 1034 |

峰值 RSS 增量：HIF 各路径约 270–296 MB；JPG 完整预览 125 MB、全尺寸解码 375 MB；ARW 同步段 200 MB、全尺寸解码 338 MB。

## 补充探针（同环境，P50）

| 操作 | 耗时 | 说明 |
| --- | --- | --- |
| `rawpy.extract_thumb()`（ARW） | **0.6 ms** | 返回与 ExifTool `-JpgFromRaw` 相同的 5616×3744 JPEG（1.1 MB） |
| ExifTool 一次性进程提取 JpgFromRaw | 155–158 ms | 当前生产路径 |
| ARW JpgFromRaw 全尺寸解码 | 126 ms | |
| ARW JpgFromRaw `draft((512,512))` | 28 ms | 解码到 1404×936 |
| ARW JpgFromRaw `draft((2048,2048))` | 87 ms | **没有缩小**：方框 2048×2048 要求两边都 ≥2048，横图 1/2 缩放后高 1872，不满足 |
| ARW PreviewImage 1616×1080 解码 | 8.8 ms | 足够覆盖 128/512/1024 档 |
| JPG 32.7MP `draft((128,128))` + load | 229 ms | DCT 缩放已生效（876×584），但 20.5 MB 文件的熵解码占主导 |
| HIF 内嵌缩略图（pillow-heif `get_thumbnail`） | 失败 | 索引 0/1：“Decoded image does not have the size signaled in the file”；索引 2：JPEG 编码，本机 libheif 没有 JPEG 插件 |

## 结论（[已测结论]，仅限本环境的相对关系）

1. **RAW 同步段的约 35% 是 ExifTool 进程启动**，其余是全尺寸内嵌 JPEG 解码与拷贝。在这台机器上，`rawpy.extract_thumb` 以约 1/250 的时间取得同一张 JPEG。这支持 P-1（进程内提取），属于 Python 层改动。
2. **RAW 缩略图 128 档的 337 ms 中**，155 ms 是进程启动，其余主要是没有 `draft` 的全尺寸解码。改为 `extract_thumb` 加 `draft`（或者小档直接用 PreviewImage）后，理论下限约为 30 ms 量级 [待验证：需在 P-1/P-3 实施后实测]。
3. **JPEG `draft` 的方框参数有缺陷**：`(size, size)` 方框让横图在 2048 档失去 DCT 缩放。改为按宽高比计算方框属于 P-3。
4. **高质量大 JPEG 的缩略图成本被熵解码主导**（draft 生效后仍需 229 ms）。换成 C++ 调用同一个 libjpeg-turbo 也无法消除这部分，**N1 在 JPEG 缩略图上的可得收益比计划预估更小**。真正的解法是持久缓存命中率，以及在可用时使用 EXIF 内嵌预览。
5. **HIF 21 MP 在这里需要约 0.9–1.1 s 才能完整解码**，而且缩略图没有便宜的来源（内嵌缩略图解码失败）。按当前 40 MP 阈值，它会在 GUI 线程同步解码。这支持 P-11（HEIF 独立阈值），也说明 HEIF 持久缩略图缓存的价值高。HEVC 解码本身在 libheif 内，不在原生化范围。
6. JPG 32.7 MP 在当前策略下（≤40 MP）同步完整解码，本环境约 0.7 s。目标平台上的实际值必须实测，再决定 JPEG 阈值是否也需要调整。
