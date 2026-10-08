"""可嵌套的行/列布局：真实边界测量、吸附建议与文档编辑，不依赖 Qt。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import uuid


def normalize_layouts(raw, items):
    from .model import number
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('overlay_layouts 必须为列表')
    leaves = {i['id'] for i in items if i['type'] != 'background'}
    groups = {}
    for node in raw:
        key = str(node.get('id', ''))
        if not key or key in groups or key in {i['id'] for i in items}:
            raise ValueError('布局 ID 缺失或重复')
        direction = node.get('direction', 'row')
        groups[key] = dict(id=key, direction=direction if direction in ('row', 'down', 'up') else 'row',
            children=list(node.get('children', [])), gap=number(node.get('gap'), .006, 0, 1),
            align=node.get('align') if node.get('align') in ('start', 'center', 'end') else 'center',
            x=number(node.get('x'), .5), y=number(node.get('y'), .5),
            anchor_x=number(node.get('anchor_x'), .5, 0, 1),
            anchor_y=number(node.get('anchor_y'), .5, 0, 1))
    parents = {}
    for node in groups.values():
        children = []
        for child in node['children']:
            if child not in leaves and child not in groups:
                continue  # 删除图层后清理悬空引用。
            if child in parents:
                raise ValueError('同一元素不能属于多个布局')
            parents[child] = node['id']
            children.append(child)
        node['children'] = children
    for key in groups:
        seen = set()
        while key in parents:
            if key in seen:
                raise ValueError('布局不能循环嵌套')
            if len(seen) >= 64:
                raise ValueError('布局嵌套不能超过 64 层')
            seen.add(key)
            key = parents[key]
    # 空组合移除；单成员组合保留锚点，避免删除兄弟后剩余内容跳位。
    changed = True
    while changed:
        empty = {key for key, node in groups.items() if not node['children']}
        changed = bool(empty)
        for key in empty:
            del groups[key]
        for node in groups.values():
            node['children'] = [key for key in node['children'] if key not in empty]
    return list(groups.values())


def indexes(doc):
    groups = {node['id']: node for node in doc.get('overlay_layouts', [])}
    parents = {child: node['id'] for node in groups.values() for child in node['children']}
    return groups, parents


def ancestors(doc, key):
    groups, parents = indexes(doc)
    result = []
    while key in parents:
        key = parents[key]
        result.append(groups[key])
    return result


def members(doc, key):
    groups, _ = indexes(doc)
    if key not in groups:
        return [key]
    return [leaf for child in groups[key]['children'] for leaf in members(doc, child)]


def bounds(layers):
    points = [point for layer in layers for point in layer.corners()]
    if not points:
        return (0., 0., 0., 0.)
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def arrange(doc, layers, size):
    """以旋转后的完整效果边界排版，隐藏/空字段不占位置或间距。"""
    groups, parents = indexes(doc)
    measured = {}

    def measure(key):
        if key not in groups:
            layer = layers.get(key)
            if layer is None:
                return None
            l, t, r, b = bounds([layer])
            measured[key] = (r-l, b-t)
        else:
            node = groups[key]
            sizes = [measure(child) for child in node['children']]
            sizes = [s for s in sizes if s is not None]
            if not sizes:
                return None
            axis = 0 if node['direction'] == 'row' else 1
            gap = node['gap'] * min(size)
            result = [0., 0.]
            result[axis] = sum(s[axis] for s in sizes) + gap*(len(sizes)-1)
            result[1-axis] = max(s[1-axis] for s in sizes)
            measured[key] = tuple(result)
        return measured[key]

    def place(key, left, top):
        if key not in groups:
            w, h = measured[key]
            layers[key] = replace(layers[key], center=(left+w/2, top+h/2))
            return
        node = groups[key]
        axis = 0 if node['direction'] == 'row' else 1
        children = [c for c in node['children'] if c in measured]
        if node['direction'] == 'up':
            children.reverse()
        offset = 0.
        for child in children:
            pos = [left, top]
            pos[axis] += offset
            pos[1-axis] += (measured[key][1-axis]-measured[child][1-axis]) * {'start': 0, 'center': .5, 'end': 1}[node['align']]
            place(child, *pos)
            offset += measured[child][axis] + node['gap']*min(size)

    for key, node in groups.items():
        if key in parents:
            continue
        extent = measure(key)
        if extent:
            place(key, node['x']*size[0]-node['anchor_x']*extent[0],
                  node['y']*size[1]-node['anchor_y']*extent[1])
    return layers


def freeze(doc, scene, ids=None):
    """离开布局时保留已显示的位置和旧避让产生的真实字号。"""
    for layer in scene.layers:
        if ids is None or layer.item['id'] in ids:
            target = next(i for i in doc['overlays'] if i['id'] == layer.item['id'])
            target.update(layer.manual_item(scene.size))


def detach(doc, key, scene):
    result = deepcopy(doc)
    freeze(result, scene, [key])
    for node in result.get('overlay_layouts', []):
        node['children'] = [child for child in node['children'] if child != key]
    return result


@dataclass(frozen=True)
class Snap:
    target: str
    direction: str
    before: bool
    rect: tuple[float, float, float, float]


def snap_candidate(doc, layers, moving, threshold, gap):
    """仅邻边和横向对齐都接近才组合，距离采用视口对应的逻辑像素。"""
    groups, parents = indexes(doc)
    candidates = {}
    moving_ids = set(members(doc, moving.item['id']))
    for layer in layers:
        key = layer.item['id']
        if key in moving_ids or layer.item['type'] == 'background' or layer.item['locked']:
            continue
        chain = ancestors(doc, key)
        root_ids = members(doc, chain[-1]['id']) if chain else [key]
        if any(i['locked'] for i in doc['overlays'] if i['id'] in root_ids):
            continue
        candidates[key] = [layer]
    for key in groups:
        ids = members(doc, key)
        if moving_ids.intersection(ids):
            continue
        subset = [v for v in layers if v.item['id'] in ids]
        if subset and not any(v.item['locked'] for v in subset):
            candidates[key] = subset
    a = bounds([moving])
    best = None
    for key, subset in candidates.items():
        b = bounds(subset)
        for axis, direction in ((0, 'row'), (1, 'down')):
            # 列中的单项允许左右组合成一行，join 会在原列位置嵌套新行。
            # 已有行的上下组合仍以整行为目标，避免拆散同一行。
            if key in parents:
                parent = groups[parents[key]]
                if parent['direction'] == 'row' and axis == 1:
                    continue
            cross = 1-axis
            cross_distance = min(abs(a[cross]-b[cross]), abs(a[cross+2]-b[cross+2]),
                                 abs((a[cross]+a[cross+2]-b[cross]-b[cross+2])/2))
            if cross_distance > threshold[cross]:
                continue
            for before, distance in ((True, abs(a[axis+2]+gap-b[axis])),
                                     (False, abs(a[axis]-gap-b[axis+2]))):
                if distance > threshold[axis]:
                    continue
                score = distance/threshold[axis] + cross_distance/threshold[cross]
                # 单项拖到列中某一行旁时，列外框不能抢走同距离的行目标。
                # 沿已有行/列插入和拖动整组仍优先整组，减少嵌套深度。
                if (direction == 'row' and key in groups
                        and groups[key]['direction'] != 'row' and moving.item['id'] not in groups):
                    score += .02
                else:
                    score += 0 if key in groups else .01
                if best is None or score < best[0]:
                    best = (score, Snap(key, direction, before, b))
    return best[1] if best else None


def join(doc, moving_id, snap, scene, gap=.006):
    result = detach(doc, moving_id, scene)
    groups, parents = indexes(result)
    target = snap.target
    # 沿已有行/列插入；跨方向则包一层组合。
    owner = groups.get(parents.get(target))
    matching = lambda node: node and (node['direction'] == 'row') == (snap.direction == 'row')
    if matching(groups.get(target)):
        owner = groups[target]
        position = 0 if snap.before else len(owner['children'])
        if owner['direction'] == 'up':
            position = len(owner['children'])-position
        owner['children'].insert(position, moving_id)
    elif matching(owner):
        position = owner['children'].index(target)
        after = not snap.before
        if owner['direction'] == 'up':
            after = not after
        owner['children'].insert(position+int(after), moving_id)
    else:
        l, t, r, b = snap.rect
        ax = 0 if (l+r)/2 < scene.size[0]/3 else 1 if (l+r)/2 > scene.size[0]*2/3 else .5
        ay = 0 if (t+b)/2 < scene.size[1]/3 else 1 if (t+b)/2 > scene.size[1]*2/3 else .5
        moving_box = bounds([v for v in scene.layers if v.item['id'] in members(doc, moving_id)])
        cross = 1 if snap.direction == 'row' else 0
        target_box = snap.rect
        alignment = min(('center', 'start', 'end'), key=lambda value: abs(
            (moving_box[cross]+moving_box[cross+2]-target_box[cross]-target_box[cross+2])/2)
            if value == 'center' else abs(moving_box[cross+(2 if value == 'end' else 0)]-target_box[cross+(2 if value == 'end' else 0)]))
        node = dict(id='layout-'+uuid.uuid4().hex, direction=snap.direction,
                    children=[moving_id, target] if snap.before else [target, moving_id],
                    gap=gap, align=alignment, x=(l+(r-l)*ax)/scene.size[0],
                    y=(t+(b-t)*ay)/scene.size[1], anchor_x=ax, anchor_y=ay)
        if owner:
            owner['children'][owner['children'].index(target)] = node['id']
        result.setdefault('overlay_layouts', []).append(node)
    freeze(result, scene, [moving_id] + members(doc, target))
    return result


def anchor_group(doc, key, anchor, scene):
    """更换固定边而不移动当前组合；嵌套组合坐标只在独立时使用。"""
    result = deepcopy(doc)
    groups, _ = indexes(result)
    node = groups[key]
    ids = members(result, key)
    visible = [v for v in scene.layers if v.item['id'] in ids]
    if not visible:
        return result
    l, t, r, b = bounds(visible)
    ax, ay = anchor
    node.update(anchor_x=ax, anchor_y=ay, x=(l+(r-l)*ax)/scene.size[0], y=(t+(b-t)*ay)/scene.size[1])
    return result


def dissolve(doc, key, scene):
    result = deepcopy(doc)
    groups, parents = indexes(result)
    freeze(result, scene, members(result, key))
    for child in groups[key]['children']:
        if child in groups:
            node = groups[child]
            result = anchor_group(result, child, (node['anchor_x'], node['anchor_y']), scene)
    groups, parents = indexes(result)
    if key in parents:
        parent = groups[parents[key]]
        pos = parent['children'].index(key)
        parent['children'][pos:pos+1] = groups[key]['children']
    result['overlay_layouts'] = [n for n in result['overlay_layouts'] if n['id'] != key]
    return result
