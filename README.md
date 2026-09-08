# SuperBirdTools — Image Manager 分支

当前 `img_mgr` 分支只运行和构建 **SuperViewer**。图像浏览、筛选、标签和预览由 `SuperViewer/` 实现；根级 `app_common/` 使用独立的 `res_mgr` 代码线提供文件列表、解码、元数据和文件操作。这里的规则与主分支不同，移植时应按功能适配。

## 开发入口

- [SuperViewer 使用说明](SuperViewer/README.md)
- [SuperViewer 架构与开发定位](SuperViewer/docs/ARCHITECTURE.md)
- [AGENTS.md](AGENTS.md)：当前分支的行为和验证约束
- [AI 编码基线](ai_rules/AI_CODING_RULES.md)
- [本批移植记录](docs/PORTS_2026-09-08.md)
- [第三方组件说明](THIRD_PARTY_NOTICES.md)

`<repo>` 表示当前检出的目录，所有开发命令默认在该目录运行。不要引用另一份工作区的虚拟环境。

## 准备环境

运行依赖见 [SuperViewer/requirements.txt](SuperViewer/requirements.txt)。首次建立环境、尚无 `.venv` 时运行：

```powershell
python init_dev.py
```

[init_dev.py](init_dev.py) 创建根级 `.venv`，重启到该解释器，安装开发工具并调用 SuperViewer 的初始化脚本。已有环境时直接使用它：

```powershell
.\.venv\Scripts\python.exe init_dev.py
```

macOS 对应解释器为 `.venv/bin/python3`。

[.gitmodules](.gitmodules) 声明 `app_common` 的上游和 `res_mgr` 分支。检查代码前同时查看两个工作树：

```powershell
git status --short
git -C app_common status --short
```

带有 `app_common` gitlink 的检出可用 `git submodule update --init --recursive` 初始化。当前分支也可能以独立嵌套仓库提供 `app_common/`；没有 gitlink 时，该命令不会创建依赖。缺少目录时按 `.gitmodules` 中的上游检出 `res_mgr` 到该位置，保留现有目录和本地修改，不为移植顺带改变仓库结构。

## 运行

Windows：

```powershell
.\run.bat
```

macOS：

```bash
./run.sh
```

也可以显式使用仓库解释器，绕过启动脚本的环境选择：

```powershell
.\.venv\Scripts\python.exe -m SuperViewer
```

[entry.py](SuperViewer/entry.py) 负责补齐导入路径，本身不切换解释器。启动日志及诊断入口见 [架构文档](SuperViewer/docs/ARCHITECTURE.md)。

## 构建

Windows 默认保留增量缓存；清理重建时传 `--clean`：

```powershell
.\build_all.bat
.\build_all.bat --clean
```

macOS：

```bash
bash build_all.sh
```

当前 [build_all.bat](build_all.bat) 调用 `SuperViewer/SuperViewer_win.spec`，[build_all.sh](build_all.sh) 调用 `SuperViewer/scripts_dev/build_mac.sh`，均只构建 Viewer，不执行双应用 MERGE 或构建后硬链接去重。

默认输出为 `dist/SuperViewer/SuperViewer.exe` 或 `dist/SuperViewer.app`，中间产物在根级 `build/`。`SUPERBIRDTOOLS_DIST_ROOT`、`SUPERBIRDTOOLS_BUILD_ROOT` 可覆盖位置；`--clean` 会删除所选输出与构建目录，使用前确认这些变量指向可重建产物。

## 验证

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest app_common/tests SuperViewer/tests -q
```

先运行变更对应的专项测试，再运行相关完整测试目录；测试定位见 [架构文档](SuperViewer/docs/ARCHITECTURE.md)。GUI 测试必须在构造窗口前隔离配置、上次目录和缓存，不能用真实图库验证会写入的操作。

## 当前分支的数据约定

- 最近的 `.superpicky` 是图库状态范围；目录切换时按该范围加载 `tags.cfg`。
- JSON 侧车优先写入 `.superpicky/metadata/`，可通过 `.superpicky/config.ini` 的 `[sidecar] dir` 指定内部相对目录；保留旧图片旁 JSON 与 XMP 读取兼容。
- 文件操作按图像与侧车整组处理，保留本分支的权限检查和 `.superpicky/deleted` 回收行为。
- `report.db` 在当前文件列表中默认停用；共享库中的兼容读写 API 仍保留。
- 普通选图、连续方向键和覆盖导出有不同的分辨率与异步约定，不应统一为全同步或全异步。
