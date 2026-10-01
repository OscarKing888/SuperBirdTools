# 按算法推荐参考区

去抖动的「1. 选区」提供 **一键推荐选区**。基本方法寻找背景；高级方法可使用本地鸟类部位模型。两者共用后台抽样预检，并保留人工选区。推荐不是全序列成功保证，仍需执行「分析并预览成片」。

## 操作与边界

- **基本方法**：YOLO 排除鸟体，结合原有角点候选与多尺度宽轮廓选区。拒绝近乎单方向的纹理，再用实际参考匹配器检查唯一性和组合运动一致性。不为凑目标数量加入不可靠区域。
- **高级方法**：默认手动选区。勾选「使用实验性部位识别」并安装模型后，可推荐头部、躯干或可见腿部。多鸟时弹出带裁片的候选按钮，确认目标后再点击推荐。也可手动指定部位；没有可信候选时明确拒绝，不自动改成背景稳定。
- 部位识别目前是**实验功能，默认关闭**。真实样本存在高置信度错误关节，背身、遮挡、展翅不能视为已经验证。识别到部位不等于该部位物理静止，也不保证它有足够纹理用于跟踪。
- 默认抽样参考帧、其后两帧、25%/50%/75% 位置及末帧，去重后最多七张。短序列全部检查。候选必须通过所有抽样帧；匹配位置还须与同名部位相符。优先最差帧质量，接近时优先躯干、头部、腿部。
- 重新推荐替换上次自动区，保留人工区；拖动或缩放自动区后，该区成为人工区。高级方法不自动混入已有人工区，基本方法检查人工区与新候选是否共同成立。
- 目标鸟和部位在完整分析时固定。检测只定位局部搜索窗口，不使用框中心或框大小变化作为补偿。不支持跨长时遮挡重识别；身份歧义须手动处理。

## 模型与坐标

使用官方 Animal Kingdom 鸟类 HRNet-W32、256×256 输入、23 个关键点。推理保留 RGB 标准化、1.25 倍正方形、翻转测试和 MSRA 四分之一像素热图修正；与官方权重键严格对应。部位候选还检查翻转一致性和鸟框范围。

权重大小 **114746485 字节（约 109 MiB）**，固定 SHA-256：

`566feff57cbf6ebcc84d111741c85ce49e07e940b450b646638f1e45587cfb15`

下载/离线导入写到用户数据目录 `models/`，先写临时文件、校验、再原子替换。可以取消，不自动安装依赖、不在启动时下载、不上传照片。使用现有 Torch，优先 CUDA/MPS，不支持时回退 CPU。YOLO 仍使用应用现有实例和设备策略；加入跨调用锁，预览检测与推荐不会并发操作同一实例。

