# SuperViewer 批量 RGB 降噪

SuperViewer 使用本地 NAFNet-SIDD width64 模型对照片进行 RGB 降噪。RAW 会完整显影为 sRGB 后处理，**不是 Bayer 原始域降噪**；不输出 DNG，不修改源照片、原 XMP 或 `report.db`。

## 使用

在列表或缩略图中右键单张/多张照片启动降噪；目录右键的批量降噪菜单可分别处理本目录或包括子目录。菜单直接使用已保存设置。非模态进度窗口显示结果和失败原因，可以停止，并打开已完成照片的输出目录。切换浏览目录不会取消正在处理的批次；关闭程序会取消排队任务并等待运行任务安全结束。

在“设置 → 用户选项 → 批量降噪”中配置：

- 输出到每张照片所在目录下的子目录（默认 `denoised`）、固定目录，或每次弹框选择目录。
- TIFF（默认 16 位，保留透明度），或 JPEG（质量 95、无色度抽样）。有透明度的图片不能输出 JPEG。
- 强度 0–100%，默认 100%。这是原像素与模型结果的混合比例，不是模型训练噪声级别；0% 只完成显影/格式转换。
- 设备自动选择 CUDA、MPS 或 CPU；可以强制 CPU。同时处理照片默认 2 张、上限 4 张，还受浏览器池分析配额及可用内存限制。

成片为 `<原主干>_denoised.tif/jpg`，对应同名 `.xmp`；冲突自动追加 `_2` 等编号。比较名称时统一 Unicode 规范化并忽略大小写。目录扫描先固定源文件集合，排除隐藏缓存目录、默认/指定的降噪子目录以及当前固定输出目录，不跟随目录符号链接。目录扫描也跳过已有 `_denoised` 成片；需要再次处理时可显式选择该文件。

两款应用预览工具栏的来源按钮按“默认预览 → 显示 RAW → 显示降噪”循环；右侧箭头展开互斥单选菜单，可直接选择目标来源。菜单勾选与该视口实际模式同步，非 RAW 照片跳过 RAW，A/B 两侧独立记忆本会话选择。“显示降噪”只查找已有成片，缺失或损坏时回退原图并在预览状态中提示。文件查找、XMP 校验及成片解码都在完整预览 worker 中执行；长按仍显示原照片的精确档位缩略图，松键后才升级成片。当前照片的降噪任务完成后，已处于降噪模式的视口自动刷新。

[`preview.py`](../image_denoise/preview.py) 通过成片 XMP 中的源路径、文件大小、修改时间及 RAW 裁切几何验证对应关系，不靠固定输出目录中的同名文件猜测。旧成片仅在源子目录且同名来源无歧义时兼容。GUI 自选输出位置记录在本机用户状态目录的 `denoise_previews.json`（最多 2048 项）；重新打开 Viewer 后仍可查找，索引只提供候选提示，实际成片仍须通过来源校验。源照片及其 XMP 不因新增索引而修改。

支持静态 JPEG、PNG、WebP、8/16 位灰度/RGB TIFF、SDR HEIF，以及 LibRaw/rawpy 支持的 RAW。PSD、动画、多页 TIFF/HEIF、浮点 TIFF、PQ/HLG HDR 图片会报告跳过，不静默取第一帧或压成 SDR。未知或损坏的 ICC 会报告失败。无 ICC 的普通 RGB 图片按 sRGB 解释；HEIF 的 SDR NCLX 信息用于传递函数/色域转换。

## 算法与数据保留

无 Qt 核心在 [`image_denoise`](../image_denoise/)，入口为 `denoise_file()`、`DenoiseOptions`、`DenoiseResult`。RAW 经相机白平衡、全尺寸 16 位线性 sRGB 显影，再按 LibRaw 默认有效画幅裁去传感器填充并应用 sRGB 编码曲线。裁切使用扣除传感器边距、应用显示旋转后的几何，不按黑色像素猜测范围；缺少有效几何时保留完整图像。`image_io.camera_frame_pixels()` 在模型推理前完成裁切，新输出已处于相机画幅，XMP 的 `denoise_camera_crop` 为 `null`，焦点/鸟体不再重复应用 RAW 边距。旧成片的实际像素不会自动重写；需要重新降噪或另存修正版。模型接受 float32 RGB `[0,1]`；不会读取缩略图或 RAW 内嵌 JPEG。普通图片按 ICC 转入 sRGB，高位深 PNG/TIFF 不经过 Pillow 的 8 位 RGB 缓冲区。

