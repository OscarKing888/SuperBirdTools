# Camera RAW 与调色

SuperViewer 的「Camera RAW」「调色」两个图片信息页签共用一份编辑会话。处理顺序为：选定底图 → Camera RAW → 从上到下执行已启用的调色分组 → 预览或导出。设置修改不会覆盖原照片。

## 功能范围

| Camera RAW 分组 | 可用功能 |
|---|---|
| 光线 | 曝光、对比度、高光、阴影、白色色阶、黑色色阶 |
| 颜色 | 相对色温、色调、自然饱和度、饱和度 |
| 效果 | 纹理、清晰度、去朦胧、三种暗角样式及形状、颗粒数量／大小／粗糙度／种子 |
| 曲线 | 高光／亮调／暗调／阴影与分区、RGB 和各通道点曲线 |
| 颜色混合器 | 红橙黄绿青蓝紫洋红八色的 HSL |
| 色彩分级 | 阴影／中间调／高光／全局色轮、亮度、混合、平衡 |
| 细节 | 锐化数量／半径／细节／蒙版、明亮度及颜色降噪 |

调色将 16 类功能平铺为共享可折叠分组：亮度／对比度、色阶、曲线、曝光、自然饱和度、色相／饱和度、色彩平衡、黑白、照片滤镜、通道混合器、颜色查找 LUT、反相、色调分离、阈值、渐变映射、可选颜色。默认全部展开，勾选组内“启用”后直接调节；取消启用保留参数，收起不关闭效果。顶部可全部展开或收起，底部撤销、重做、重置、导出始终可用。

处理顺序会影响结果（例如曝光与 LUT 互换），按面板从上到下执行，首次勾选的先后不改变默认排列。已有配方保留原来的顺序和重复步骤；每组“操作”菜单可上移、下移、复制或移除调整，最多 64 步。LUT 支持 `.cube`、`.3dl`、`.look` 及上游八个内置风格。

每个组标题右侧都有“＋”菜单，收起时仍可使用。从菜单选择任意处理类型，新步骤将在当前组之后执行，自动启用并展开，也可以重复添加同类处理。一次添加可一次撤销；打开或取消菜单不改变参数。如果当前组尚未使用，会保留为禁用步骤，以固定插入位置，其效果不会被启用。

调色分组上方只有“一键自动调节”按钮。点击后统一分析亮度、反差和颜色，把结果填入下方六类基础组：**亮度／对比度、色阶、曲线、曝光、自然饱和度、色彩平衡**。各组按实际处理顺序计算，前一组的效果会参与下一组分析。

结果直接更新每类第一个组的滑块、数值和曲线，并展开相应组，继续手动微调即可。色阶／曲线显示 RGB 总通道，色彩平衡显示中间调；无需调整的参数保留中性值。已有基础组原位重算，缺少的组按面板顺序加入，整次操作可一次撤销；连续点击不会叠加重复步骤。Camera RAW、重复添加的额外步骤和黑白／反相／LUT 等风格化效果保留。最多 64 步，需为缺少的基础组预留位置。

计算期间改参数或切底图时，旧结果不会覆盖新调整。纯色、透明或极低反差图片可能无需变化，面板会提示。自动算法是本项目实现，不保证与 Photoshop 结果相同。

两页都有 RGB 直方图及阴影／高光溢出比例。曲线可点击添加、拖动调整、方向键微调，Delete 删除内部点；色轮支持鼠标和方向键。一次滑块拖动记作一次撤销。

这是从 PhotoCraft 移植的 RGB 图像处理能力。RAW 继续由 rawpy／LibRaw 解码为 16 位数据，然后在 SDR/sRGB float32 管线中调整。色温是相对调整，不是相机绝对 Kelvin 标定；高光调整不等于传感器域高光恢复。首版不包含相机／镜头配置文件、HDR／广色域工作流、局部蒙版、匹配／替换颜色或 HDR 色调。Camera RAW 的细节降噪与已有 NAFNet AI 降噪是两项不同功能。

## 选择底图和比较效果

