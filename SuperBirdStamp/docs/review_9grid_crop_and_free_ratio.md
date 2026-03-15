# 9 宫格裁剪与「自由」比例 — 实现 Review 简报

## 1. 配置与类型扩展

- **editor_options.json**：`ratio_options` 已增加 `{"label": "自由", "value": "free"}`。
- **editor_options.py**：新增并导出 `RATIO_FREE = "free"`；`_normalize_ratio_options` 支持 `value === "free"`，返回类型为 `list[tuple[str, float | None | str]]`；`RATIO_OPTIONS` 类型已同步。
- **editor_core.py**：`parse_ratio_value` 在遇到 `"free"` 时返回 `RATIO_FREE`；新增 `is_ratio_free(ratio)`；`compute_crop_plan` 支持 `crop_box_override` 与 `RATIO_FREE`（自由且无 override 时返回 `(None, (0,0,0,0))`）；新增 `_crop_plan_from_override(width, height, crop_box)` 用于从归一化框计算 padding 与 padded 空间下的框。

## 2. 裁剪管线

- **editor_core.compute_crop_plan**：当存在有效 `crop_box_override` 时优先使用并据此计算 `outer_pad`；当 `is_ratio_free(ratio)` 且无 override 时直接返回不裁切。
- **editor_crop_calculator._compute_crop_plan_for_image**：从 `settings["crop_box"]` 读取覆盖框；若有效则调用 `_crop_plan_from_override` 并返回；若 `is_ratio_free(ratio)` 则返回不裁切。
- **editor_renderer**：`_build_current_render_settings` 增加 `crop_box: getattr(self, "_crop_box_override", None)`；`_clone_render_settings` / `_normalize_render_settings` 使用 `_parse_ratio_value` 保留 `RATIO_FREE`，并支持读写 `crop_box`；`_ratio_combo_index_for_value` 支持 `RATIO_FREE` 匹配。
- **video_export**：`_compute_crop_plan_for_image` 在入口处检查 `settings["crop_box"]` 与 `_is_ratio_free(ratio)`，逻辑与 GUI 管线一致。

## 3. 9 宫格交互与绘制（EditorPreviewCanvas）

- **绘制**：在 `_paint_overlays` 中当 `_crop_edit_mode` 且存在 `_crop_effect_box` 时调用 `_paint_crop_handles`，绘制裁剪矩形框与 8 个手柄（四角 + 四边中点），手柄为白底黑边圆形。
- **坐标**：`_norm_to_widget` / `_widget_to_norm` 基于 `_display_rect()` 在归一化 (0–1) 与 widget 坐标间转换；手柄命中半径 `_HANDLE_HIT_RADIUS = 8`。
- **交互**：`mousePressEvent` 在裁剪编辑模式下对手柄做命中检测，命中则记录 `_dragging_handle` 与 `_drag_start_box` 并消费事件；`mouseMoveEvent` 在拖拽中根据 `_box_after_drag` 更新框（自由比例直接改边/角，否则走 `_constrain_box_to_ratio_from_fixed_corner` 保持比例）；`mouseReleaseEvent` 结束拖拽并再次发出 `crop_box_changed`。
- **比例约束**：非自由时使用 `_crop_ratio`（或原图比例）；自由时 `_ratio_free=True`，不约束宽高比。
- **与 pan 的协调**：拖拽手柄时由 canvas 消费事件，不触发基类 pan。

## 4. 主编辑器与模板对话框集成

- **editor.py**：新增「调整裁剪框」复选框、`_crop_box_override`；`preview_label.canvas.crop_box_changed` 连接至 `_on_canvas_crop_box_changed`（写回 override 并触发 `_on_crop_settings_changed`）；应用模板时在 `_apply_template_crop_padding_to_main_output` 中从 payload 同步 `_crop_box_override`。
- **editor_renderer._apply_preview_overlay_options_from_ui**：根据「调整裁剪框」设置 `canvas.set_crop_edit_mode`，并根据 `_selected_ratio()` 与 `_is_ratio_free` 设置 `canvas.set_crop_ratio_constraint`。
- **editor_template_dialog.py**：同样新增「调整裁剪框」、连接 `canvas.crop_box_changed` 到 `_on_tmpl_canvas_crop_box_changed`（写回 `current_payload["crop_box"]` 并 `_refresh_preview`）；`_apply_preview_overlay_options` 中同步 canvas 的裁剪编辑模式与比例约束；预览分支中 `_compute_crop_plan` 增加 `crop_box_override`（来自 `current_payload["crop_box"]`）；`_template_ratio_combo_index_for_value` 支持 `RATIO_FREE`。

## 5. _CropPaddingEditorWidget

- 未移除四向 padding 控件（方案 A 仅移除的设想未完全执行），保留与现有模板/主界面兼容；裁剪范围除由 9 宫格直接编辑外，仍可受既有 padding 影响（无 crop_box 覆盖时）。若需「仅 9 宫格 + 填充色」的纯方案 A，可后续再收口。

