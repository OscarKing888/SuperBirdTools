"""Convex footprints and the largest complete integer-pixel common rectangle."""
from math import ceil, floor
import numpy as np


def intersect_convex(subject, clip):
    polygon = list(subject)
    for a,b in zip(clip,(*clip[1:],clip[0])):
        if not polygon:
            break
        def side(p):
            return (b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])
        output = []
        previous = polygon[-1]
        old = side(previous)
        for current in polygon:
            new = side(current)
            if (new >= -1e-8) != (old >= -1e-8):
                ratio = old/(old-new)
                output.append((previous[0]+ratio*(current[0]-previous[0]),
                               previous[1]+ratio*(current[1]-previous[1])))
            if new >= -1e-8:
                output.append(current)
            previous,old = current,new
        polygon = output
    return tuple(polygon)


def outward_bounds(polygons):
    points = [p for polygon in polygons for p in polygon]
    return (floor(min(x for x,y in points)+1e-8),floor(min(y for x,y in points)+1e-8),
            ceil(max(x for x,y in points)-1e-8),ceil(max(y for x,y in points)-1e-8))


def has_complete_pixel(polygon, *, cancelled=lambda:False):
    """Fast feasibility check for attributing an empty canvas to its first frame."""
    if len(polygon) < 3:
        return False
    top = ceil(min(y for x,y in polygon)-1e-8)
    bottom = floor(max(y for x,y in polygon)+1e-8)
    edges = tuple(zip(polygon,(*polygon[1:],polygon[0])))
    for y in range(top,bottom):
        if cancelled():
            raise InterruptedError('公共画幅计算已取消')
        interval = _row_interval(edges,y)
        if interval is not None and interval[0] < interval[1]:
            return True
    return False


def _row_interval(edges, y):
    """Integer x boundaries whose complete unit squares lie in the convex polygon."""
    lo,hi = float('-inf'),float('inf')
    for (ax,ay),(bx,by) in edges:
        dx,dy = bx-ax,by-ay
        for yy in (y,y+1):
            if abs(dy) < 1e-10:
                if dx*(yy-ay) < -1e-8:
                    return None
            elif dy > 0:
                hi = min(hi,ax+dx*(yy-ay)/dy)
            else:
                lo = max(lo,ax+dx*(yy-ay)/dy)
    if lo == float('-inf') or hi == float('inf'):
        return None
    return ceil(lo-1e-8),floor(hi+1e-8)


def largest_pixel_rectangle(polygon, *, cancelled=lambda:False):
    """Scan complete pixel squares; histogram runs use O(width) memory, no image mask.

    Every square's four corners must lie inside the convex footprint. Equal areas
    prefer the topmost, then leftmost box (remaining ties use tuple order).
    """
    if len(polygon) < 3:
        return None
    left,top = ceil(min(x for x,y in polygon)-1e-8),ceil(min(y for x,y in polygon)-1e-8)
    right,bottom = floor(max(x for x,y in polygon)+1e-8),floor(max(y for x,y in polygon)+1e-8)
    if right <= left or bottom <= top:
        return None
    width = right-left
    heights = np.zeros(width,dtype=np.int32)
    edges = tuple(zip(polygon,(*polygon[1:],polygon[0])))
    best,area = None,0
    for y in range(top,bottom):
        if cancelled():
            raise InterruptedError('公共画幅计算已取消')
        interval = _row_interval(edges,y)
        if interval is None:
            heights.fill(0)
            continue
        start = max(0,min(width,interval[0]-left))
        stop = max(0,min(width,interval[1]-left))
        if stop <= start:
            heights.fill(0)
            continue
        heights[:start] = 0
        heights[stop:] = 0
        heights[start:stop] += 1
        # Process height changes rather than every column of a wide flat run.
        changes = np.flatnonzero(np.diff(heights))+1
        boundaries = np.concatenate(([0],changes,[width]))
        stack = []
        for x in boundaries:
            x = int(x)
            height = int(heights[x]) if x < width else 0
            begin = x
            while stack and stack[-1][1] > height:
                begin,h = stack.pop()
                candidate = (left+begin,y+1-h,left+x,y+1)
                value = (x-begin)*h
                if value > area or (value == area and value and
                        (candidate[1],candidate[0],candidate[3],candidate[2]) <
                        (best[1],best[0],best[3],best[2])):
                    best,area = candidate,value
            if height and (not stack or stack[-1][1] < height):
                stack.append((begin,height))
    return best
