# 局部主体稳定：三组鸟类连拍实验

这是隔离的离线 CLI 原型，不是已经接入 GUI 的功能。保留现有 YOLO 检测器和 image_dejitter 管线；不修改 main、不修改 app_common、不上传用户原片或模型权重。

## 实现范围

- 手工指定每组参考帧的局部多边形 ROI，Shi–Tomasi 选点，金字塔 LK 前后向跟踪。
- 前后向误差、最大位移、局部共识和跨区域一致性检查。
- 估计 reference -> moving 的主体局部位移，应用其负值平移整张图。
- 不缩放、不旋转、不非刚性变形、不生成式补图。
- 输出半透明 ROI、真实点轨迹、失败点、JSON 报告、NPZ 坐标和公共有效裁剪。
- 特征不足、左右参考区不一致等情况返回 needs_keyframe，不自动改用水纹。

## 与现有 YOLO 的边界

已查看 SuperBirdStamp/birdstamp/gui/editor_core.py：现有检测器候选包括 yolo11n.pt、yolo11s.pt、yolov8n.pt。原型不重复创建检测模型。

现有鸟框可通过 manifest 每组的可选 target_box 字段传入（归一化 xyxy）。它用于限制目标区域，不直接将整鸟框中心或框宽当作稳定锚点。人工局部 ROI 与该框取交集。

本例的局部 ROI 是人工指定，不是 YOLO/SAM/鸟类骨架自动预测。下一帧显示的 ROI 是参考 ROI 的局部位移投影，不是逐帧语义分割。不能把本例描述成全自动系统。

现有 image_dejitter/auto_regions.py 采用分格和角点质量推荐选区；候选纹理强不等于世界静止。未来可在既有策略边界添加显式 subject-local 策略，但这个分支尚未做该集成。

## 运行

在仓库根目录，使用现有仓库 .venv（以下 Windows 命令；macOS 将解释器替换为 .venv/bin/python3）：

```powershell
.\.venv\Scripts\python.exe experiments/subject_stabilization/sample_manifest.py
.\.venv\Scripts\python.exe experiments/subject_stabilization/demo.py --images "D:\Photos\six_frames" --manifest experiments/subject_stabilization/manifest.json --out "D:\Temp\bird_stabilization_debug"
.\.venv\Scripts\python.exe -m unittest discover -s experiments/subject_stabilization -p "test_core.py" -v
```

依赖 numpy、Pillow、OpenCV；不要同时安装多个互相冲突的 OpenCV wheel，也不需要下载神经网络权重。输出目录必须与输入目录不同。

`sample_manifest.py` 只生成标注配置，不包含照片。六张输入文件名及分组为：

| 组 | 参考帧 | 下一帧 |
| --- | --- | --- |
| A | DSC09864.JPG | DSC09865.JPG |
| B | DSC09879.JPG | DSC09880.JPG |
| C | DSC09911.JPG | DSC09912.JPG |

每组独立估计；不把三次拍摄当成六张无间隔连续视频。标注来自 1488×992 的显示坐标，先归一化再映射到分析图；测量在上传版本 2048×1365 上执行。

## 实际结果（像素）

| 组 | 对下一帧应用 dx | dy | 共同位移内点 / 有效轨迹 | 内点残差中位数 | 全部有效锚点残差中位数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | +3.418 | -15.357 | 34 / 43 | 1.224 | 1.601 |
| B | -5.973 | +2.653 | 40 / 42 | 0.601 | 0.620 |
| C | -6.327 | -16.826 | 53 / 62 | 1.471 | 1.633 |

这些残差是参与拟合特征的一致性，不是独立验证的相机位姿真值误差，更不是通用成功率。

C 组头/嘴局部位移约 (+7.391,+4.446)，双腿共识位移约 (+6.327,+16.826)。锁腿后头部仍相对移动约 (+1.064,-12.380) px；此差异包含真实姿态变化和测量误差，不强行消除。锁头与锁腿是不同视觉目标。

## 已验证与未验证

已在独立 Linux 沙箱执行四个 Python 文件的 py_compile、7 项合成单元测试及三组真实图片配准。合成测试涵盖已知平移方向、局部外变化、无纹理拒绝、参考区域冲突、重叠选区拒绝、离群点和参数检查。

未运行仓库完整测试套件、Qt GUI、Windows/macOS 打包或现有 YOLO 推理；不能声称产品集成已完成。GitHub 改动通过连接器写到独立实验分支，没有触碰主线或本地用户工作区。

## 重要限制

1. 双腿在这三组站姿素材中提供了可用参考，但不保证它们世界静止；迈步、浮水、飞行时应改用适合的局部或人工关键帧。
2. ROI 约束的是特征点中心。LK 的邻域窗口仍可包含邻近水面，尤其细腿边缘；前后向检查与鲁棒共识不能完全消除此风险。
3. 两帧不能唯一分离相机抖动与主体真实运动。输出是主体局部稳定，不是物理相机运动重建。
4. 未实现跨拍摄组的自动桥接、尺度稳定、失焦恢复、遮挡重识别或长期防漂移。不同姿态的跨组匹配仍需关键帧/更强跟踪后端。
5. 像素阈值针对当前分析分辨率。原生高像素 RAW/不同尺寸图片需明确分析缩放和坐标映射，不能照抄所有阈值。
6. 绿色 ROI 为采用参考，其他区域仅诊断；代码允许调用方选其它 fit_regions，但任何 ROI 都不自动获得“静止”语义。
