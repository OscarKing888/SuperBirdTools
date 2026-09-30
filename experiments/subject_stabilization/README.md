# BirdStamp：没有固定背景的局部主体稳定实验

> **当前状态：离线算法原型，不是已经接入 GUI 的产品功能。**
> 本报告使用六张真实照片，按 **三组各两张连续照片** 分别验证。半透明选区由人工指定；特征点跟踪、异常点筛除、位移估计与图像补偿由代码实际计算。没有运行 YOLO/SAM，没有生成中间帧，没有重绘鸟或背景。

**开发入口：** [实现方案与验收标准](IMPLEMENTATION_PLAN.md) · [算法核心](core.py) · [离线 CLI](demo.py) · [样例选区](sample_manifest.py) · [完整测量数据](results/report.json) · [单元测试](test_core.py)

## 1. 实验回答什么问题？

照片背景全部是水面，没有明确的静止景物；目标鸟会抬头、蓬羽、张翅，其他鸟也在运动。此时可以做 **选定局部参考的主体稳定**，但不能仅凭图像宣称已经恢复真实相机运动，也不能要求活动中的鸟所有部位同时固定。

本次结论：

- 三组内部都找到了支持平移补偿的局部对应点。
- 第三组头部和双腿的纵向位移相差约 **12.38 px**；一个平移不能让二者同时重合。锁定哪一部分属于稳定目标的选择，而不是哪一部分天然代表相机运动。
- 当前应用已有 YOLO 鸟体检测，后续应复用它定位目标、缩小搜索范围，不应直接把整鸟框的中心和宽高当作精确稳定参数。
- 这次用双腿附近的选区作为参考，只适用于本次素材的实验设置；不代表鸟迈步、浮水、飞行时也应该锁腿。

### 输入照片

| 组别 | 参考帧 | 待补偿帧 | 本次验证范围 |
| --- | --- | --- | --- |
| A | `DSC09864.JPG` | `DSC09865.JPG` | 两帧局部平移配准 |
| B | `DSC09879.JPG` | `DSC09880.JPG` | 两帧局部平移配准，背景有其他运动鸟 |
| C | `DSC09911.JPG` | `DSC09912.JPG` | 展翅姿态下的局部运动差异 |

测量坐标为上传图片的 **2048 × 1365 像素**。三组分别处理，没有把跨组姿态跃变伪装成已经验证的六帧连续跟踪。

## 2. 六帧 DEBUG MASK：哪些区域参与计算？

| 标记 | 含义 |
| --- | --- |
| 绿色区域、绿色点 | 双腿局部；通过检查的对应点参与最终平移估计 |
| 黄色区域 | 头部与嘴部，用于检查其运动与双腿是否一致 |
| 蓝色区域 | 胸腹局部，作为运动对照，不参与本次最终补偿 |
| 红色区域／红叉 | 动作或干扰范围，或被剔除的异常对应点；以图内图例为准 |

每一行是一组。**右列仍是未补偿的下一帧**，用于显示跟踪结果；右列彩色选区由参考选区按该局部的估计位移投影，不是逐帧自动分割的轮廓。

![六帧半透明选区与真实跟踪点](assets/01_six_frame_masks.avif)

[单独查看六帧 MASK](assets/01_six_frame_masks.avif)

**人工与自动的边界：** 当前原型没有自动识别“腿”“头”“躯干”。这些名字来自人工选区。算法只追踪选区里的图像特征并计算运动一致性，不拥有鸟类解剖语义。

## 3. 实际算法流程

```text
目标鸟位置：现有 YOLO / 用户选择（正式接入时复用）
  ↓
参考帧的局部选区（本次人工指定）
  ↓
Shi–Tomasi 选点 → 金字塔 LK 前向跟踪 → 反向检查
  ↓
剔除往返误差、越界和位移异常的对应点
  ↓
分别估计左右腿的局部运动，检查区域间一致性
  ↓
共同平移共识 → 接受 / needs_keyframe
  ↓
对整张待补偿照片施加反向平移
  ↓
共同有效区域裁剪，避免边缘黑边
```

当前核心实现位于 [core.py](core.py)，主要接口为 `Options`、`track_region`、`analyze_pair`、`consensus_translation` 与 `warp_translation`。

