"""选区纹理分类、孔径约束与联合求解回归。"""
import math
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from birdstamp.image_dejitter.aperture import classify_texture, texture_summary, aperture_problems, classify_regions
from birdstamp.image_dejitter.region_measurement import Measurement, make_template, measure
from birdstamp.image_dejitter.subject_local_tracker import SubjectLocalTracker
from birdstamp.image_dejitter.translation_solver import solve_translation


def wires(size=(640,420), angles=(3.,), spacing=None, offset=(0,0), blob=None):
    """浅灰天空上的长电线（贯穿全图，便于沿线滑动），可选纹理斑块（鸟）。"""
    image = Image.new('L', size, 170)
    draw = ImageDraw.Draw(image)
    w, h = size
    for k, angle in enumerate(angles):
        slope = math.tan(math.radians(angle))
        ys = [h*.45+k*37] if spacing is None else [h*.3+i*spacing for i in range(6)]
        for y0 in ys:
            y0 += offset[1]
            draw.line([(-w, y0+offset[0]*0-(w+offset[0])*slope), (2*w, y0+(w-offset[0])*slope)], fill=40, width=3)
    if blob is not None:
        rng = np.random.default_rng(7)
        patch = Image.fromarray(rng.integers(0,255,(60,70),dtype=np.uint8)).filter(ImageFilter.GaussianBlur(1))
        image.paste(patch, (int(blob[0]+offset[0]), int(blob[1]+offset[1])))
    return image.filter(ImageFilter.GaussianBlur(.6)).convert('RGB')


def shifted(image, dx, dy):
    """整图平移（相机抖动）；边界复制，电线因此在新图中仍贯穿全图。"""
    import cv2
    array = np.asarray(image)
    moved = cv2.warpAffine(array, np.float32([[1,0,dx],[0,1,dy]]), image.size, borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(moved)


def test_classify_wire_texture_and_flat():
    tilted = np.asarray(wires(angles=(3.,)).convert('L'))[150:250, 200:400]
    texture = classify_texture(tilted)
    assert texture.kind == '1d'
    assert math.degrees(math.atan2(-texture.normal[0], texture.normal[1])) == pytest.approx(3, abs=1.5)
    rng = np.random.default_rng(1)
    noise = np.asarray(Image.fromarray(rng.integers(0,255,(120,120),dtype=np.uint8)).filter(ImageFilter.GaussianBlur(1)))
    assert classify_texture(noise).kind == '2d'
    assert classify_texture(np.full((80,80),120,np.uint8)).kind == 'flat'


def test_parallel_wires_only_are_refused_at_creation_with_direction():
    image = wires(angles=(2.,))
    regions = ((.1,.35,.3,.55),(.6,.35,.8,.55))
    textures = classify_regions(image, regions)
    assert [t.kind for t in textures] == ['1d','1d']
    assert '竖直' in texture_summary(textures)
    assert aperture_problems(textures)
    with pytest.raises(ValueError, match='单向边缘.*竖直'):
        SubjectLocalTracker(image, regions)
    with pytest.raises(ValueError, match='平坦'):
        SubjectLocalTracker(Image.new('RGB',(400,300),'white'), ((.1,.1,.4,.4),))


def test_crossing_wires_solve_full_translation():
    def scene(dx=0, dy=0):
        image = Image.new('L', (640,480), 170)
        draw = ImageDraw.Draw(image)
        draw.line([(-200, 140+dy+(-200-dx)*0), (900, 140+dy)], fill=40, width=3)          # 水平线：约束竖直
        slope = math.tan(math.radians(50))
        x0 = 420+dx
        draw.line([(x0-300/slope, 480+300+dy), (x0+700/slope, -700+dy)], fill=40, width=3)  # 约 50° 斜线
        return image.filter(ImageFilter.GaussianBlur(.6)).convert('RGB')
    reference, moving = scene(), scene(6, -4)
    tracker = SubjectLocalTracker(reference, ((.05,.2,.35,.4),(.55,.45,.85,.75)))
    assert tracker.geometry.kinds == ('1d','1d')
    result = tracker.track(moving)
    assert result.observation.displacement == pytest.approx((6,-4), abs=.4), result.error


def test_wire_plus_bird_joint_solve_and_constraints():
    reference = wires(angles=(1.,), blob=(290,150))
    moving = shifted(reference, -5, -10)
    regions = ((.44,.33,.58,.5),(.05,.36,.3,.56),(.72,.36,.97,.56))
    tracker = SubjectLocalTracker(reference, regions)
    assert tracker.geometry.kinds == ('2d','1d','1d')
    result = tracker.track(moving)
    obs = result.observation
    assert obs.displacement == pytest.approx((-5,-10), abs=.3), result.error
    by = {label: regions for label, _, regions in obs.constrained_by}
    assert by['水平'] == (0,)
    assert {1,2} <= set(by['竖直'])
    assert '水平←选区 1' in obs.summary()


def test_wire_slides_along_line_but_normal_is_measured():
    reference = wires(angles=(0.,))
    template = make_template(reference, (.3,.38,.6,.55), index=0, kind='1d', normal=(0.,1.), scale=1., margin=40.)
    # 沿线方向的任意位移不可观测；只读法向分量。
    measured = measure(template, shifted(reference, 23, -7))
    assert measured.ok and measured.kind == '1d'
    assert measured.value[0] == pytest.approx(-7, abs=.35)


def test_repeated_parallel_wires_are_ambiguous():
    reference = wires(angles=(0.,), spacing=14)
    template = make_template(reference, (.3,.3,.6,.42), index=0, kind='1d', normal=(0.,1.), scale=1., margin=40.)
    measured = measure(template, shifted(reference, 0, 14))
    assert not measured.ok and '平行边缘重复' in measured.reason


def test_solver_reports_missing_direction_and_conflicts():
    blob = Measurement(0,'2d',True,value=(-5.,-10.),covariance=(.25,0.,0.,.25),scale=1.,method='lk',inliers=20)
    wire = Measurement(1,'1d',True,value=(-10.,),normal=(0.,1.),covariance=(.09,),scale=1.,method='edge_ncc')
    ok = solve_translation([blob, wire])
    assert ok.ok and ok.displacement == pytest.approx((-5,-10), abs=.05)
    only = solve_translation([wire], failed=(0,))
    assert not only.ok and '水平方向缺少约束' in only.reason and '选区 1 本帧失配' in only.reason
    conflict = solve_translation([blob, Measurement(1,'1d',True,value=(-30.,),normal=(0.,1.),covariance=(.09,),scale=1.)])
    assert not conflict.ok and '冲突' in conflict.reason


def test_wire_through_bird_box_does_not_drag_texture_match():
    # 鸟框内贯穿一根长电线：沿线滑动的角点被剔除，二维位移仍由鸟体纹理决定。
    reference = wires(angles=(0.,), blob=(300,160))
    moving = shifted(reference, 7, -3)
    tracker = SubjectLocalTracker(reference, ((.45,.36,.6,.55),))
    assert tracker.geometry.kinds == ('2d',)
    result = tracker.track(moving)
    assert result.observation.displacement == pytest.approx((7,-3), abs=.3), result.error