## 6. 兼容与测试

- 旧模板/设置无 `crop_box` 时行为不变（仍由 ratio + center_mode + bird + crop_padding_* 计算）。
- 新模板保存后带 `crop_box`；读取时用于 `compute_crop_plan` 的 override 并显示 9 宫格。
- 比例切换：选「自由」时 9 宫格可任意比例；选固定比例或原比例时拖动手柄保持对应宽高比。
- 已对修改过的 Python 文件执行 `py -3 -m py_compile`，通过。

## 7. 建议的后续优化

- **方案 B**：如需精确数值，可做 9 宫格与四向 L/T/R/B 像素 spinbox 的双向同步。
- **手柄与无障碍**：可调大手柄半径或提供键盘微调；高 DPI 下手柄尺寸可随缩放调整。
- **模板对话框**：`_on_tmpl_canvas_crop_box_changed` 内每次拖动都触发 `_refresh_preview()`，拖拽频繁时可能卡顿，可增加防抖（例如 200–300ms）再刷新预览。

## 8. 今日新增进展（裁切中心 & 平移）

- **裁切框平移标记 & 行为分支（EditorPreviewCanvas + editor.py）**  
  - 在 `EditorPreviewCanvas` 中增加 `_has_pan` 与 `has_pan()`：仅当拖动 9 宫格中间区域（整体平移裁切框）时置为 `True`，单纯拖拽边/角缩放不会置 `True`。  
  - 主编辑器 `_on_canvas_crop_box_changed` 依据 `has_pan()` 分两种情况：
    - **未平移（仅缩放）**：调用 `_update_crop_padding_from_box(...)`，将当前裁切框在整张图中的位置转换为四向 `crop_padding_*`（像素），并通过 `_crop_padding_widget.set_values(...)` 同步到 UI 与重载 settings，实现“缩放后 padding 自动对齐裁切框”的效果。
    - **已平移**：调用 `_set_custom_center_from_box(...)`，从裁切框中心推导自定义中心 `(cx, cy)`，写入 `_custom_center` 并将「裁切中心」下拉改为 `CENTER_MODE_CUSTOM`。

- **CENTER_MODE_CUSTOM 与自定义中心坐标**  
  - `editor_core.py`：已定义 `CENTER_MODE_CUSTOM` 并包含在 `CENTER_MODE_OPTIONS`，`normalize_center_mode()` 支持 `"custom"`。  
  - 主编辑器 settings（`editor_renderer._build_current_render_settings`）在 `center_mode == custom 且有 _custom_center` 时，会将：
    - `custom_center_x` / `custom_center_y` 一并写入当前照片的重载 settings；  
    - `_photo_override_settings_from_snapshot`、`_clone_render_settings`、`_normalize_render_settings` 也保留与解析这两个字段。  
  - **apply_all_btn**：`_apply_current_settings_to_all_photos()` 使用上述 snapshot，将 `center_mode="custom"` 及 `custom_center_x/y` 一起复制到全部照片的模板重载配置中。

- **计算管线对 CENTER_MODE_CUSTOM 的支持**  
  - GUI 主流程（`editor_crop_calculator.py`）：
    - `_resolve_crop_anchor_and_keep_box(..., settings)` 新增 `settings` 参数；当 `center_mode == _CENTER_MODE_CUSTOM` 时，从 `settings["custom_center_x/y"]` 读取锚点 `(cx, cy)`，返回 `(cx, cy), None`，其他模式仍按鸟体/焦点/图像中心逻辑。  
    - `_compute_crop_plan_for_image` 调用 `_resolve_crop_anchor_and_keep_box(..., settings=settings)`，确保自定义中心生效。  
  - 视频导出（`video_export.py`）：
    - `_clone_render_settings` 会把 `custom_center_x/y` 浮点化保留在导出 settings 中。  
    - `_compute_crop_plan_for_image` 从 `settings` 中解析出 `custom_center_x/y` 组合成 `custom_center`，传入 `_resolve_crop_anchor_and_keep_box(..., custom_center=custom_center)`；当 `center_mode == custom` 且 `custom_center` 有效时，以该点为 anchor 做视频帧裁切。

- **比例约束的修正（16:9 等像素比例）**  
  - 在 9 宫格拖拽中（`editor_preview_canvas._box_after_drag`），比例约束改为使用 **归一化比例 = 像素比例 ÷ 图像宽高比**：  
    - 图像宽高比 `image_aspect = W/H`，目标像素比例 `R`（例如 16:9）；  
    - 归一化坐标下需满足 `(r-l)/(b-t) = R / image_aspect = R * H / W`，代码中对应 `ratio_norm = target_pixel_ratio / image_aspect`。  
  - 在整体收缩/放大已有框到新比例（`editor_core.constrain_box_to_ratio`）时，同样按  
    - `pixel_ratio = R`，`target_ratio = pixel_ratio * height / width`  
    在归一化空间约束宽高；从而保证像素级裁切框宽高比严格等于当前选中的比例（如 16:9、4:3 等）。
