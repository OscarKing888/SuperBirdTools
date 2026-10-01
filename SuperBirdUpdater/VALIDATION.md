# 更新器验收记录

环境：macOS arm64、仓库共享 Python 3.13 环境。未推送或发布 GitHub Release。

已完成：

- 合入 main 的候选预览、时序关联和过滤徽章功能后，更新器与相关集成专项：116 passed、2 skipped；合并无代码冲突，共享子模块沿用 main 的已提交版本。
- 更新器、构建工具、Viewer 主题及 BirdStamp 工作区/关闭专项：78 passed、2 skipped（Windows 专属检查）。
- 全仓回归：1660 passed、2 skipped、60 subtests passed、15 failed。失败项中 13 项在未修改的 main 上复现，涉及 macOS 运行 Windows Perl DLL、原照片发送 IPC、去抖动 UI、旧缓存升级和原图预览缓存；另 2 项为整套运行中出现的 Viewer 选择/主题与 BirdStamp 恢复时关闭时序失败，两项单独及专项组合运行通过。未将这些失败标为更新器验收通过，也未修改无关功能来掩盖失败。
- macOS `build_all.sh` 完成三应用打包、增量分卷与完整安装 ZIP；`--check-about` 验证两个打包应用身份及资源，独立更新器 `--diagnose` 成功。
- 打包版 BirdStamp `--check-runtime` 从包内模型结构构建 YOLO 并完成 CPU 推理，Torch 2.10.0；使用临时设置目录和随机初始化权重，不下载模型，不触碰用户设置。未验证预训练权重效果或 CUDA。
- `build_tools/smoke_update.py dist` 使用临时安装副本执行真实打包更新器的 check → prepare → 独立 apply，更新三个 bundle（含更新器自身），中文资源内容正确、三套代码签名校验通过，更新后再次检查无新版本。
- 本地 HTTP 服务覆盖严格 Range、重定向、整包下载显式确认、截断响应、哈希损坏和缺失附件。真实 Qt 子进程覆盖 prepare/close/resume、导出阻止双方关闭及进程退出后才安装。
- 变更 Python 编译、工作流 YAML 解析、双方 `git diff --check` 通过。

待对应环境验收：Windows 64 位真实打包、自更新、文件占用及应用重启；公开 GitHub Release 的实际网络分发。目前没有包含新清单的公开 Release，因此没有将本地 HTTP 或本地分发测试描述为线上验证。
