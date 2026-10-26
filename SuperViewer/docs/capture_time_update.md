# 从同名 RAW 更新拍摄时间

单独更新拍摄时间通过下方 CLI 或核心 API 调用，右键菜单已移除该入口；菜单仍提供“从同名 RAW 更新所有信息…”。

1. 指定需要处理的 PNG、JPG/JPEG 照片。
2. 通过 `--raw-directory` 指定保存 RAW 的目录。
3. 程序递归扫描该目录及所有层级的子目录（包含隐藏目录），寻找文件名主体相同的 RAW，例如 `DSC01234.jpg` 对应 `DSC01234.ARW`。忽略大小写，其他选中格式不处理。

保留的控制器兼容调用在后台运行，完成后同步列表和照片信息页。全部成功不弹框；有失败时显示成功/失败数量，以及每个失败文件的完整路径和原因，可选中文本复制。下述选择和预览行为适用于控制器调用。

每次结果返回后，本轮照片只保留“未找到同名 RAW”的选中状态，方便再次选择其他 RAW 目录继续更新；已更新成功及其他失败的照片取消选择，其他失败仍列在报告中。全部成功后清空本轮选择，当前预览和未保存的备注保持不变。

拍摄时间保存到照片的同名 **XMP 侧车**，不改动 PNG/JPG、RAW 的内嵌元数据或文件系统时间。同目录同名的 PNG 和 JPG 共用一份 XMP。保留已有鸟名、备注、标签和其他元数据。

RAW 时间读取优先级为 EXIF `DateTimeOriginal`，缺失时使用 `CreateDate`，同时保留相应亚秒和时区；不使用文件创建/修改时间，也不读取 RAW 的侧车。没有同名 RAW、找到多个同名 RAW（包括不同子目录或不同 RAW 扩展名）、无有效时间、损坏 XMP 或写入失败都会列入失败报告。扫描或读取期间照片、RAW 或 XMP 被修改时，该文件会失败并提示重试。符号链接不参与搜索，避免循环遍历。

## 命令行

从仓库根目录使用开发环境执行相同处理逻辑：

```powershell
.\.venv\Scripts\python.exe -m SuperViewer.superviewer.capture_time_update --raw-directory "D:\Photos\RAW" "D:\Photos\Export\DSC01234.jpg" "D:\Photos\Export\DSC01235.png"
```

macOS 使用 `.venv/bin/python3` 及对应路径。每个文件输出一行 JSON，包含 `source`、`success`、`raw_source` 和 `error`；全部成功退出码为 0，有失败为 1。

## 从同名 RAW 更新所有信息

列表或缩略图多选图像后，右键 **拍摄信息 → 从同名 RAW 更新所有信息…**，选择 RAW 目录。
程序沿用上述递归同名匹配规则，把匹配 RAW 旁的整份 XMP 原样复制到每张选中图像的目录，
使用选中图像的同名 `.xmp`；已有大小写不同的 `.XMP` 会原位替换。
支持共享格式表中的全部图像格式（包括 PNG/JPEG、PSD、HEIF、TIFF 和 RAW），不处理视频。

**目标已有 XMP 的全部内容会被覆盖**，包括鸟名、标签、评分、备注、拍摄信息和编辑参数；
不是逐字段合并，也不会额外从 RAW 内嵌 EXIF 提取信息。未知命名空间、中文和文件格式保持字节一致，
源 RAW、源 XMP、选中图像及 `report.db` 不被改写。同目录同名图像共用的 XMP 只复制一次；
源与目标已是同一份 XMP 时保持原文件。

复制先写入同目录临时文件，检查源 XMP 的 XML/RDF，再原子替换目标。无 RAW、多个同名 RAW、
无 XMP、源 XMP 无效、符号链接侧车、并发修改或复制失败均逐张报告，保留未完成替换的目标文件。
已损坏的目标 XMP 可以被有效的源 XMP 整份替换。
成功后同步列表、标签及信息面板，清除旧字段缓存与旧标签撤销历史，保留预览和未提交的备注草稿。
选中状态与拍摄时间操作相同：未找到 RAW 的保留选择，便于更换目录重试。两种更新任务互斥，
全部成功不弹框，部分失败显示可复制报告。

CLI（macOS；Windows 使用根 `.venv\\Scripts\\python.exe`）：

```sh
.venv/bin/python3 -m SuperViewer.superviewer.raw_xmp_update --raw-directory "/Photos/RAW" "/Photos/Export/DSC01234.jpg"
```

CLI 使用同一复制核心和上述 JSON/退出码规则，不需要图形界面或 ExifTool。