鸟类网络推理代码改编自 [Microsoft HRNet](https://github.com/leoxiaobin/deep-high-resolution-net.pytorch/blob/master/lib/models/pose_hrnet.py)，保留 MIT 声明和完整许可（亦嵌入 Python 模块，随打包分发）。[MMPose 配置及官方鸟类权重](https://github.com/open-mmlab/mmpose/blob/main/configs/animal_2d_keypoint/topdown_heatmap/ak/hrnet_animalkingdom.md)、[23 点定义](https://github.com/open-mmlab/mmpose/blob/main/configs/_base_/datasets/ak.py)。本轮未引入 SAM 或 TAPIR。

手工旧选区继续用原有整图分析。自动主体区采用固定关键帧尺度的局部图，长边最多 1024，直接从源图采样以限制临时内存。分别记录参考/当前裁片原点与分析比例，所有点和最终位移恢复至 EXIF 定向后的源像素。裁片定位改变不会直接改变补偿。完整人工修正建立新关键帧，并更新局部参考窗口。

## 模块与持久化

- `image_dejitter/region_recommendation.py`：GUI/CLI 共用推荐结果、确定性抽样、质量和同名部位/多区一致性核验。
- `image_dejitter/bird_candidates.py`、`bird_parts/`：全部鸟候选、有界 3×3 漏检补充、固定权重校验、独立鸟类关键点后端。
- `image_dejitter/local_crop_tracker.py`：局部分析到源坐标的映射、缓存、人工关键帧。仍调用原有 OpenCV LK 和相同可靠性门槛。
- `image_dejitter/bird_observation_cache.py`：检测和关键点的 256 项 / 32 MiB 有界缓存，只保留数值；键包含源文件与模型属性、目标框。
- `gui/region_recommendation_panel.py`：后台推荐/安装、目标选择、取消与线程所有权。完成回调校验路径、文件签名、选区、算法和参数快照；关闭等真实线程结束。
- 全局设置 `dejitter_region_recommendation` 保存版本、目标框、请求部位、实际推荐部位、自动区来源、局部分析模式和模型版本。旧工作区缺失此字段时所有框为人工框。分析签名版本更新，旧派生结果重新计算，不改变原文件。

## CLI

```sh
# 显式下载，或追加 --source /path/to/official.pth 离线导入
PYTHONPATH=.:SuperBirdStamp .venv/bin/python3 -m birdstamp bird-parts-model

# 基本方法；输出不覆盖已有 JSON
PYTHONPATH=.:SuperBirdStamp .venv/bin/python3 -m birdstamp recommend-regions \
  frame1.JPG frame2.JPG --reference frame1.JPG --output roi.json

# 高级实验；多鸟时 --target 是报告中从 1 开始的鸟候选编号
PYTHONPATH=.:SuperBirdStamp .venv/bin/python3 -m birdstamp recommend-regions \
  frame1.JPG frame2.JPG --reference frame1.JPG --output subject-roi.json \
  --method subject_local --experimental-parts --part auto --target 1

PYTHONPATH=.:SuperBirdStamp .venv/bin/python3 -m birdstamp stabilize \
  frame1.JPG frame2.JPG --reference frame1.JPG --regions subject-roi.json \
  --method subject_local --output results
```

`--model-file` 可指定已验证的官方权重用于诊断。推荐失败或需选鸟也写 JSON，并退出码 2；报告包含原因和抽样文件名。Windows 设置对应的 `PYTHONPATH`，并使用仓库 `.venv\Scripts\python.exe`。

## 2026-10-01 验证

- 三宝鸟 41 张 11232×7488 TIFF：自动背景推荐筛出一个左上轮廓区；抽样 7 张通过，完整生产分析 **41/41** 通过，共同画幅 **10693×6549**。冷推荐约 23 秒，单分析 worker 整组约 15 秒；数值仅代表本机这次素材。
- 六张黑脸琵鹭：前两对的实验自动模式推荐躯干，第三对推荐头部；三对均可单独推荐躯干，第一/第三对可单独推荐头部。三对腿部请求均拒绝（参考图可靠点不足，或后帧同名部位证据不足）。没有把拒识当作成功，也未替代已有人工腿部方案。
- 使用此前实验独立人工多边形检查三张参考图的头/躯干/腿候选：9 个部位请求中 8 个输出、1 个拒识，8 个输出的中心均落在对应人工区域内。该检查只证明粗略位置，不等同关节精度或完整轮廓正确；样本太少，不能据此开放正式自动部位识别。
- 另外检查三宝鸟的 7 张不同姿态关键点图，观察到展翅、背身和遮挡中的错误关节，故发布门控仍关闭。模型/推理和同名部位预检都已接通，可显式试用。
- macOS CPU 模型推理与本机默认设备推荐已运行；两帧部位预检热运行约 0.5–1.0 秒，验证进程峰值 RSS 约 1.26 GiB（含 YOLO/Torch/原图）。测试记录不代表所有设备表现。
- 回归覆盖局部原点变化、检测框抖动不产生补偿、实际导出像素、缓存往返、人工区保护、迟到结果、取消/关闭、模型校验和原子安装、旧配置。
- **尚未实测 Windows 64 位和打包应用启动**；源码采用跨平台 PyTorch/Qt/Pillow/OpenCV 路径，没有引入 CUDA 自定义算子或训练框架，但不能用此代替实测。
