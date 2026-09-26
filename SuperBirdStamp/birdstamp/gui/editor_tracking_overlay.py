"""跟踪诊断只用于预览；失败框是预测位置，绝不参与裁切或导出。"""

def tracking_overlays(regions, result, crop=None):
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
        transformed = ((box[0]-left)/(right-left), (box[1]-top)/(bottom-top),
                       (box[2]-left)/(right-left), (box[3]-top)/(bottom-top))
        label = str(index+1) if matched else f"{index+1} · {'预计位置' if located else '未定位'}"
        overlays.append((transformed, label, matched))
    return tuple(overlays)
