# SuperViewer 目录操作与文件拖放

## 交互与组件

```text
目录树 L4                         文件列表 / 缩略图 L4
├─ 目录右键 → 新建子目录… L5       └─ 多选 → 拖动原文件 + 同名 XMP
│  └─ QInputDialog 输入名称             ↓ 默认移动 / 实体 Ctrl 复制
└─ 目标目录：主题高亮描边 ←─────────────┘
   └─ 悬停展开、边缘滚动 → 系统操作窗口 → 状态栏结果
```

沿用共享目录浏览器的懒加载、目录菜单与滚动归属，新增原生名称输入框；输入取消不创建目录。
创建成功仅向已有父节点插入新节点并展开，当前浏览目录、文件多选和已有展开子树保留。
名称校验遵守 Windows/macOS 的共同文件名限制，不允许路径或递归创建。

`app_common` 提供 `directory_tree_class`、`file_table_view_class`、`thumbnail_view_class`
工厂，默认仍为原有 Qt 视图。Viewer 的派生模型仅增加可拖动标志，原有稀有度列、徽章、排序
和缩略图缓存仍由原模型实现。两个拖动视图复用原生选择处理，按下已选行不丢失多选；
不修改方向键播放事件。目录树只接收本窗口当前拖动令牌，空白、占位、外来或过期拖动被拒绝。
目录选择记忆和待剪切显示仍保留；延迟滚动定位由面板持有的定时器执行，关闭时拒绝访问模型，
面板销毁时取消回调，避免文件操作刷新后迅速关窗访问已释放的模型。
高亮仅绘制边框，不改变当前目录或文件预览。实体 Ctrl 在 macOS 映射为 Qt MetaModifier，
Windows 映射为 ControlModifier；放下时读取当前状态，因此拖动途中可以切换复制/移动。

## 数据与系统操作

控制器复用浏览器的实际源路径与 XMP 收集接口，拒绝把缓存预览当作源文件，
后台提交前再次通过严格同名侧车解析器获取当前 XMP，RAW/JPEG 共用侧车只提交一次。
同目录移动跳过；同目录复制也由系统决定。图片和 XMP 是同一系统批次的独立文件，
具体覆盖、跳过、保留两份和取消均遵循系统窗口中用户对各文件的选择；不自动覆盖、不回滚系统结果。

Windows 使用 [SHFileOperationW](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shfileoperationw)，
STA worker、Unicode 双 NUL 路径数组、`FOF_ALLOWUNDO`，检查返回值和 `fAnyOperationsAborted`；
不设置静默、自动覆盖或自动改名标志。系统窗口不归属隐藏中的 Qt 主窗口，关闭等待时仍可取消。
macOS 使用 CoreServices Apple Event：`core/move` 或 `core/clon`，文件 URL 描述符，
目标参数 `insh`，等待完成并允许 Finder 交互；所有描述符在成功/失败时释放。
参数选型参照 [Apple Developer Forums 的 Finder 原生冲突窗口验证](https://developer.apple.com/forums/thread/804786)。
打包加入 Finder 自动化用途说明和 Apple Events entitlement；权限拒绝明确显示系统设置入口，
不回退到没有系统冲突对话框的文件复制实现。

后台线程独立于当前目录，运行期间不能启动第二批。实际 `QThread.finished` 后，
按已消失的源文件清理预览、标签撤销记录和只读报告缓存的会话标记；`report.db` 不写入。
当前目录涵盖源或目标时按实际磁盘刷新，系统取消也处理部分完成，不删除尚存在的源文件。
主窗关闭等待系统窗口结束及真实线程完成，不强杀系统文件操作。

这是依赖交互式系统冲突窗口的拖放能力，无新增 CLI；核心系统适配与目录校验不依赖 Qt，
可由其它入口复用。原有剪贴板粘贴与归档事务保持原策略。

## 覆盖

- `test_directory_file_transfer.py`：中文目录与非法名称、同名 XMP/共用侧车去重、
  Windows 参数/取消/STA 回收、Finder 描述符/权限错误/交互标志、两个视图真实鼠标多选拖动、
  真实 Qt drop 默认移动及实体 Ctrl 复制、取消部分完成、关闭生命周期、深浅色和放大字体截图。
- 共享目录菜单/主题/扫描、剪贴板侧车、缓存/元数据/过滤、预览/长按播放、目录选择记忆、
  待剪切提示、延迟定位关闭/销毁生命周期与 BirdStamp 导航回归。
- 默认测试用临时文件和替代系统执行器，不弹系统操作或权限窗口；Windows 64 位系统交互须在 Windows 实机验证。
