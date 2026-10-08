# 全屏图的平台叠加安全框

在「1. 模板裁切」的「平台安全框」中选择 **小红书 / B站 / 抖音**，用于全屏发布图像；普通非全屏图选择默认的 **关闭（非全屏）**。该选项独立于「不裁切 / 原比例 / 自由比例」，不会因为选择「不裁切」自动启用。图片比例本身不能判断发布端是否全屏，需要按发布用途选择。

选项属于当前照片实例，切图分别保存，支持「全部应用」和工作区保存/恢复；「重置为模板值」将安全框恢复为关闭。不会改写模板图层坐标，也不移动、缩放或再次裁切底图。

## 自动适配与整体偏移

- 使用叠加阶段的实际输出宽高；宽 ≥ 高使用横屏预设，宽 < 高使用竖屏预设，正方形采用横屏预设。边距按画幅比例换算，适用于 16:9、9:16、4:3、3:4、1:1 和自定义比例，也随分辨率自动缩放。
- 原排版完成后，计算所有实际输出的文本、徽章、图像和背景的联合边界，包含旋转、描边和阴影。隐藏项及未输出类别不影响安全框布局。
- 在保持相对位置、旋转、层叠顺序的前提下，施加能进入安全框的最小整体平移。已在框内时保持原位置；整体尺寸过大时先等比缩小再平移，不拆散组合。图层锁定只限制手动编辑，不阻止整体安全适配。
- 预览绿色虚线为平台参考框，只在窗口显示，不写入图片/GIF/视频。平台选择和边距配置参与渲染缓存校验，预览、叠加编辑命中和导出使用同一场景几何。拖动叠加后松手提交，会重新应用安全框限制。
- 视频若另外指定不同的输出画幅，仍沿用视频导出的等比缩放与补边；平台安全框依据模板成片画幅计算。需要全屏铺满时，将模板裁切比例与视频目标比例设置一致。

## 可配置的保守预设

这些数值是项目的保守参考预设，**不是平台发布的统一保证值**。具体遮挡会随设备、客户端版本和展示模式变化。配置位于 `config/editor_options.json` 的 `platform_safe_area`，通过 `birdstamp.config.resolve_bundled_path` 读取，修改后重启应用生效。`labels` 控制选项文案，`presets` 控制边距。

每组数组依次为 **左 / 上 / 右 / 下**，以输出宽或高的比例表示：

| 平台 | 竖屏边距 | 横屏边距 |
| --- | --- | --- |
| 小红书 | 6% / 12% / 16% / 22% | 6% / 8% / 10% / 16% |
| B站 | 6% / 10% / 16% / 20% | 5% / 12% / 5% / 14% |
| 抖音 | 6% / 12% / 18% / 24% | 6% / 10% / 12% / 18% |

非法配置回退到对应默认预设；旧工作区或未知平台值按关闭处理，不继承上一张照片的平台选择。

## CLI 与实现

CLI 使用同一绘制核心，例如在仓库根目录运行：

```sh
PYTHONPATH=SuperBirdStamp .venv/bin/python3 -m birdstamp render photo.png --template template.json --out output --format png --platform-safe-area douyin
```

CLI 需将 `SuperBirdStamp` 加入 `PYTHONPATH`（例如 macOS 用 `PYTHONPATH=SuperBirdStamp .venv/bin/python3 -m birdstamp ...`）。Windows 使用 `.venv\Scripts\python.exe` 与相应 `PYTHONPATH`。可选值为 `off`、`xiaohongshu`、`bilibili`、`douyin`。

无 Qt 几何与预设规范化在 [overlays/safe_area.py](../birdstamp/overlays/safe_area.py)；[build_scene](../birdstamp/overlays/render.py) 在原排版完成后统一适配，不新增独立处理阶段。参数由 `ImageProcTemplateCropStage` 描述，实际叠加由 `ImageProcTemplateOverlayStage` 执行。旧 fields/Banner 模板只在开启安全框时通过既有图层适配器渲染，关闭沿用原绘制路径。

回归见 [test_platform_safe_area.py](../tests/test_platform_safe_area.py)：三平台、多比例、旋转/阴影、超大组合、原图不变、预览与真实输出一致、逐图切换/批量应用/工作区、CLI 与参考框不进入导出。
