# SuperBirdUpdater

独立打包的文件级增量更新器，Windows x86_64/macOS 使用同一套清单协议。默认从 `OscarKing888/SuperBirdTools` 的公开 GitHub Release 匿名下载；百度盘仍用于手动分发，不参与自动更新。

## 安装与使用

首次手动解压完整 `SuperBirdTools-<major>.<minor>.<commit前8位>-<platform>-<arch>.zip`。三个组件必须同处一个当前用户可写的目录：Windows 是 `SuperViewer/`、`SuperBirdStamp/`、`SuperBirdUpdater/`，macOS 是三个同名 `.app`；目录根还必须包含 `installed-update.json` 和 `update_config.json`。不要只移动一个 app，也不要在挂载的只读镜像内更新。

两个主应用出窗后延迟五秒检查；同一安装目录只运行一个更新器，自动检查默认间隔一小时。菜单「更新 → 检查更新」可立即检查。源码启动和独立单应用包默认不会替换代码。更新器下载时不关闭应用；安装前所有实例必须先同意正常退出。图片/GIF/视频导出期间暂停安装，用户完成任务后可在更新器点击重试。

配置示例：

```json
{
  "source": "github",
  "repository": "OscarKing888/SuperBirdTools",
  "channel": "stable",
  "automatic_check": true,
  "startup_delay_ms": 5000,
  "check_interval_seconds": 3600
}
```

`channel` 可选 `stable` / `prerelease`；后者允许最新预发布版。`source=local` 时另外设置 `directory` 为含更新清单和 ZIP 分卷的目录，用于离线验证。配置只保存来源，不保存账户令牌。其他来源需要实现 `latest()` / `chunks()` 接口，尚未实现的类型会报错，不能把百度盘网页链接直接填写为下载接口。

## 构建和发布

仓库根运行 `build_all.sh` 或 `build_all.bat`，保持原有增量缓存和 `--clean` 规则。构建器新增独立 Updater；最后生成：

- `dist/installed-update.json`：初始安装版本和受管理文件清单。
- `dist/update_config.json`：初始更新来源；已有配置保留。
- `dist/updates/update-<platform>-<arch>.json`：远程版本清单。
- `dist/updates/update-<platform>-<arch>-NNN.zip`：确定性 ZIP64 载荷分卷。
- `dist/updates/SuperBirdTools-<major>.<minor>.<commit前8位>-<platform>-<arch>.zip`：首次安装完整包。

独立生成入口：`python build_tools/generate_update_manifest.py --dist dist --package --hash-workers 8`。所有 Python 命令使用仓库 `.venv`。默认最多八个线程流式计算 MD5 和 SHA-256；分卷默认 1 GiB，单个不可分文件或完整安装包超出 GitHub 附件限制则明确报错。

更新器优先显示 `release_version`（`主版本.次版本.HEAD前8位`）；旧清单回退为短 commit。清单保留原来的短 hash `version`，兼容已发布客户端，并保留完整 commit、共享子模块 commit 和主线 first-parent 提交数量。顺序依赖同一条不重写历史的发布主线，浅克隆禁止生成；同序号不同 commit 拒绝自动更新。同 commit 的重新打包不会作为新版本推送；需要发布新 commit。源码 `app_metadata.json` 保存前两段版本；构建配置生成在 `build/version/app_metadata.json`，系统 bundle/EXE 数值版本保留兼容格式。

CI 的两个平台构建保持 `--clean`，上传完整包、清单和所有分卷；发布阶段校验两平台版本、哈希、附件数量及大小，在草稿附件齐全后才发布。已有附件不覆盖，不混合不同构建。手动工作流仅提供 Actions 制品；真正自动更新使用公开 Release 附件，而不是需要登录的 Actions 下载页。

## 模块与协议