- 面板「编辑底图」选择原始文件，或通过来源验证的降噪成片。RAW 的原始文件会解码真实 RAW 像素。
- 每个原始／降噪版本独立保存参数与撤销历史。选中的降噪版本固定到具体文件及大小／修改时间；重新降噪不会静默替换它。可显式「查找最新降噪版本」或选择已有成片。
- 预览工具栏的源循环为「默认预览 → 显示 RAW → 显示降噪 → 编辑效果」。普通图片跳过 RAW，视频隐藏图片源控件。
- 编辑效果下的「调整前／后」比较同一底图应用参数之前／之后。它不会把降噪底图的“前”切回相机 JPEG。
- A/B 各自保留预览模式、前后状态、缩放和平移；同一照片共享配方。参数面板跟随活动侧。
- 编辑预览与当前底图使用相同的完整像素尺寸；「调整前／后」同样使用原尺寸，可放大至 100% 检查细节。调参时后台渲染，完成前保留当前画面并显示更新提示，保持缩放和平移。大图或复杂效果需要等待计算完成。直方图和自动调节分析仍可使用有界代理；长按方向键期间只显示已有缩略图，不启动编辑加载或渲染。

## 参数、草稿与资产

调整结束约 800 ms 后自动保存到原照片同 stem 的 `.xmp`，不需要另存一张临时照片。撤销／重做限当前会话，最多 100 条。照片 JSON 侧车、原图和 report.db 均不写入。

应用私有字段 `XMP-superpicky:photo_edit_state` 保存版本化配方；同 stem RAW/JPEG 在一个 XMP 内按完整文件名分开记录，不互相套用。未知元数据、已有标签及 report.db 兼容补全保留。保存会检查原图指纹与 XMP 配方修订号；外部修改冲突时保留当前草稿并明确提示，不覆盖他人修改。

恢复草稿位于用户状态目录的 `photo_edit/recovery`，不放照片旁。正常保存后清理匹配草稿；磁盘失败时可重试保存，或先复制配方。若 XMP 和恢复草稿都无法写入，关闭窗口会保留编辑会话。菜单「重新载入 XMP」会询问是否放弃当前未保存调整。若原图本身被替换，旧记录不会自动套用，应先保留旧 XMP 再处理记录。

「参数与恢复」支持复制／粘贴整份配方、Camera RAW 设置导入／导出、重试保存、重新载入及清理缓存。导入兼容的 Camera RAW XMP 字段时会报告不支持项；不保证与 Adobe 成片像素一致。原生配方是应用私有格式，不能只凭存在于 XMP 就认为其他编辑器能识别全部效果。

外部 LUT 规范化后按 SHA-256 存在照片目录 `.superbird-assets`，XMP 存引用。应用内照片复制／移动／归档会携带所需 LUT；原目录资源可能被其他照片共用，因此不会自动删除。手动用系统工具搬照片时应同时携带 XMP 和该资源目录。缺失或校验不符会报错。

文件操作前若参数尚在保存，状态栏提示保存完成后重试。后台移动／归档期间编辑暂不可用。降噪成片是独立长期资产，不随原图自动搬家；原图或成片位置改变后，旧来源校验可能失效，需要重新选择可验证的版本或重新生成，不能静默改用原始底图。

## 成片与目录规则

「用户选项 → 照片编辑」设置默认位置和格式。降噪也使用相同目录策略，但分别保存设置，默认子目录仍为 `denoised`。

| 策略 | 对 `D:/Photos/Birds/IMG.ARW` 的示例 |
|---|---|
| 原图子目录（默认） | `D:/Photos/Birds/edited/IMG_edited.tif` |
| 原图同目录 | `D:/Photos/Birds/IMG_edited.tif` |
| 原图目录的平级目录 | `D:/Photos/Birds_edited/IMG_edited.tif` |
| 固定目录 | 用户指定目录下的 `IMG_edited.tif` |
| 每次询问 | 导出时选择目录 |

默认 TIFF 16 位，也可选 PNG 16 位或 JPEG 8 位／质量 95。同名图片或 XMP 已存在时自动分配编号，不覆盖原图或已有输出。先在目标目录完成临时文件和元数据，再成对发布图片与 XMP；失败回滚本次输出。导出包含 `photo_edit_baked` 来源／底图／配方快照，重新打开成片不会把已经烘焙的参数执行第二遍。

