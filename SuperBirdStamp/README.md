# 极速鸟框

`birdstamp` is a cross-platform Python tool for batch rendering bird-photo outputs with a metadata banner.

当前仓库推荐使用 monorepo 根目录共享的 `.venv`。首次初始化可在仓库根运行
`python init_dev.py`；初始化完成后，运行、测试与打包均使用根目录 `.venv`。
GUI 启动优先使用仓库根 `run.sh` / `run.bat`，完整打包优先使用仓库根
`build_all.sh` / `build_all.bat`。

## 开发定位

编辑器、工作区、模板元数据、流水线和导出模块的关系见 [架构与代码定位](docs/ARCHITECTURE.md)。文档包含常见功能的入口、核心文件和回归测试；必须保留的行为见 [AGENTS](../AGENTS.md)。

## Features

- Batch process single files or directories (`--recursive`).
- Decode JPG/JPEG/PNG/TIFF, optional HEIF/HEIC/HIF, optional RAW.
- Metadata extraction uses ExifTool when available and merges same-stem XMP sidecars;
  sidecar values take priority. `report.db` is read-only fallback/hydration input.
- Render JSON banner templates from `config/templates/`.
- GUI exports PNG/JPEG, GIF, and video.
- GUI image/GIF/video rendering converges through
  `VideoFrameJob -> render_video_frame() -> build_default_image_proc_pipeline()`.
  The builder is in `birdstamp.export_stage.pipeline`; the default non-export stages
  are `ImageProcTemplateCropStage`, `ImageProcResizeLimitStage`,
  `ImageProcTemplateOverlayStage`, and `ImageProcFocusOverlayStage`.

## Development Setup

```powershell
python init_dev.py
```

启动两个 GUI：

```powershell
.\run.bat
```

macOS 使用 `./run.sh`。只启动 SuperBirdStamp GUI：

```powershell
.\.venv\Scripts\python.exe -m SuperBirdStamp
```

## Quick Start

CLI 模块位于 `SuperBirdStamp/`。Windows PowerShell 从仓库根执行：

```powershell
Set-Location SuperBirdStamp
..\.venv\Scripts\python.exe -m birdstamp render .\photos --recursive --out .\output --template default
```

Print parsed metadata:

```powershell
..\.venv\Scripts\python.exe -m birdstamp inspect .\photos\IMG_0001.JPG
```

Initialize user config:

```powershell
..\.venv\Scripts\python.exe -m birdstamp init-config
```

Open GUI editor:

```powershell
..\.venv\Scripts\python.exe -m birdstamp gui
```

Open GUI with a startup image:

```powershell
..\.venv\Scripts\python.exe -m birdstamp gui --file .\photos\IMG_0001.JPG
```

macOS 在 `SuperBirdStamp/` 内将解释器替换为 `../.venv/bin/python3`。

GUI capabilities:

- Open an image and preview rendered output.
- Edit template layout, fonts, colors, divider, and logo.
- Toggle shown fields and output mode.
- Save current template as JSON.
- Export rendered images as JPEG/PNG, GIF, or video.


# License

本仓库根目录代码与文档在未另行说明时，按 `GNU Affero General Public License v3.0 (AGPL v3.0)` 发布，详见 `LICENSE`。

仓库中包含独立子模块与第三方组件时，这些内容仍以其各自上游许可证为准，不因本仓库根目录 `LICENSE` 自动变更。相关边界说明见 `THIRD_PARTY_NOTICES.md`。
