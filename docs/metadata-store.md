# 元数据首响应与侧车同步

两款应用共用 [`MetadataStore`](../app_common/exif_io/metadata_store.py)。SQLite 已提交的编辑是尚未同步阶段的本机持久副本；XMP 是照片可携带、可交换的最终侧车。`report.db` 仍只读，原图不写入用户元数据。

## 读取与编辑

- GUI 只读取 `peek()` 内存快照。后台 `read_many()` 读取本机 SQLite，不访问照片盘；`browser`、`full` 和显示用 profile 分别记录字段完整性，只有编辑字段的记录不能冒充完整 EXIF。
- 目录先展示有界内存/SQLite 列表及元数据快照，随后扫描、核验并增量补齐。筛选、排序及标签下拉只使用内存；活动标签过滤不再启动独立的 XML 全量 loader。
- `submit_edit()` 返回 Future；只有 SQLite 事务提交后 UI 才更新为已保存。补丁保留显式清空、零值和标签增删；未同步编辑优先于侧车、原图和报告回退。
- `remember_many()` 导入后台读取结果，校验读取起始版本，拒绝用晚到旧结果覆盖新编辑。`refresh_scope()` 只重建缓存，不删除 outbox。跨应用变化由 [`MetadataStoreWatch`](../app_common/file_browser/_metadata_watch.py) 和 BirdStamp 的 `EditorMetadataStoreWatch` 每 500 毫秒在所属工作池查询 `changes_since()`，Qt 接收后局部更新，保留备注/文件名草稿、光标、选择和预览视野。
- 清空字段在同次 XML 发布中记录 `XMP-superpicky:metadata_cleared_fields`，防止同步完成后原图或 report.db 的旧值重新出现；再次赋值清除相应记录，外部程序写入的非空侧车字段优先于残留的清空记录。数字 `0`、`False` 不是空值。

## 持久化与生命周期

数据库为 `metadata-store-v1.sqlite3`：Windows 位于 `%LOCALAPPDATA%/SuperBirdTools/Data`，macOS 位于 `~/Library/Application Support/SuperBirdTools`；测试用 `SUPERBIRD_METADATA_STORE_DIR` 隔离。不可把它当作可随意删除的 Cache 数据库。

一个 SQL 所有者线程处理短事务，WAL + FULL 提交；源文件读取、ExifTool 和侧车锁均不占用 SQL 写事务。`snapshots` 有容量与访问淘汰边界；`resources`、`outbox` 保存版本与待同步补丁，不受缓存清理影响。`changes` 为两应用提供版本变化记录，落后于保留范围的消费者重新获取快照。

`SidecarSyncAction` 在已有 `metadata_work_pool()` 执行：取得同 stem 跨进程锁，重取最新待写版本，严格读取并合并 XML，原子发布，再确认该版本。新编辑不会被旧确认清除；失败保存错误并退避重试，重启恢复。关闭先停止协调/投递，等运行中的应用 worker 真正结束，未执行编辑保留在 SQLite。

旧 [`metadata_sqlite.py`](../app_common/exif_io/metadata_sqlite.py) 仍是可丢弃的解析/合并/目录列表缓存，`SUPERBIRD_METADATA_CACHE_DIR` 只覆盖它。其失效按资源或子树处理，读取更新访问时间；XMP 后台指纹包含 SHA-256，避免 Windows 同大小且保留 mtime 的外部修改漏检。旧缓存不迁移为用户编辑。

## 文件操作与导出

`file_operation()` 在短 SQL 事务中保护源路径/目录，拒绝该范围的新编辑，再同步受影响的 pending；整个复制、移动、改名、删除事务结束才解除保护。它在 worker 中调用，可重入，异常保留原文件和待同步编辑。生成侧车后重新解析文件配对，避免漏掉原先不存在的 XMP；目录保护包含未在当前列表中的 pending。

低层直接写入器也登记跨进程写入租约，并持同一侧车锁；文件操作等待此前写入结束，旧路径晚到写入被拒绝。直接分析结果发布后通知共享 Store 使相关快照失效。标签库迁移保留原有多文件恢复日志，准备、提交和恢复都遵守此屏障，事务结束后导入新的标签快照。

分析、拍摄时间及拼音更新由 worker 捕获该次直接写入的准确版本回执，待内存发布后交付结果。GUI 只比较内存 revision，不能重新查询侧车指纹，也不能把随后另一笔编辑的版本号附给旧结果。