预览缓存默认内存 512 MiB、磁盘 1 GiB，可调整或清空，磁盘只存有界代理；缓存写失败不阻止编辑。单次最大 6400 万像素，并按可用内存做保守检查；不会为了导出成功而偷偷缩小图片。首版不支持 PSD、动画和 PQ／HLG HDR 输入。

## CLI 与共享 API

在仓库根目录使用 `.venv` 的 Python；Windows 替换为 `.venv\Scripts\python.exe`：

```bash
.venv/bin/python3 -m app_common.photo_edit info
.venv/bin/python3 -m app_common.photo_edit show /photos/IMG.ARW
.venv/bin/python3 -m app_common.photo_edit auto-adjust /photos/IMG.ARW
.venv/bin/python3 -m app_common.photo_edit render /photos/IMG.ARW --format tiff --json
.venv/bin/python3 -m app_common.photo_edit render /photos/IMG.ARW --input original --output-mode fixed -o /exports
.venv/bin/python3 -m app_common.photo_edit import-settings /photos/IMG.ARW /settings/CameraRaw.xmp
.venv/bin/python3 -m app_common.photo_edit export-settings /photos/IMG.ARW /settings/new.xmp
```

`render` 可传多张照片、`--recipe` 显式配方 JSON 或单张 `--denoised` 验证输入；它不改源照片参数。`import-settings` 和 `auto-adjust` 是明确写入照片 XMP 的命令；自动调节默认 `--mode all`，与界面一键综合调节相同。CLI 保留旧 `tone`、`contrast`、`color` 模式供已有脚本使用。处理已保存的当前底图，没有变化时不写入。Ctrl+C 取消，已完成的成片保留。GUI、CLI 都调用同一核心：

```python
from app_common.photo_edit import RecipeStore
from app_common.photo_edit.pipeline import render_recipe
from app_common.photo_edit.export import export_recipe
from app_common.image_output_paths import ImageOutputOptions

state = RecipeStore().load(photo_path)
preview = render_recipe(state, proxy=True)  # float32 RGBA，不依赖 Qt
output = export_recipe(state, ImageOutputOptions())
```

## 源码构建与上游更新

安装版自带原生组件，不需要 Rust。源码开发需 Rust 1.95.0、平台 C/C++ 构建工具及共享虚拟环境中的 `maturin==1.9.6`：

根目录 `init_dev.py` 会安装固定的 Python 原生构建依赖；已有 `.venv` 可只执行下方的 pip 命令补齐。Rust 工具链仍需单独准备。打包仅在原生二进制缺失或过期时检查依赖，错误会给出当前构建解释器对应的安装命令，不在构建中自动安装工具链。入口会查找 PATH 或 `CARGO_HOME/bin`（默认 `~/.cargo/bin`）中的 rustup，再只为编译子进程选择固定工具链，兼容系统 PATH 中已有旧版 Rust 的情况。

```powershell
rustup toolchain install 1.95.0 --profile minimal
.\.venv\Scripts\python.exe -m pip install -r build_tools/requirements-photo-edit.txt
.\.venv\Scripts\python.exe app_common/native/photo_edit/tools/build_native.py
```

macOS 用 `.venv/bin/python3` 执行同一脚本，编译本机架构。所有应用 spec 经 `build_tools/photo_edit_native.py` 校验上游快照、桥接源码、ABI 和二进制摘要；缺失或过期时构建。生成文件留在当前 checkout，不装入共享 `.venv`，无需每个 worktree 新建环境。更新原生组件前退出正在使用该 checkout 的程序。

PhotoCraft 固定在 `369704ac551e8e409800e423ff550c353d5522ef`，保留九个依赖 crate 与 Camera RAW XMP 模块的原始源码。上游快照不直接改；适配仅放桥接层。更新程序、锁文件和回归步骤见 [共享原生库说明](../app_common/native/photo_edit/README.md)。

安装包可离线运行 `SuperViewer --check-photo-edit --output report.json`，检查真实 Rust 渲染、中文路径、XMP 往返、三种格式、16 位 TIFF、源文件不变及成片不重复调色。Windows 和 macOS CI 均包含该门禁。
