"""跟踪诊断只用于预览；失败框是预测位置，绝不参与裁切或导出。"""

def tracking_overlays(regions, result, crop=None, *, alignment=None, source_size=None, canvas_box=None):
    if result is None:
        return ()
    left, top, right, bottom = crop or (0, 0, 1, 1)
    overlays = []
    located = any(box is not None for box in result.boxes)
    for index, region in enumerate(regions):
        box = result.boxes[index] if index < len(result.boxes) else None
        matched = box is not None
        if not matched:
            box = result.predicted_boxes[index] if index < len(result.predicted_boxes) else region
        transformed = (alignment.output_polygon(box, source_size, canvas_box) if alignment else
                       ((box[0]-left)/(right-left), (box[1]-top)/(bottom-top),
                        (box[2]-left)/(right-left), (box[3]-top)/(bottom-top)))
        if matched:
            label = f'{index+1} · 手动' if index in result.manual_indices else str(index+1)
        else:
            label = f"{index+1} · {'预计位置' if located else '未定位'}"
        overlays.append((transformed, label, matched))
    return tuple(overlays)


def polygon_bounds(points):
    return (min(x for x,y in points),min(y for x,y in points),
            max(x for x,y in points),max(y for x,y in points)) if points else None


def apply_frame_alignment(state, frame, focus, bird, regions, tracking):
    """A/B and main preview use the same native-pixel transform, including focus centering."""
    alignment = frame.alignment
    state.focus_polygon = alignment.output_polygon(focus,frame.source_size,frame.canvas_box)
    state.focus_box = polygon_bounds(state.focus_polygon)
    state.bird_polygon = alignment.output_polygon(bird,frame.source_size,frame.canvas_box)
    state.bird_box = None
    state.reference_diagnostics = tracking_overlays(regions,tracking,alignment=alignment,
        source_size=frame.source_size,canvas_box=frame.canvas_box)


def apply_alignment_crop(state, sequence, key):
    alignment = sequence.alignments.get(key)
    if alignment is None or key not in sequence.source_sizes:
        return False
    state.crop_polygon = alignment.source_crop_polygon(sequence.source_sizes[key],sequence.canvas_box)
    state.crop_effect_box = polygon_bounds(state.crop_polygon)
    state.alignment_crop_box = None
    return True


def subject_debug_points(result, crop=None):
    """源像素到实际预览裁切；视口缩放不参与观测，失配点不能显示为拟合成功。"""
    observation = result.observation if result else None
    if observation is None or not observation.source_size:
        return ()
    w,h = observation.source_size
    l,t,r,b = crop or (0,0,1,1)
    accepted = observation.status == 'tracked'
    return tuple(((p[2]/w-l)/(r-l),(p[3]/h-t)/(b-t),
                  (p[4]/w-l)/(r-l),(p[5]/h-t)/(b-t),bool(p[6] and accepted))
                 for p in observation.points)
