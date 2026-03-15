# 极速鸟框

`birdstamp` is a cross-platform Python tool for batch rendering bird-photo outputs with a metadata banner.

当前仓库推荐的使用方式是 monorepo 根目录共享 `.venv`，通过 `python init_dev.py` 初始化环境；GUI 启动优先使用仓库根 `run.sh` / `run.bat`，打包优先使用仓库根 `build_all.sh` / `build_all.bat`。

## Features

- Batch process single files or directories (`--recursive`).
- Decode JPG/JPEG/PNG/TIFF, optional HEIF/HEIC/HIF, optional RAW.
- Metadata extraction:
  - Preferred: ExifTool (`auto|on|off`)
  - Fallback: Pillow EXIF
- Render banner templates (YAML/JSON), built-in: `default/minimal/dark/compact`.
- Bird name priority: CLI arg, metadata, filename regex.
- Output modes: `keep`, `fit`, `square`, `vertical`.
- CLI module commands (run from `SuperBirdStamp/` with the shared `.venv`):
  - `python -m birdstamp render`
  - `python -m birdstamp inspect`
  - `python -m birdstamp templates`
  - `python -m birdstamp init-config`
  - `python -m birdstamp gui`

## Development Setup

```bash
python init_dev.py
```

启动两个 GUI：

```bash
./run.sh
```

只启动 SuperBirdStamp GUI：

```bash
python SuperBirdStamp/entry.py
```

## Quick Start

CLI 示例默认在 `SuperBirdStamp/` 目录内执行；如果你在仓库根目录，请先 `cd SuperBirdStamp`。

```bash
python -m birdstamp render ./photos --recursive --out ./output --template default --theme gray --bird "灰喜鹊"
```

Print parsed metadata:

```bash
python -m birdstamp inspect ./photos/IMG_0001.JPG
```

Initialize user config:

```bash
python -m birdstamp init-config
```

Open GUI editor:

```bash
python -m birdstamp gui
```

Open GUI with a startup image:

```bash
python -m birdstamp gui --file ./photos/IMG_0001.JPG
```

GUI capabilities:

- Open an image and preview rendered output.
- Edit template layout, fonts, colors, divider, and logo.
- Toggle shown fields and output mode.
- Save current template as YAML/JSON.
- Export rendered image as JPEG/PNG.


# License

本仓库根目录代码与文档在未另行说明时，按 `GNU Affero General Public License v3.0 (AGPL v3.0)` 发布，详见 `LICENSE`。

仓库中包含独立子模块与第三方组件时，这些内容仍以其各自上游许可证为准，不因本仓库根目录 `LICENSE` 自动变更。相关边界说明见 `THIRD_PARTY_NOTICES.md`。
