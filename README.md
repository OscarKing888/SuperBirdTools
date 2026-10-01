# SuperBirdTools

单仓多应用结构：

- `app_common/`：共享通用库，作为 git submodule 维护。
- `SuperViewer/`：SuperViewer 模块，保留自身配置、图标、脚本与打包 spec。
- `SuperBirdStamp/`：SuperBirdStamp 模块，保留自身包代码、模型、资源、脚本与打包 spec。

## 开发文档导航

- [项目行为与验证约束（AGENTS）](AGENTS.md)
- [2026-09-07 审查、修复与后续建议](docs/REVIEW_2026-09-07.md)
- [SuperViewer 架构与功能定位](SuperViewer/docs/ARCHITECTURE.md)
- [SuperBirdStamp 架构与功能定位](SuperBirdStamp/docs/ARCHITECTURE.md)

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

macOS 全量构建统一选择并向两个子脚本传递 Python 解释器；默认优先根目录 `.venv`，
可通过 `PYTHON_BIN` 显式覆盖。BirdStamp 单独构建也优先根目录 `.venv`，仅在其不存在时
回退到 app 自己的 `.venv`，避免分析依赖和自动安装依赖时使用不同环境。
`lap` 已列入 BirdStamp requirements，供 Ultralytics 跟踪模块及打包收集使用。

同机重复构建默认保留 PyInstaller 缓存：macOS 使用 `build/SuperViewer_mac` 和
`build/BirdStamp_mac`，Windows 使用 `build/merged_win`，复用未失效的
Analysis/PYZ/EXE 及原生库处理缓存。升级 Python 或依赖、修改 spec/hooks、增删模块、
切换 CPU/CUDA 环境、缓存异常或准备正式发布时，使用全量清理：

```bash
bash build_all.sh --clean
```

```bat
build_all.bat --clean
```

日志中的 `Processing standard module hook` 是依赖分析钩子，不是重新编译
Python 标准库或 Torch/Qt 的 C/C++ 代码。PyInstaller 会分析导入关系、生成 Python
字节码归档、收集已经安装的原生库，并在 macOS 上处理库路径与代码签名。

增量缓存以整个 PyInstaller 构建阶段为单位；修改任一已收集的 Python 文件后，
所属 app 的 Analysis 仍可能完整重跑，不能保证只处理改动的业务代码。
`dist/` 的 COLLECT 阶段及 macOS BUNDLE 阶段每次都会重新收集，
所以增量模式主要节省模块图分析、动态库扫描及未变更原生库的处理时间。
命中缓存时日志仍有 `checking Analysis/PYZ/PKG/EXE`，但不应再次出现相应的
`Building ...`；如失效，前面的 `Building because ...` 会说明原因。
不要在日常构建前手工删除 `build/` 或添加 `--clean`。两个平台的 CI 发布构建
仍使用 `--clean`。Windows merged spec 会隔离两个 app 的 Analysis/PYZ 工作目录，
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

## 应用名称、版本与 About 配置

只需修改根目录 [app_metadata.json](app_metadata.json)：`version` 是两应用共用的 SemVer 版本，`build_number` 是 macOS 构建号；`apps` 下分别配置 `product_name`（短名称）和 `subtitle`（副标题）。`window_title` 是共用标题模板，也可在某个 app 内单独覆盖，支持 `{app_name}`、`{product_name}`、`{subtitle}`、`{version}`、`{author}`。修改后重启应用；发行包需要重新构建。

[app_identity.py](app_identity.py) 供 About、主窗口、Qt 应用信息和打包共用。macOS plist 与 Windows EXE 版本资源直接读取同一配置。可执行文件名、工作区格式标识和用户数据目录仍是稳定的 `SuperViewer` / `SuperBirdStamp`。版本也可以通过原有脚本更新（现在只写一份 JSON）：

```bash
.venv/bin/python3 build_tools/set_build_version.py 0.2.0 --build-number 2
```

Windows 使用 `.venv\Scripts\python.exe` 执行同一脚本。不要再修改两个包的 `__version__` 或 spec 中的版本常量。

两个应用各自的 [Viewer about.cfg](SuperViewer/about.cfg) 和 [BirdStamp about.cfg](SuperBirdStamp/about.cfg) 继续配置作者、链接、二维码图片。`app_name` / `version` 保留占位符，实际值取自统一配置。`images` 中的 `path` 相对于该配置文件，`size` 指显示长边，`label` 是说明，`url` 是点击链接；`images: []` 隐藏图片。二维码按窗口宽度换行，超出屏幕高度时滚动。BirdStamp 兼容用户配置目录中的 `about.cfg` 覆盖，省略图片字段时继承内置图片。完整规则见 [共享 About 文档](app_common/about_dialog/README.md)。

只读检查可直接运行，生成截图不需要打开或修改工作区：

```bash
.venv/bin/python3 SuperViewer/entry.py --check-about --output-dir /tmp/about-check
.venv/bin/python3 SuperBirdStamp/entry.py --check-about --output-dir /tmp/about-check
```

打包后的可执行文件也接受同样的 `--check-about` 参数；Windows 将输出路径替换为本机目录。

## GitHub Actions 自动构建

`.github/workflows/build-release.yml` 提供两种入口：

- 在 GitHub Actions 页面手动运行时，输入 SemVer 版本号（例如 `0.2.0`），构建结果保留为 14 天的 Actions artifacts。
- 推送 `v*` tag（例如 `v0.2.0`）时，自动构建 Windows x86_64 和 macOS arm64 合集，生成 `SHA256SUMS.txt`，并创建对应 GitHub Release。

```bash
git tag v0.2.0
git push origin v0.2.0
```

构建时只更新 `app_metadata.json`，SuperViewer、SuperBirdStamp、两者的 macOS bundle 和 Windows EXE 版本资源均读取该配置；改动只发生在 runner 的临时 checkout 中。CI 还会在打包前移除 BirdStamp 的 autosave/export-state 运行态文件，避免把本机照片路径放入发布包。

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
## 自动更新

完整多应用构建现在同时生成独立 `SuperBirdUpdater`、短 commit 版本文件清单、并行 MD5/SHA-256 校验和文件级增量下载载荷。默认通过公开 GitHub Release 免登录更新；保留百度盘手动入口。

首次安装需解压整个套件，两个主应用与更新器放在同一个可写目录；下载完成后安全关闭两应用，事务安装成功后重启。配置、发布方法、命令行及恢复流程见 [更新器文档](SuperBirdUpdater/README.md)。