平移共识采用有界候选点的确定性最大共识搜索，再以中位数细化；不是自由仿射、单应性或非刚性变形。两组选定局部的运动不一致、可靠点不足或共同共识比例不足时，返回 `needs_keyframe`，不会静默改用水纹匹配。

### 真实特征点匹配

以下连线来自计算得到的对应点坐标，不是为了展示而虚构的匹配。具体原始轨迹可通过 CLI 导出的 `tracks.npz` 检查。

![三组实际局部特征点匹配](assets/02_actual_feature_tracks.avif)

[单独查看匹配图](assets/02_actual_feature_tracks.avif)

### 原片与补偿后的慢速交替对照

左侧为原片，右侧为局部主体稳定后。每组只有两个真实输入帧，动图是慢速循环，不包含插帧。显示的是便于观察的局部视窗；实际补偿对整张照片使用同一平移。

![三组原片与局部稳定后的慢速对照](assets/01_three_pairs_before_after.avif)

[单独查看三组对照动图](assets/01_three_pairs_before_after.avif)

## 4. 测量结果与解释

下表补偿量施加在每组的第二张照片上。横向正数为向右，纵向正数为向下；保留两位小数便于阅读，完整精度见 [results/report.json](results/report.json)。

| 组别 | 横向补偿 dx | 纵向补偿 dy | 共识内点／有效匹配 | 内点残差中位数 |
| --- | ---: | ---: | ---: | ---: |
| A：09864 → 09865 | +3.42 px | −15.36 px | 34 / 43 | 1.22 px |
| B：09879 → 09880 | −5.97 px | +2.65 px | 40 / 42 | 0.60 px |
| C：09911 → 09912 | −6.33 px | −16.83 px | 53 / 62 | 1.47 px |

**残差是参与拟合的局部特征与模型的一致性，不是独立的真实相机运动精度。** 目前也没有盲测素材上的通用成功率，不能把这三组结果推广为所有鸟类动作都能自动稳定。

当前 `scale_applied = 1.0`、`rotation_applied_degrees = 0.0`。没有自动尺寸稳定，也没有自动扶正身体方向。

## 5. 第三组：姿态变化为什么不能被“全部对齐”？

第三组不同局部的实测位移为：

| 参考局部 | 横向位移 | 纵向位移 |
| --- | ---: | ---: |
| 头部／嘴部 | +7.39 px | +4.45 px |
| 双腿共同运动 | +6.33 px | +16.83 px |

这里是 **参考帧 → 下一帧的测量位移**，不是补偿量。二者在纵向相差约 **12.38 px**。

![第三组姿态与局部运动诊断](assets/03_pose_vs_motion.avif)

[单独查看姿态诊断图](assets/03_pose_vs_motion.avif)

### 原片、锁头、锁腿三列对照

绿色短横线固定在参考帧腿部位置，便于观察不同稳定目标的结果。

![第三组原片、锁头与锁腿对照](assets/02_head_vs_legs.avif)

[单独查看锁头与锁腿动图](assets/02_head_vs_legs.avif)

锁头会使头部局部更稳定，双腿仍有相对位移；锁腿会使站姿参考更稳定，头、颈与翅膀仍可活动。本次采用锁腿后，头部相对运动约为 **(+1.06, −12.38) px**。这些变化可能包含真实姿态变化与测量误差，没有继续用局部拉伸把它们消掉。

**如果要求头、腿、翅尖全部固定，就不是本实验定义的主体稳定，而是在修改鸟的动作或形状。**

## 6. 接入现有 YOLO 和 BirdStamp 的方式

YOLO 继续负责目标定位。本次 `demo.py` 的可选 `target_box` 只是归一化目标框输入，会限制参考 MASK；它尚不是完整的检测缓存适配、目标身份关联或逐帧目标框约束实现。

建议增量增加 `subject_local` 策略，保留现有参考区域稳定流程。先实现用户选择目标与局部锚区、可靠性检查和关键帧修正，再评估自动候选区域与自动选择。不要根据每帧整鸟框中心／面积直接输出位移或缩放。

现有代码接入位置包括：

