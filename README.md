# SuperBirdTools

单仓多应用结构：

- `app_common/`：共享通用库，作为 git submodule 维护。
- `SuperViewer/`：SuperViewer 模块，保留自身配置、图标、脚本与打包 spec。
- `SuperBirdStamp/`：SuperBirdStamp 模块，保留自身包代码、模型、资源、脚本与打包 spec。
- `scripts/`：仓库级构建入口，统一从 monorepo 根目录触发打包。

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

从仓库根目录运行：

```bash
python -m SuperViewer
python -m SuperBirdStamp
```

如果只想进入单个 app 目录运行，也可以使用各自的 `entry.py`：

```bash
python SuperViewer/entry.py
python SuperBirdStamp/entry.py
```

## 打包入口

Windows：

```bat
scripts\\build_superviewer_win.bat
scripts\\build_superbirdstamp_win.bat
build_all.bat
```

macOS：

```bash
bash scripts/build_superviewer_mac.sh
bash scripts/build_superbirdstamp_mac.sh
bash build_all.sh
```

各 app 自己原有的构建脚本仍然保留在模块目录内：

- `SuperViewer/scripts_dev/build_win.bat`
- `SuperViewer/scripts_dev/build_mac.sh`
- `SuperBirdStamp/build_win.bat`
- `SuperBirdStamp/scripts_dev/build_mac.sh`

## 输出布局

- 单独 build 和全量 build 都默认输出到仓库根 `dist/`
- macOS 全量 build 后，`dist/` 顶层只保留：
  - `SuperViewer.app`
  - `SuperBirdStamp.app`
- Windows 单独 build 仍输出 `dist/SuperViewer/`、`dist/SuperBirdStamp/`
- Windows `build_all.bat` 默认走根级 merged spec，目标是让两个 app 在同一个 `dist/` 下共享尽可能多的运行库

## 平台差异

- macOS：`.app` bundle 天然更偏向自包含，`build_all.sh` 采用“统一 `dist` + 构建后 hardlink 去重”的方式，减少两个 `.app` 在同一磁盘上的总占用，但不改变 bundle 自身结构
- Windows：`build_all.bat` 使用 PyInstaller `MERGE` 多程序构建，让后一个 app 尽量引用前一个 app 已收集的公共运行库，避免简单串行 build 带来的重复 `_internal`
- Windows merged 输出要求两个 app 目录一起分发，不能单独拿走其中一个目录

## 这次重组的关键点

- 不改原始两个仓库，只在 `SuperBirdTools` 中复制整理。
- 两个 app 不再各自内嵌 `app_common`，而是共享根目录 submodule。
- PyInstaller spec 已改为从各自模块目录打包，同时把仓库根加入 `pathex`。
- 每个 app 新增 `entry.py` / `__main__.py`，解决 sibling `app_common` 的导入问题。
