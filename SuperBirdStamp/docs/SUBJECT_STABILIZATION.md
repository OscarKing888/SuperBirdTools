# 去抖动识别方法

去抖动页的「识别方法」保留 **基本：参考区匹配**，新增 **高级：局部主体跟踪**。默认仍为基本方法，旧工作区缺少新字段时按基本方法恢复。高级方法适合天空、水面等背景不能作为可靠参考的序列；用户必须确认选区属于同一目标的同一局部运动。

## 使用

面板按「1. 选区 → 2. 分析 → 3. 导出」分组：参考区编辑在第一组，识别方法、补偿强度、分析进度和预览诊断在第二组，输出范围、格式、工作区选项和导出进度在第三组。

1. 在去抖动页选择识别方法，在参考原图上框选一个或多个矩形区域。
2. 基本方法可自动建议纹理选区，支持原有平移/轻微旋转设置。高级方法要求人工选择有纹理的头部、躯干或其它局部，禁用自动背景选区和旋转设置。已有鸟体框和检测缓存仍用于原图辅助观察，不额外创建模型。
3. 高级方法可选「局部锁定」或「自然跟随」。锁定保持局部的输出位置；跟随对主体轨迹做窗口内线性拟合，保留缓慢运动趋势，补偿偏离趋势的部分。两帧不能估计趋势，按锁定处理。
4. 点击「分析并预览成片」。失配保留成功前缀和失败诊断，不允许把未完成序列作为全部成功导出。切回原图，可以按原编号拖动选区修正。
5. **将失败帧的全部选区手动修正后，该帧同时成为后续片段的新关键帧**。后续跟踪以其局部纹理为参考，但构图仍使用原参考的虚拟锚点。右键撤销修正后需重新分析；旧关键帧链的诊断会失效。
6. DEBUG 开关显示实际关键帧点到当前点的连线：绿点参与拟合，红点被拒绝；颜色不表示世界静止。沿用 A/B 两侧各自的源坐标和裁切映射，辅助层不进入导出。
7. 输出仍使用整组共同画幅，也可开启并集补边。高级方法强制只平移，缩放恒为 1，旋转恒为 0；以整数裁切偏移避免重复重采样，实际应用值记录在计划中。

不要把同时活动的头、腿、翅膀强制作为共同参考。双腿只适用于这些站立样本，不是飞行、迈步、浮水场景的通用锚点。ROI 只限制角点中心，光流窗口仍可能覆盖旁边水面；小局部无纹理或两区冲突时应换参考，不能把阈值放宽到假成功。

## 核心及扩展边界

- [recognition.py](../birdstamp/image_dejitter/recognition.py)：`RecognitionStrategy` / `RegionTracker` 接口、注册表及版本化 `SubjectSettings`。基本实现包装原 `ReferenceRegionTracker`；高级实现创建 `SubjectLocalTracker`。这是当前独立序列管线的识别入口，旧 `DeJitterStrategy.stabilize()` 保持显式参考区 API 的兼容性。
- [subject_local_tracker.py](../birdstamp/image_dejitter/subject_local_tracker.py)：分格 Shi–Tomasi 角点、前后向金字塔 LK、最大位移/有限值/边界/空间覆盖检查、每区共识、区间冲突拒绝、区域等权平移。重叠 ROI 不重复选点；持续点 ID 在同一关键帧内稳定。`LocalObservation` 保存对应点、源尺寸、实际分析尺寸和双轴比例。
- [subject_sequence.py](../birdstamp/export_stage/subject_sequence.py)：人工关键帧分段、锁定/自然跟随、`SubjectFramePlan` 和共同裁切。失败不插值成图像观测，不通过旋转或缩放强行拟合姿态。有效且严格递增的拍摄时间优先，否则明确使用输入帧顺序；超过十秒的间隔需要人工关键帧确认或分开分析。
- [sequence_analysis.py](../birdstamp/export_stage/sequence_analysis.py)：复用现有有界 action 池及线程所有权；高级方法不使用基本方法的邻帧恢复搜索。参数变更/取消/关闭仍经现有 worker 身份及 token 门控，不新增 GUI 线程计算路径。
- [subject_observation_cache.py](../birdstamp/image_dejitter/subject_observation_cache.py)：会话内 32 MiB / 512 项上限，只存无像素观测。签名含算法版本、源/参考文件属性、ROI 和分析映射；改变强度、跟随模式、输出编码不重跑命中的点跟踪。原图解码和参考角点准备仍可能执行。
- [sequence_preview_cache.py](../birdstamp/gui/sequence_preview_cache.py)：整段计划及实际对应点随现有磁盘缓存保存，工作区恢复不需重新跟踪；新算法签名使旧派生缓存重新分析，不改变源文件或工作区照片。
- [editor_subject_controls.py](../birdstamp/gui/editor_subject_controls.py)：表单及中文说明，默认值来自 `config/editor_options.json`。方法/模式/窗口随全局渲染设置持久化，DEBUG 随工作区预览状态保存，不进入分析签名。

