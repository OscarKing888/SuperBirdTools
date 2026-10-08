# 从同名 RAW 更新拍摄时间

1. 在 SuperViewer 列表或缩略图中选择 PNG、JPG/JPEG 照片。
2. 右键选择“从同名 RAW 更新拍摄时间…”，指定保存 RAW 的目录。
3. 程序递归扫描该目录及所有层级的子目录（包含隐藏目录），寻找文件名主体相同的 RAW，例如 `DSC01234.jpg` 对应 `DSC01234.ARW`。忽略大小写，其他选中格式不处理。

更新在后台运行，完成后同步列表和照片信息页。全部成功不弹框；有失败时显示成功/失败数量，以及每个失败文件的完整路径和原因，可选中文本复制。

每次结果返回后，本轮照片只保留“未找到同名 RAW”的选中状态，方便再次选择其他 RAW 目录继续更新；已更新成功及其他失败的照片取消选择，其他失败仍列在报告中。全部成功后清空本轮选择，当前预览和未保存的备注保持不变。

拍摄时间保存到照片的同名 **XMP 侧车**，不改动 PNG/JPG、RAW 的内嵌元数据或文件系统时间。同目录同名的 PNG 和 JPG 共用一份 XMP。保留已有鸟名、备注、标签和其他元数据。

RAW 时间读取优先级为 EXIF `DateTimeOriginal`，缺失时使用 `CreateDate`，同时保留相应亚秒和时区；不使用文件创建/修改时间，也不读取 RAW 的侧车。没有同名 RAW、找到多个同名 RAW（包括不同子目录或不同 RAW 扩展名）、无有效时间、损坏 XMP 或写入失败都会列入失败报告。扫描或读取期间照片、RAW 或 XMP 被修改时，该文件会失败并提示重试。符号链接不参与搜索，避免循环遍历。

## 命令行

从仓库根目录使用开发环境执行相同处理逻辑：

```powershell
.\.venv\Scripts\python.exe -m SuperViewer.superviewer.capture_time_update --raw-directory "D:\Photos\RAW" "D:\Photos\Export\DSC01234.jpg" "D:\Photos\Export\DSC01235.png"
```

macOS 使用 `.venv/bin/python3` 及对应路径。每个文件输出一行 JSON，包含 `source`、`success`、`raw_source` 和 `error`；全部成功退出码为 0，有失败为 1。
