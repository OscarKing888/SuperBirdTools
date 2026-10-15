# 连拍计算 FPS 参数与档位维护

目录树右键“计算连拍信息”的最低连拍速度使用 FPS（每秒照片张数）。
可输入 0.2–1000 FPS，支持两位小数；“相机档位”提供常用档位、按速度汇总和 12 个厂商分组。
默认 4 FPS 等价于原来的 250 ms。选档后可继续手动调整，只有“开始计算”才更新控制器的会话参数；取消不更新。

`burst_fps.py` 集中维护档位和 `fps_to_max_gap_ms()`，控制器用浮点数 `1000 / FPS`
传给原有 `plan_bursts(max_gap_ms=...)`，不会把 30/60/120/195 FPS 的毫秒间隔取整。
每组最少张数、按目录编号、RAW+JPEG 同名合并、无亚秒时至少 1 秒容差、XMP 写入规则均保持原样。
这是相邻照片的速度下限，并非整组平均速度；实际拍摄变慢或时间戳取整时，可调低 FPS，允许更大的间隔。
本轮只调整 GUI 参数，原有可复用分组 API 保留，不新增 CLI。

档位是不同机型、快门类型、裁切模式和短时高速模式的汇总，不表示一台相机支持整个品牌列表，
也不承诺穷尽所有历史机型与未来固件。只纳入静态照片速度，不把视频、取景器刷新率或缓存张数当作连拍 FPS。
新增机型档位时更新 `CAMERA_FPS_PRESETS`；“全部档位”自动去重、排序，不另维护副本。

主要官方参考资料（2026-10-15 核对）：

| 厂商 | 覆盖与官方依据 |
| --- | --- |
| 佳能 Canon | EOS 单反与 R 系列的低/中/高速档；[R3 驱动模式](https://cam.start.canon/en/C010/manual/html/UG-04_AF-Drive_0100.html) 包括 3/6/8/12/15/30 与最高 195 FPS 短时连拍 |
| 尼康 Nikon | 单反与 Z 系列可调低速档、电子快门高速档；[Z8 驱动模式](https://onlinemanual.nikonimglib.com/z8/en/release_mode_33.html)、[速度设置](https://onlinemanual.nikonimglib.com/z8/en/csmd_continuous_shooting_speed_226.html) |
| 索尼 Sony | α 与 RX 系列；[α7R II 的 2.5 FPS](https://helpguide.sony.net/ilc/1520/v1/en/contents/TP0001092096.html)、[α9 III 可选速度](https://helpguide.sony.net/ilc/2380/v1/en/contents/201h_drivemode_dial_setting.html)、[RX100 VII 的 30/60/90 FPS 短时连拍](https://www.sony.com/electronics/support/compact-cameras-dsc-rx-series/dsc-rx100m7/specifications) |
| 富士 Fujifilm | X/GFX 系列，包含小数实际速度；[X-T5](https://app.fujifilm-dsc.com/en/manual/x-t5/menu_shooting/shooting_setting/)、[X-H1](https://fujifilm-dsc.com/en-int/manual/x-h1/menu_shooting/shooting_setting/index.html)、[X-H2S](https://fujifilm-dsc.com/en/manual/x-h2s/technical_notes/spec/) |
| OM SYSTEM / 奥林巴斯 | OM/OM-D 的机械、静音、Pro Capture；[OM-1 II 档位表](https://learning.omsystem.com/OM-1MarkII/zz_html_manual/en/sequential_shooting_settings_112.html) 含 12.5/16.7/25/50/100/120 FPS |
| 松下 Panasonic | LUMIX G/S 系列；[G9 II 连拍说明](https://eww.pavc.panasonic.co.jp/dscoi/DC-G9M2/html/DC-G9M2_DVQP3010_eng/0047.html) 含 20/60/75 FPS |
| 宾得 Pentax / 理光 | [K-1](https://www.ricoh-imaging.co.jp/english/products/k-1/spec/) 的 0.7/1/3/4.4/6.5、[K-3](https://www.ricoh-imaging.co.jp/english/products/k-3/feature/03.html) 的 8.3、[K-3 III](https://www.ricoh-imaging.co.jp/english/products/k-3-3/spec/) 的 2.5/7/12 FPS 等 |
| 徕卡 Leica | SL/Q/M 系列；[Q3 技术参数](https://leica-camera.com/en-US/photography/cameras/q/q3-metal-gray/technical-specification)、[SL3-S 技术参数](https://leica-camera.com/sites/default/files/pm-117188-Leica-SL3-S_Technical-Data_en.pdf) |
| 哈苏 Hasselblad | [X1D](https://cdn.hasselblad.com/datasheets/x1d/X1D-50c-Datasheet_EN_v3.pdf)、[X2D](https://cdn.hasselblad.com/f/77891/x/38effba8fe/x2d_100c_datasheet_en.pdf)、[X2D II](https://cdn.hasselblad.com/f/77891/x/15b206e1b9/x2d_ii_100c_datasheet_multi_20260416.pdf) 等中画幅低速档 |
| 适马 Sigma | fp / fp L / sd Quattro 系列；[fp 参数](https://www.sigma-global.com/en/cameras/fp/)、[fp 系列功能表](https://www.sigma-global.com/en/special/fp-series/functions/) |
| 三星 Samsung | NX 系列；[NX1 官方手册入口](https://www.samsung.com/uk/support/model/EV-NX1ZZZBZBTR/)、[NX1 发布资料](https://news.samsung.com/global/samsung-electronics-announced-the-launch-of-the-revolutionary-nx1) |
| 卡西欧 Casio | EXILIM 系列高速/预拍摄档；[EX-F1 手册](https://support.casio.com/pdf/001/EXF1_MF_FD_110808_E.pdf)、[EX-100F 连拍说明](https://support.casio.com/en/support/answer.php?cid=001035003003&num=2&qid=74281) |

界面采用已有 `QDialog`、`QFormLayout`、`QDoubleSpinBox` 和原生菜单，不新增共享控件或折叠容器。
参数行按窄窗口换行，说明随宽度换行，输入行伸缩，内容由单一 `QScrollArea` 滚动，开始/取消固定在滚动区外。
档位较多的厂商菜单进一步按速度分段，避免大字体下单个菜单过长。
离屏回归见 `SuperViewer/tests/test_burst_fps.py` 与 `test_burst_info_controller.py`；
共享算法、中文 XMP 读回和列表分组回归仍使用 `app_common/tests/test_burst_info.py`、`test_file_browser_burst_groups.py`。
