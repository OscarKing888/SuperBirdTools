"""参考区拖动几何：源图归一化坐标，比例与中心约束统一在边界内求解。"""


def move_region(box, delta):
    l, t, r, b = box
    dx = max(-l, min(float(delta[0]), 1 - r))
    dy = max(-t, min(float(delta[1]), 1 - b))
    return l + dx, t + dy, r + dx, b + dy


def resize_region(box, handle, point, *, keep_ratio=False, symmetric=False):
    l, t, r, b = box
    width, height = r - l, b - t
    sx = -1 if 'w' in handle else 1 if 'e' in handle else 0
    sy = -1 if 'n' in handle else 1 if 's' in handle else 0
    cx, cy = (l + r) / 2, (t + b) / 2
    ax = cx if symmetric or not sx else r if sx < 0 else l
    ay = cy if symmetric or not sy else b if sy < 0 else t
    centered_x, centered_y = symmetric or not sx, symmetric or not sy
    max_w = 2 * min(ax, 1 - ax) if centered_x else ax if sx < 0 else 1 - ax
    max_h = 2 * min(ay, 1 - ay) if centered_y else ay if sy < 0 else 1 - ay
    min_w, min_h = min(.01, width), min(.01, height)
    wanted_w = sx * (point[0] - ax) * (2 if symmetric else 1) if sx else width
    wanted_h = sy * (point[1] - ay) * (2 if symmetric else 1) if sy else height
    if keep_ratio:
        # 归一化宽高同比例变化，等价于保持当前原图像素宽高比；不继承模板比例。
        scale = (max(wanted_w / width, wanted_h / height) if sx and sy
                 else wanted_w / width if sx else wanted_h / height)
        scale = max(max(min_w / width, min_h / height), min(scale, max_w / width, max_h / height))
        new_w, new_h = width * scale, height * scale
    else:
        new_w = max(min_w, min(wanted_w, max_w)) if sx else width
        new_h = max(min_h, min(wanted_h, max_h)) if sy else height
    left = ax - new_w / 2 if centered_x else ax - new_w if sx < 0 else ax
    top = ay - new_h / 2 if centered_y else ay - new_h if sy < 0 else ay
    # 约束已在上面联立求解；这里只消除边界处浮点舍入产生的极小越界。
    return tuple(max(0.0, min(1.0, value)) for value in (left, top, left + new_w, top + new_h))