- [去抖动策略接口](../../SuperBirdStamp/birdstamp/image_dejitter/de_jitter_strategy.py)
- [现有参考区域稳定策略](../../SuperBirdStamp/birdstamp/image_dejitter/reference_region_stabilization_strategy.py)
- [策略注册](../../SuperBirdStamp/birdstamp/image_dejitter/strategy_registry.py)
- [序列分析](../../SuperBirdStamp/birdstamp/export_stage/sequence_analysis.py)
- [检测与裁剪核心](../../SuperBirdStamp/birdstamp/gui/editor_core.py)
- [BirdStamp 架构说明](../../SuperBirdStamp/docs/ARCHITECTURE.md)

> **特别注意坐标符号：** 当前实验直接 warp 整图，补偿量为测量位移的负值；应用通过移动源图裁剪中心实现稳定时，裁剪中心通常应随测量位移同向移动。不能把本实验的 `correction` 不加转换地写入现有 `stable_center`。详见 [实现方案](IMPLEMENTATION_PLAN.md)。

## 7. 本地复现实验

需要 Python、NumPy、Pillow 与 OpenCV。复用仓库现有 `.venv`；不要为本实验重复安装相互冲突的 OpenCV wheel，也不需要下载新的模型权重。

先切换到本实验分支，在仓库根目录运行。照片放在自己的本地目录，保持上表六个文件名；输出目录应与输入目录分离。

### Windows / PowerShell

```powershell
.\.venv\Scripts\python.exe experiments/subject_stabilization/sample_manifest.py
.\.venv\Scripts\python.exe experiments/subject_stabilization/demo.py --images "D:\Photos\six_frames" --manifest experiments/subject_stabilization/manifest.json --out "D:\Temp\bird_stabilization_debug"
.\.venv\Scripts\python.exe -m unittest discover -s experiments/subject_stabilization -p "test_core.py" -v
```

### macOS / shell

```bash
.venv/bin/python3 experiments/subject_stabilization/sample_manifest.py
.venv/bin/python3 experiments/subject_stabilization/demo.py --images "$HOME/Pictures/six_frames" --manifest experiments/subject_stabilization/manifest.json --out "$HOME/Downloads/bird_stabilization_debug"
.venv/bin/python3 -m unittest discover -s experiments/subject_stabilization -p 'test_core.py' -v
```

`sample_manifest.py` 使用针对这六张照片人工定义的归一化选区。更换素材必须重新选择参考区域，不能把它当作通用鸟体自动分区模型。

CLI 为每组输出基础参考／跟踪叠加图、成功时的补偿图与共同裁剪图、`report.json` 和 `tracks.npz`。本页的大幅排版图与对照动图是基于这些结果另外整理的展示材料；当前 CLI 并没有自动生成本页全部排版。

## 8. 已验证与尚未验证

已完成：三组真实照片的独立两帧实验，以及最初的 **7 项合成单元测试**，覆盖已知平移方向、异常点、空图、区域冲突、MASK 外运动、选区重叠及错误输入。

尚未完成：GUI 集成、现有 YOLO 的实际适配、长序列漂移控制、跨组姿态衔接、尺度变化补偿、严重失焦恢复、多鸟遮挡交叉、完整项目回归、Windows/macOS 打包验证。

需要保留的限制：

- 鸟的局部也可能真实移动；本实验测得的是所选局部在图像中的位移。
- MASK 限制的是特征点中心，LK 邻域窗口仍可能包含旁边水面，不能声称完全排除了背景影响。
- 参考区域并未被自动语义分割。右帧 MASK 的投影也不能被解释为准确的逐帧分割。
- 仅有两帧时无法可靠估计一段运动的平滑趋势；本次验证的是局部锁定，不是自然跟随轨迹规划。
- 改变空间位置不会恢复失焦丢失的细节，也不会修复照片中本来就存在的运动模糊。

## 9. 开发交接与展示资源

后续模块设计、坐标约定、Qt 线程与取消机制、缓存、失败状态、CLI、分阶段实施及验收要求，统一见 **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)**。该文档描述待实现方案，不表示其中功能已经完成。

本页图片为实际 DEBUG JPG/GIF 转换得到的轻量 AVIF 展示副本；静态图经过有损压缩，动画保留原有两个展示帧与时序。显示尺寸与原分析分辨率不同，数值仍来自 2048 × 1365 输入照片。浏览器若未播放动图，可点击其下方链接查看资源。

只发布了用于开发讨论的派生调试图、动画与数据；没有把六张独立原片、字体文件或模型权重提交到仓库。本次工作仅在实验分支，不修改 `main`、产品 GUI、检测器或 `app_common`。