从 RAW 整份替换 XMP 同样先同步目标和源的已接受编辑，再按固定顺序取得两侧系统锁，发布后通知 Store 失效旧快照。Windows 后台侧车读取使用允许移动/删除的共享句柄，读取字节后立即关闭，再进行 XML 解析；原子覆盖仍保留有界重试。

导出通过 `read_export_xmp()` 在侧车锁内取得 XML 与已提交补丁的固定快照，把补丁应用到输出 XML，不要求源目录可写。保留未知 XML 属性与标签，只将成片 XMP 写入独立侧车。

BirdStamp 成片缓存签名包含已提交元数据 revision 和侧车内容摘要，因此 XMP 尚未同步的编辑也使旧成片失效；输出侧车的摘要参与配对缓存校验。

首次写入相机字段先从原图生成临时 XMP，再单独执行赋值及语义回读，最后原子发布，避免 ExifTool 初次格式转换覆盖用户赋值。

## 诊断与回归

分别观察目录缓存首屏、扫描、指纹校验、SQLite/内存命中、源文件/XML 实读及 GUI 应用耗时；worker 总完成时间不代表首屏等待时间。热目录首屏测试阻塞扫描及源文件检查，确认缓存仍先可用；后台解除阻塞后再断言外部增删改。

性能日志入口包括 `[metadata.store.read]` / `[metadata.store.commit]` 的 SQL 和排队总耗时、`[metadata.snapshot]` 首批快照、`[metadata.read_batch]` 的各来源读取时间，以及浏览器 `apply_listing.*` / `_meta_apply`。缓存恢复阶段只恢复列表选择，自动完整预览在校验后提交一次；用户主动点击仍遵守原预览加载策略。

关键覆盖位于 `test_metadata_store.py`、`test_metadata_sqlite.py`、`test_metadata_cache_resource_versions.py`、目录快照/范围测试及两应用元数据接入测试。导出补丁与源侧车不变由 `test_export_pending_metadata.py` 验证；文件事务、删除选择和目录改名回归保留。所有用例在临时数据库、临时照片和离屏 Qt 下执行。

### 2026-10-28 Windows 验证记录

600 条隔离合成缓存记录、后台校验尚未执行时：SQLite 批量查询 13.51 ms，缓存元数据批次生成 71.22 ms，内存评级/标签筛选 0.16 ms，600 条全部命中。以禁止访问的计数钩子检查照片目录，源 `stat`、`scandir`、源文件读取和 XML 解析各为 0。该测量不包含 GUI 绘制、缩略图或真实照片盘校验，不能当作真实图库的首屏总耗时；这些阶段分别保留性能日志。

`test_directory_return_displays_metadata_before_reconcile_scan` 在列表、缩略图两种模式下阻塞后台扫描，验证目录返回后缓存可显示和筛选，GUI 对照片盘的状态查询为零。`test_exif_sql_first_response_precedes_blocked_source_validation` 验证完整 EXIF 先发缓存；`test_file_barriers_in_all_pool_slots_sync_inline_without_starvation` 覆盖工作池满载时的文件同步屏障。多进程锁、崩溃重启、失败重试、晚到结果、清空字段和中文读写也有隔离回归。

共享层最后一次完整集合为 **1002 passed、1 skipped、1 deselected**；排除的日志跨进程轮换测试已在未修改的 main 复现 Windows 文件占用。BirdStamp 元数据、模板、选择、成片缓存、XMP 导出和 GIF/video 相关集合为 **184 passed、1 deselected**；排除的 PNG/JPG 跨目录导出复用测试也已在 main 复现。完整 BirdStamp 集合在 Qt/Torch 初始化时出现访问冲突，未完成，不能视为全套通过。

Viewer 原 main 上另外确认的已有失败是：`test_badge_text_editor.py` 六项离屏控件命中，`test_bird_body_integration.py::test_fast_body_identity_reuses_cached_actual_path_repair` 两项路径用例，`test_bird_identification_controller.py::test_table_candidates_buttons_and_background_adoption` 的 Return/Enter 两项，以及 `test_video_frame_options.py` 两项离屏布局。这些问题未混入本轮修复。

Viewer 最终分批验证 **1398 passed**：目录拖放文件 40 项，其余集合 1358 项；上述 12 项 main 既有失败单独排除。一次先前合跑在目录拖放期间长时间等待，该测试存在未替换的原生警告对话框路径，现改为收集警告并严格失败，避免模态窗口挂住测试；具体警告未再重现，目录拖放与移动历史、安全检查合跑另有 78 项通过。以上为 Windows 源码环境验证，未进行 macOS 或打包产物验证。
