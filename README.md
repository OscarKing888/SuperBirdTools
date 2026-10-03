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
- [SuperViewer 批量 RGB 降噪、模型预下载与离线打包](docs/image_denoise.md)
- [鸟清晰度检测：算法、阈值、存储与模型](docs/bird_sharpness.md)

## 鸟识别模型与 SuperPicky

SuperViewer 的「鸟清晰度检测」「查看清晰度计算过程」和「显示鸟体」会用到下面的模型。模型权重都不入 git；其中分割模型和鸟眼模型来自 [SuperPicky（慧眼选鸟）](https://github.com/jamesphotography/SuperPicky)，本仓库既不复制也不打包它们，而是在运行时读取已安装的 SuperPicky 里的文件。

| 用途 | 模型文件 | 来源 | 缺失时 |
| --- | --- | --- | --- |
| 鸟体识别（首选，给出每只鸟的像素轮廓） | `yolo11l-seg.pt`（或 `yolo11m/s/n-seg.pt`） | SuperPicky 安装包 | 改用下面的检测模型，只有鸟框 |
| 鸟体识别（备用，只有鸟框） | `yolo11n.pt`（或 `yolo11s.pt`、`yolov8n.pt`） | `python init_dev.py` 下载到 `SuperBirdStamp/models`，打包版 Viewer 自带 | 两种识别模型都没有时无法检测 |
| 鸟眼、鸟喙定位 | `cub200_keypoint_resnet50_slim.pth` | SuperPicky 安装包 | 按整只鸟测量，准确度明显下降 |

SuperPicky 的鸟眼模型：

- **怎么用**：在 [bird_sharpness/models.py](bird_sharpness/models.py) 中按 SuperPicky 的 `PartLocalizer`（ResNet50 加坐标、可见度输出头）重建网络，再加载同一份权重，输入 416 px。
- **用途**：得到左眼、右眼、鸟喙的位置与可见度。眼可见（≥ 0.5）时只测眼周头部区域的清晰度；看不到眼判「无鸟眼」，分数上限 299。
- **兼容性**：只调用模型，不调用 SuperPicky 程序。清晰度分数写入与 SuperPicky 相同的锐度字段 `XMP-photoshop:City`，使用相同的 0–1000 刻度。

模型查找顺序：

1. 环境变量 `SUPERBIRD_SHARPNESS_MODEL_DIR`；
2. 打包程序自带的 `models` 目录；
3. 仓库的 `SuperViewer/models`、`SuperBirdStamp/models`；
4. 已安装的 SuperPicky：
   - macOS：`/Applications/SuperPicky.app/Contents/Resources/models`、`~/Applications/SuperPicky.app/...`；
   - Windows：`%ProgramFiles%\SuperPicky`、`%LOCALAPPDATA%\SuperPicky` 下的 `_internal\models` 或 `models`；
5. 与本仓库同级的 SuperPicky 源码目录 `SuperPicky/models`。

所以要获得完整的鸟清晰度检测，请安装 SuperPicky，或把上面两个文件放进 `SuperViewer/models`。打包版 SuperViewer 目前只带 `yolo11n.pt`，没装 SuperPicky 的电脑只能按鸟框测整只鸟，检测进度窗口会提示。SuperPicky 自带的其他模型（飞行判断 `superFlier_efficientnet.pth`、鸟种识别 `model20240824.pth`、美学评分 `cfanet_iaa_ava_res50`）目前没有使用。细节见 [鸟清晰度检测](docs/bird_sharpness.md#模型)。

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

从根目录手动运行完整测试（应用启动和 `run.sh` / `run.bat` 都不会触发测试）：

```powershell
.\run_tests.bat
```

macOS / Linux 用 `./run_tests.sh`。脚本使用仓库 `.venv`（在 worktree 中自动使用主 checkout 的 `.venv`，也可用 `PYTHON_EXE` 指定），其余参数原样传给 pytest，例如 `./run_tests.sh SuperViewer/tests -k tag`。

GUI 测试默认以 Qt `offscreen` 运行，不在桌面弹出窗口：根目录 [conftest.py](conftest.py) 在导入任何测试前设置 `QT_QPA_PLATFORM=offscreen`，直接运行 `python -m pytest` 同样生效。需要观察真实窗口调试时加 `--show-windows`（或设置 `SUPERBIRD_TEST_SHOW_WINDOWS=1`）；显式设置的 `QT_QPA_PLATFORM` 优先。

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

根目录 [app_metadata.json](app_metadata.json) 保存两应用共用的版本前缀 `version`（如 `1.2`）和 `build_number`；完整显示版本固定为 `主版本.次版本.HEAD前8位`，例如 `1.2.a1b2c3d4`。前两段通过 `bump-version` 传入，第三段不能手填。`apps` 下分别配置 `product_name`（短名称）和 `subtitle`（副标题）。`window_title` 支持 `{app_name}`、`{product_name}`、`{subtitle}`、`{version}`、`{author}`，也可在某个 app 内覆盖。

[app_identity.py](app_identity.py) 供 About、主窗口、Qt 应用信息和打包共用。源码启动从 Git HEAD 生成完整版本；构建工具 [set_build_version.py](build_tools/set_build_version.py) 将完整版本写入 `build/version/app_metadata.json`，所有 spec 收集这份生成配置，打包后无需 Git。生成文件不提交，源码配置不会因为 build 变脏；相同内容不重复写入，以保留增量缓存。直接运行 spec 也会自动生成配置。

```bash
# 只查看实际版本
.venv/bin/python3 build_tools/set_build_version.py --check-only
# 生成构建配置，不改源码版本前缀
.venv/bin/python3 build_tools/set_build_version.py --build-number 2
```

Windows 使用 `.venv\Scripts\python.exe` 执行同一脚本。macOS `CFBundleShortVersionString` 使用 `主版本.次版本.0`，Windows 固定数字资源使用 `主版本.次版本.0.0`；完整 hash 版本用于应用显示、EXE 字符串资源、Tag 和安装包名。[Apple 数字版本格式](https://developer.apple.com/documentation/bundleresources/information-property-list/cfbundleshortversionstring)和 [Windows 数字版本资源](https://learn.microsoft.com/en-us/windows/win32/api/verrsrc/ns-verrsrc-vs_fixedfileinfo)不接受 hash 字母。`build_number` 继续作为 macOS 构建号；CI 使用运行编号。不要再修改两个包的 `__version__` 或 spec 中的版本常量。

两个应用各自的 [Viewer about.cfg](SuperViewer/about.cfg) 和 [BirdStamp about.cfg](SuperBirdStamp/about.cfg) 继续配置作者、链接、二维码图片。`app_name` / `version` 保留占位符，实际值取自统一配置。`images` 中的 `path` 相对于该配置文件，`size` 指显示长边，`label` 是说明，`url` 是点击链接；`images: []` 隐藏图片。二维码按窗口宽度换行，超出屏幕高度时滚动。BirdStamp 兼容用户配置目录中的 `about.cfg` 覆盖，省略图片字段时继承内置图片。完整规则见 [共享 About 文档](app_common/about_dialog/README.md)。

只读检查可直接运行，生成截图不需要打开或修改工作区：

```bash
.venv/bin/python3 SuperViewer/entry.py --check-about --output-dir /tmp/about-check
.venv/bin/python3 SuperBirdStamp/entry.py --check-about --output-dir /tmp/about-check
```

打包后的可执行文件也接受同样的 `--check-about` 参数；Windows 将输出路径替换为本机目录。

## GitHub Actions 自动构建

`.github/workflows/build-release.yml` 提供两种入口：

- 在 GitHub Actions 页面手动运行时，输入前两段版本号（例如 `1.2`），第三段取所选 ref 的实际 HEAD 前 8 位，构建结果保留为 14 天的 Actions artifacts。
- 推送 `v*` tag（例如 `v1.2.a1b2c3d4`）时，先测试并校验版本规则，再以 `--clean` 构建 Windows x86_64 和 macOS arm64 三应用套件，生成更新清单、增量分卷、完整安装包和 `SHA256SUMS.txt`；附件校验及上传全部成功后发布 GitHub Release。
- 普通 commit 或只推送 `main` 不触发此发布工作流。手动运行即使选择 Tag，也只生成 Actions artifacts，不发布 Release。

完成本轮提交并合入 `main` 后，在检出 `main` 的仓库根运行版本工具：

```bash
./bump-version.sh 1.2
```

```bat
:: Windows
bump-version.bat 1.2
```

[build_tools/bump_version.py](build_tools/bump_version.py) 先校验前两段版本号和仓库状态，再只更新 `app_metadata.json` 的前缀（`build_number` 默认保持当前值，可用 `--build-number N` 指定）。入口脚本随后直接用 Git 命令：

1. 有配置变化时，`git commit --only` 仅提交 `app_metadata.json`（`Bump version to 1.2`），保留其他暂存内容和本地运行态修改；
2. **提交完成后**读取 HEAD 的前 8 位，在该提交上创建附注 Tag `v1.2.<hash>`；避免把提交自身的 hash 写回提交内容造成循环；
3. `git -C app_common push origin main`：CI 必须能下载主仓库引用的子模块提交；
4. `git push --atomic origin main v1.2.<hash>`：`main` 与本次 Tag 要么一起推送成功，要么远端都不变。

推送要求当前 checkout 在 `main`，`app_common` 的 gitlink 提交必须已在子模块的 `main` 上。源码版本配置有未提交修改或前两段低于已提交版本时，修改文件前停止。提交失败保留文件修改；Tag 失败保留版本提交，修复后用相同前两段重跑即可补建。已有 Tag 不覆盖；在同一个 HEAD 重跑会复用相同的附注 Tag，不重复提交。如果已有新代码提交（包括合入 `origin/main` 后产生的合并提交），同一个 `1.2` 会生成新的 hash Tag，旧 Tag 保留但不批量推送。

- `--no-push`：只在本地提交和打 Tag，之后不带该参数重跑即可推送；也可在功能分支上使用。
- `--no-tag`：提交并推送 `main`，不创建或推送 Tag（不会触发发布）。
- `--no-commit`：只更新 `app_metadata.json` 的版本前缀。直接运行 `build_tools/bump_version.py` 也只支持此模式。

Tag 必须是 `v主版本.次版本.8位小写hash`，hash 必须等于 Tag 所指 commit 的前 8 位，前两段也必须与该提交的源码配置一致。旧数字三段版本、预发布后缀、错误 hash 都会在 CI 校验时被拒绝。更新器用 `release_version` 显示完整版本，同时保留旧清单的短 commit `version` 字段，兼容已发布更新器；更新顺序仍按主线 first-parent 提交数判断，不能对 hash 排大小。

本地 `build_all.sh` / `build_all.bat` 和单应用 spec 均自动生成当前 commit 版本。CI 通过 `SUPERBIRDTOOLS_BUILD_VERSION` 和 `SUPERBIRDTOOLS_BUILD_NUMBER` 传递统一版本与构建号，两个平台再次验证与各自 HEAD 一致。完整安装包名为 `SuperBirdTools-1.2.<hash>-<platform>-<arch>.zip`。回归检查：`.venv/bin/python3 -m pytest build_tools/tests/test_bump_version.py build_tools/tests/test_set_build_version.py -q`。

进度见 [Build packages and release](https://github.com/OscarKing888/SuperBirdTools/actions/workflows/build-release.yml)，成功后的下载见 [Releases](https://github.com/OscarKing888/SuperBirdTools/releases)。也可以运行 `gh run list --workflow build-release.yml`，再运行 `gh run watch <运行ID> --exit-status`。仓库需启用 GitHub Actions；发布 job 使用工作流自带的 `GITHUB_TOKEN` 和 `contents: write` 权限，无需额外配置个人令牌。

若 checkout 报 `not our ref`，说明 `app_common` 的 gitlink 提交尚未推送到其远端；先推送子模块，再重跑失败任务。不要通过重置 gitlink 丢弃尚未发布的共享代码。

构建时只更新 `app_metadata.json`，SuperViewer、SuperBirdStamp、两者的 macOS bundle 和 Windows EXE 版本资源均读取该配置；改动只发生在 runner 的临时 checkout 中。CI 还会在打包前移除 BirdStamp 的 autosave/export-state 运行态文件，避免把本机照片路径放入发布包。

Windows merged 包中的 `SuperViewer/` 与 `SuperBirdStamp/` 相互引用，必须保持在同一个 zip 中分发。macOS 产物当前是原生 arm64。自动构建产物均未做 Windows 代码签名或 macOS Developer ID 签名/公证，首次运行时可能出现系统安全提示。

Release 已存在且本工作流的资产齐全时，会保留旧资产而不覆盖；若只存在部分资产，工作流会停止，避免新 checksum 与旧包混用。修复上传失败的草稿时，请先人工删除该草稿中本工作流生成的 ZIP、更新清单 JSON 和 `SHA256SUMS.txt`，再重新运行；已公开版本的内容更新请使用新 Tag。当前依赖和外部模型/ffmpeg 资源尚未锁定到完整哈希，因此历史 tag 的重新构建不保证逐字节一致。

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