- `manifest.py`：版本比较、路径/大小写/符号链接/归档范围验证；清单仅管理三个组件目录。
- `build.py`：最终产物扫描、并行哈希、分卷和完整包。普通文件记录 `path/kind/mode/size/md5/sha256/asset/offset/compressed_size/compression`；目录记录权限；链接记录相对 `target`。清单 `schema=1`，并保存完整附件大小和 SHA-256。
- `sources.py`、`download.py`：可替换来源与文件内容准备。HTTP Range 严格验证 206 和 Content-Range；200 响应不会静默触发整包下载。显式允许后才下载并验证完整分卷。SHA-256/MD5 均从实际解压数据验证，解压大小受清单约束。
- `runtime.py`、`bridge.py`、`coordinator.py`：启动登记、安装闸门、当前用户 QLocalServer，以及所有实例的 prepare/close/resume 协调。独立随机令牌与安装身份绑定，不改变照片发送 IPC。不强杀主应用。
- `install.py`：目录外运行的安装器使用安装锁、同目录临时文件、备份及 fsync 后的事务日志。每次替换用 rename，不原地修改硬链接。断电/异常后日志驱动回滚，恢复失败保留备份并报告路径。
- `gui.py`：界面只编排后台任务；保留 QThread 到真正 finished，再处理结果。停止时等待后台网络请求在超时内结束。

MD5 用于用户可核对的清单，同时 SHA-256 校验内容。首版信任 HTTPS 及配置的公开 GitHub 仓库，没有独立离线签名密钥体系。不要把更新来源改为不信任的仓库。

## 数据保留与恢复

仅旧清单列出、且未被本地修改的过期文件会删除；未知文件、用户照片、运行状态不删除。Windows 可修改配置/模板保留已有内容；macOS Resources/Frameworks 中的签名资源视为发布默认值，用户运行状态仍保留在用户目录或 MacOS 中的覆盖文件。所有新文件重名冲突都会中止而不是覆盖未知用户文件。

macOS 安装后验证三个 bundle 的代码签名。目录结构发生不兼容的「文件/链接改为目录」等冲突时，更新器会保留旧版本并要求完整安装，不递归删除未知目录。

根目录 `.superbird-update/transaction.json` 和同目录 UUID 备份用于恢复。勿手动删除正在恢复的目录。更新器独立副本位于系统临时目录，`.superbird-update/helper.json` 保存其位置；自更新未完成时仍可从该副本运行 `--root <安装目录> --recover`。恢复前需关闭两个应用。系统在 Python 入口运行前可能已加载部分库；若在替换的极短窗口内新启动造成 Windows 占用，安装会失败回滚，不尝试强行覆盖锁定库。

日志和跳过版本偏好存储在当前用户的 `SuperBirdUpdater/<安装身份>/` 下，macOS 位于 `~/Library/Application Support`，Windows 位于 `%LOCALAPPDATA%`。日志轮转。下载失败保留已验证文件以便重试；安装成功后 GUI 清理该版本下载缓存。

## 命令行和验收

```text
SuperBirdUpdater --root <安装目录> --check
SuperBirdUpdater --root <安装目录> --prepare <缓存目录>
SuperBirdUpdater --root <安装目录> --prepare <缓存目录> --allow-full
SuperBirdUpdater --root <安装目录> --apply <缓存目录>
SuperBirdUpdater --root <安装目录> --recover
SuperBirdUpdater --diagnose
```

`--apply` 将控制权交给目录外独立安装器，输出 PID 和日志路径；安装结果查看日志及 `installed-update.json`。开发环境可用 `python -m SuperBirdUpdater`。

测试：`QT_QPA_PLATFORM=offscreen <repo>/.venv/bin/python3 -m pytest SuperBirdUpdater/tests build_tools/tests`。含真实本地 HTTP 服务、真实 Qt 子进程、安全退出阻断、内核锁竞争、损坏文件、回滚/断电注入、链接与硬链接保护。macOS 的目录浏览测试应使用用户目录内可见的 `--basetemp`，避免系统隐藏 `/private/var` 路径影响旧测试。

Windows 打包/更新以及公开 GitHub Release 端到端下载仍需在对应环境验收；本地 HTTP 测试不能替代这两项。

具体结果见 [验收记录](VALIDATION.md)。`build_tools/smoke_update.py dist` 在临时安装目录验证真实 macOS 打包更新器的跨版本更新和自替换；BirdStamp 的 `--check-runtime` 验证包内模型结构与 CPU 推理，使用随机权重及临时设置目录。
