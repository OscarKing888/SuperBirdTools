# SuperBirdTools

单仓多应用结构：

- `app_common/`：共享通用库，作为 git submodule 维护。
- `SuperViewer/`：SuperViewer 模块，保留自身配置、图标、脚本与打包 spec。
- `SuperBirdStamp/`：SuperBirdStamp 模块，保留自身包代码、模型、资源、脚本与打包 spec。

## 目录原则

- 每个 app 只维护自己独有的资源与构建脚本。
- `app_common` 在仓库根目录平级共享，两个 app 都通过入口文件和 PyInstaller `pathex` 引用它。
- 后续新增第 3 个 app 时，直接新增一个顶层 app 目录，并复用相同模式即可。

## 开发运行

前提：

- Python `>= 3.10`
- 先安装各 app 自己的依赖，或统一建一个包含两个 app 依赖的虚拟环境
- 首次 clone 后先初始化 `app_common` 子模块

先初始化子模块：

```bash
git submodule update --init --recursive
```

初始化共享开发环境：

```bash
python init_dev.py
```

初始化完成后，日常运行、测试和打包都应使用仓库根目录的共享 `.venv`，不要改用全局 Python。

从仓库根目录启动 GUI：

```bash
./run.sh
```

Windows：

```bat
run.bat
```

如果只想直接运行某个 app，也可以从仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m SuperViewer
.\.venv\Scripts\python.exe -m SuperBirdStamp
```

macOS：

```bash
./.venv/bin/python3 -m SuperViewer
./.venv/bin/python3 -m SuperBirdStamp
```

从根目录运行完整测试：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest
```

`pytest.ini` 会加入根目录与 `SuperBirdStamp` 包路径，无需临时改用全局解释器。

如果只想直接运行入口脚本：

```powershell
.\.venv\Scripts\python.exe SuperViewer\entry.py
.\.venv\Scripts\python.exe SuperBirdStamp\entry.py
```

## 打包入口

推荐的全量打包入口：

macOS：

```bash
bash build_all.sh
```

Windows：

```bat
build_all.bat
```

Windows 同机重复构建默认保留 `build/merged_win`，让 PyInstaller 复用未失效的
Analysis/PYZ/EXE 缓存。升级 Python 或依赖、修改 spec/hooks、增删模块、切换
CPU/CUDA 环境、缓存异常或准备正式发布时，使用全量清理：

```bat
build_all.bat --clean
```

增量缓存以整个 PyInstaller 构建阶段为单位；修改 SuperBirdStamp 代码后，其
Analysis 仍可能完整重跑。`dist/` 的 COLLECT 阶段也会在每次构建时重新收集，
所以增量模式主要节省模块图分析和动态库扫描时间。不要在日常构建前手工删除
`build/merged_win`。merged spec 会隔离两个 app 的 Analysis/PYZ 工作目录，
避免它们因覆盖同一个 `base_library.zip` 而互相使缓存失效。构建前请关闭正在
运行的 `dist` 版本及其子进程，否则 Windows 会阻止 COLLECT 重新生成产物目录。

如果只想单独打包某个 app，可直接使用模块目录内的实际脚本：

macOS：

```bash
bash SuperViewer/scripts_dev/build_mac.sh
bash SuperBirdStamp/scripts_dev/build_mac.sh
```

Windows：

```bat
SuperViewer\\scripts_dev\\build_win.bat
SuperBirdStamp\\build_win.bat
```

## 输出布局

- 单独 build 和全量 build 都默认输出到仓库根 `dist/`
- macOS 全量 build 后，`dist/` 顶层只保留：
  - `SuperViewer.app`
  - `SuperBirdStamp.app`
- Windows 单独 build 仍输出 `dist/SuperViewer/`、`dist/SuperBirdStamp/`
- Windows `build_all.bat` 默认走根级 merged spec，目标是让两个 app 在同一个 `dist/` 下共享尽可能多的运行库

## GitHub Actions 自动构建

`.github/workflows/build-release.yml` 提供两种入口：

- 在 GitHub Actions 页面手动运行时，输入 SemVer 版本号（例如 `0.2.0`），构建结果保留为 14 天的 Actions artifacts。
- 推送 `v*` tag（例如 `v0.2.0`）时，自动构建 Windows x86_64 和 macOS arm64 合集，生成 `SHA256SUMS.txt`，并创建对应 GitHub Release。

```bash
git tag v0.2.0
git push origin v0.2.0
```

构建时会把版本同步到 SuperViewer、SuperBirdStamp 及 BirdStamp 的 macOS bundle；这些改动只发生在 runner 的临时 checkout 中。CI 还会在打包前移除 BirdStamp 的 autosave/export-state 运行态文件，避免把本机照片路径放入发布包。

Windows merged 包中的 `SuperViewer/` 与 `SuperBirdStamp/` 相互引用，必须保持在同一个 zip 中分发。macOS 产物当前是原生 arm64。自动构建产物均未做 Windows 代码签名或 macOS Developer ID 签名/公证，首次运行时可能出现系统安全提示。

Release 已存在且本工作流的资产齐全时，会保留旧资产而不覆盖；若只存在部分资产，工作流会停止，避免新 checksum 与旧包混用。需要补齐或替换时，请先人工删除该 Release 中本工作流生成的 zip 与 `SHA256SUMS.txt`，再重新运行。当前依赖和外部模型/ffmpeg 资源尚未锁定到完整哈希，因此历史 tag 的重新构建不保证逐字节一致。

## 平台差异

- macOS：`.app` bundle 天然更偏向自包含，`build_all.sh` 采用“统一 `dist` + 构建后 hardlink 去重”的方式，减少两个 `.app` 在同一磁盘上的总占用，但不改变 bundle 自身结构
- Windows：`build_all.bat` 使用 PyInstaller `MERGE` 多程序构建，让后一个 app 尽量引用前一个 app 已收集的公共运行库，避免简单串行 build 带来的重复 `_internal`
- Windows merged 输出要求两个 app 目录一起分发，不能单独拿走其中一个目录

## 这次重组的关键点

- 不改原始两个仓库，只在 `SuperBirdTools` 中复制整理。
- 两个 app 不再各自内嵌 `app_common`，而是共享根目录 submodule。
- PyInstaller spec 已改为从各自模块目录打包，同时把仓库根加入 `pathex`。
- 每个 app 新增 `entry.py` / `__main__.py`，解决 sibling `app_common` 的导入问题。