模型遵循 [NAFNet 官方 SIDD width64 配置](https://github.com/megvii-research/NAFNet/blob/main/options/test/SIDD/NAFNet-width64.yml)。默认 FP32、512 分块、128 重叠，边界加权融合；整图累加器留在 CPU。内存不足会缩小分块并整张重试，设备失败回退 CPU，日志记录实际设备。NAFNet 包含全局池化，因此分块结果不保证与整图逐像素一致；应结合真实照片检查羽毛与平滑背景，不把测试集分数当成本相机的画质保证。

每张照片通过 `DenoiseAction` 提交到浏览器已有 `WorkKind.ANALYSIS` 低优先级任务池。协调器提交前预留估算内存，不让已启动的浏览器线程等待内存额度。CPU 照片可并行推理，GPU 按分块互斥；解码、颜色处理和编码仍在不同照片间并行。不会运行时修改 Torch 全局线程配置；CPU 模式需要减少原生运算线程时，可在启动前设置 `OMP_NUM_THREADS=2`、`MKL_NUM_THREADS=2`。

输出使用 `tifffile`/`imagecodecs` 真正写出 16 位数组；8 位原图转成 16 位不会恢复原本不存在的信息。成片保存标准拍摄 EXIF 和原侧车用户字段，自定义 namespace 保存在新 XMP 中。方向和尺寸按最终像素校正，ICC 固定为与输出匹配的 sRGB。源 RAW/TIFF 的像素数据偏移、压缩与位深结构不能拷贝覆盖成片；含原文件偏移的二进制 MakerNotes 也不复制，避免生成无法再次读取的 TIFF/JPEG。

图片和 XMP 先在目标目录的临时子目录完成，再无覆盖发布。写入失败或取消会清理本次未完成输出；如果清理也失败，错误中列出残留路径。ExifTool 写出用有超时的独立进程，不占用浏览器元数据全局读取锁。

## CLI、资源与构建

从仓库根目录使用共享 `.venv`。macOS 示例（Windows 使用 `.venv\Scripts\python.exe`）：

```bash
.venv/bin/python3 -m image_denoise /path/to/photos --recursive
.venv/bin/python3 -m image_denoise /path/to/photo.ARW --output-dir /path/to/outputs --format jpeg --strength 80 --device cpu --workers 2
.venv/bin/python3 build_tools/download_denoise_model.py --check-only
.venv/bin/python3 SuperViewer/entry.py --check-denoise --device cpu --output /tmp/denoise-check.json
```

固定的原始权重约 443 MiB，来源、revision、大小及 SHA-256 在 [`image_denoise/models.py`](../image_denoise/models.py)。模型只从作者 Hugging Face 源预下载，保存于被 Git 忽略的 `SuperViewer/models/denoise/`；上游代码及完整许可证随源码和包分发。

`init_dev.py` 与 Viewer 构建前步骤准备模型，已校验缓存不重复下载；`--dry-run` 不下载。下载完成后校验大小和哈希再原子替换，截断内容不会成为正式模型。三个 Viewer spec 共用 `collect_viewer_denoise()` 离线校验并收集模型与原生运行库。运行时不访问网络。

模型和新增依赖改变了打包内容，首次集成或正式发布使用 `build_all.sh --clean` / `build_all.bat --clean`。CI 在 macOS arm64、Windows 64 位执行真实包内 `--check-denoise`，校验模型推理、16 位 TIFF 和 ICC 编解码；诊断 JSON 不写用户运行配置。Windows CUDA 与 macOS MPS 仍需对应设备验证。

回归入口：`image_denoise/tests`、`SuperViewer/tests/test_denoise_controller.py`、共享用户选项和浏览器工作池测试，以及原有预览/快切/关闭回归。测试只写临时照片目录；模型测试在本地权重准备好后运行。