统一解码器先处理 EXIF 方向，再在整幅图上生成长边最多 2048 的分析图。没有按鸟体框逐帧缩放，实际映射为 `source = analysis * (source_size / analysis_size)`。不同原图尺寸或方向不隐式拉伸匹配，明确要求分段。原局部位移 `d` 是参考到当前的方向，输出裁切中心位移是 `+d`，等效图像补偿是 `-d`。

不自动判断“哪一只鸟”或推荐解剖部位；本版通过人工局部确认目标，复用已有检测辅助，不声称完成多目标身份关联。遮挡、换姿、超过可靠搜索范围时显式要求人工关键帧；不生成中间帧、不恢复失焦细节、不做非刚性变形。自然跟随是主体路径平滑，不是相机运动重建。

## CLI

从仓库根目录设置 `PYTHONPATH=.:SuperBirdStamp`（Windows 使用对应的分号路径及仓库虚拟环境），运行同一生产核心：

```sh
.venv/bin/python3 -m birdstamp stabilize frame1.JPG frame2.JPG \
  --reference frame1.JPG --regions roi.json --method subject_local \
  --mode lock --strength 100 --output results --debug
```

`roi.json` 示例，`index` 是显式输入序列的零起始下标，关键帧必须提供与参考区一一对应的完整位置：

```json
{
  "regions": [[0.60, 0.66, 0.62, 0.77], [0.65, 0.66, 0.67, 0.77]],
  "keyframes": [
    {"index": 1, "regions": [[0.61, 0.67, 0.63, 0.78], [0.66, 0.67, 0.68, 0.78]]}
  ]
}
```

也可仅传矩形列表。每次输出建立独立子目录，写 PNG、`report.json`，`--debug` 另存真实点轨迹 `tracks.npz`。失败也写数值诊断并以错误退出。报告仅记录文件名和匿名属性签名，不记录个人绝对路径、模型或字体；NPZ 的无效前后向误差是 NaN（不是零误差），JSON 对应 `null`。

## 验证

新增 [test_subject_local.py](../tests/test_subject_local.py) 和 [test_subject_local_ui.py](../tests/test_subject_local_ui.py) 覆盖已知正负位移、完整输出像素一致性、局部外强运动、参考区冲突、纹理缺失、尺寸映射、重叠去重、取消、强度观测复用、人工关键帧、跟随趋势、拍摄间隔、工作区/缓存往返、CLI 输出及源文件不变。

2026-09-30 使用实验的三对黑脸琵鹭照片验证，分别读取 HIF 与 JPG 原片（5616×3744），人工腿部多边形取外接矩形，分析长边 2048。两种格式的三组均通过；JPG 的实际整数裁切补偿如下：

| 参考/当前 | 源图裁切中心 dx, dy | 共同输出尺寸 |
| --- | --- | --- |
| DSC09864 / DSC09865 | -7, +42 | 5609×3702 |
| DSC09879 / DSC09880 | +16, -8 | 5600×3736 |
| DSC09911 / DSC09912 | +16, +46 | 5600×3698 |

这不是相机运动真值精度。矩形选区和区域等权与原多边形实验不同，不能要求拟合数字完全相同。JPG 原片通过 SHA-256 前后比对未改变；验证输出和原片不加入仓库。macOS 离屏验证不替代 Windows 64 位及打包 GUI 冒烟。

算法 API 参照 [OpenCV 稀疏金字塔 LK 文档](https://docs.opencv.org/4.x/dc/d6b/group__video__track.html)；实验来源为 `origin/experiment/subject-stabilization-debug-20260930` 的 `experiments/subject_stabilization/IMPLEMENTATION_PLAN.md`。
