"""两段式稳定：背景逐帧去抖，画框只跟随目标鸟的平滑趋势。"""
import threading
import numpy as np
import pytest
from PIL import Image, ImageFilter

from birdstamp.export_stage.render_job_seed import RenderJobSeed
from birdstamp.export_stage.sequence_preview import prepare_sequence_preview, sequence_input_key
from birdstamp.export_stage.sequence_photo_error import SequencePhotoError
from birdstamp.gui.editor_utils import path_key
from birdstamp.image_dejitter import bird_follow
from birdstamp.image_dejitter.bird_candidates import BirdCandidate
from birdstamp.image_dejitter.bird_follow import robust_trend
from birdstamp.image_dejitter.recognition import SubjectSettings, METHOD_KEY, FOLLOW_KEY, FOLLOW_WINDOW_KEY

SIZE = (480, 360)
BIRD = (40, 30)
REGION = (.04, .05, .34, .4)          # 背景参考区（左上角），远离鸟


def scene(count=9, *, seed=3):
    rng = np.random.default_rng(seed)
    world = Image.fromarray(rng.integers(0,255,(600,800,3),dtype=np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
    camera = np.array([(60+2*k+rng.integers(-6,7), 60-k+rng.integers(-6,7)) for k in range(count)])  # 漂移＋抖动
    bird = np.array([(330+5*k, 250+2*k) for k in range(count)], dtype=float)                       # 鸟匀速走动
    frames = []
    for (cx, cy), (bx, by) in zip(camera, bird):
        frame = world.crop((cx, cy, cx+SIZE[0], cy+SIZE[1]))
        frame.paste((230, 20, 20), (int(bx-cx), int(by-cy), int(bx-cx)+BIRD[0], int(by-cy)+BIRD[1]))
        frames.append(frame)
    return frames, camera, bird


def red_detector(missing=()):
    """以红色块为鸟；missing 中的帧号（图像左上像素蓝通道编码）返回无检测。"""
    def detect(image, **kw):
        array = np.asarray(image.convert('RGB')).astype(int)
        if int(array[0,0,2]) in missing:
            return ()
        mask = (array[...,0] > 200) & (array[...,1] < 60) & (array[...,2] < 60)
        ys, xs = np.nonzero(mask)
        if not len(xs):
            return ()
        w, h = image.size
        return (BirdCandidate((xs.min()/w, ys.min()/h, (xs.max()+1)/w, (ys.max()+1)/h), .9),)
    return detect


def seeds(tmp_path, frames, *, follow=True, window=5, target=None):
    paths = []
    for i, frame in enumerate(frames):
        frame = frame.copy(); frame.putpixel((0,0), (0,0,i))   # 帧号编码，供检测桩识别
        path = tmp_path/f'{i:02d}.png'; frame.save(path); paths.append(path)
    settings = {METHOD_KEY:'reference_region', FOLLOW_KEY:follow, FOLLOW_WINDOW_KEY:window,
                'dejitter_reference_source':str(paths[0]), 'dejitter_reference_regions':(REGION,),
                'dejitter_reference_strength':100, 'dejitter_alignment_mode':'translation',
                'dejitter_region_recommendation':{'version':1,'target':target} if target else {}}
    return [RenderJobSeed(p, dict(settings), {}, True) for p in paths]


def output_positions(sequence, frames, camera, bird, tmp_path):
    keys = [path_key(tmp_path/f'{i:02d}.png') for i in range(len(frames))]
    background, subject = [], []
    for key, (cx, cy), (bx, by) in zip(keys, camera, bird):
        alignment = sequence.alignments[key]
        l, t = sequence.canvas_box[:2]
        # 世界点在输出中的位置：世界→源（减相机）→参考坐标（对齐）→画布。
        to_output = lambda x, y: np.array((x-cx+alignment.source_to_reference[2]-l, y-cy+alignment.source_to_reference[5]-t))
        background.append(to_output(150, 150))
        subject.append(to_output(bx+BIRD[0]/2, by+BIRD[1]/2))
    return np.array(background), np.array(subject)


def test_background_shake_removed_and_framing_follows_bird_trend(tmp_path, monkeypatch):
    frames, camera, bird = scene()
    monkeypatch.setattr(bird_follow, 'detector', red_detector())
    sequence = prepare_sequence_preview(seeds(tmp_path, frames), cancel_event=threading.Event())
    background, subject = output_positions(sequence, frames, camera, bird, tmp_path)
    # 鸟匀速运动的趋势被画框完全跟随：输出中鸟几乎不动。
    assert np.ptp(subject, axis=0).max() <= 2
    # 背景只随“跟随路径”平滑移动：去掉线性趋势后无逐帧抖动。
    k = np.arange(len(frames))
    for axis in (0, 1):
        residual = background[:,axis]-np.polyval(np.polyfit(k, background[:,axis], 1), k)
        assert np.abs(residual).max() <= 1.5
    statuses = {plan.status for plan in sequence.subject_plans.values()}
    assert statuses == {'bird_follow'}


def test_follow_off_keeps_pure_background_lock(tmp_path, monkeypatch):
    frames, camera, bird = scene()
    monkeypatch.setattr(bird_follow, 'detector', red_detector())
    sequence = prepare_sequence_preview(seeds(tmp_path, frames, follow=False), cancel_event=threading.Event())
    assert not sequence.subject_plans
    boxes = [sequence.pixel_boxes[path_key(tmp_path/f'{i:02d}.png')] for i in range(len(frames))]
    # 纯背景锁定：输出中背景静止，鸟随真实走动漂移。
    for box, (cx, cy) in zip(boxes, camera):
        assert (box[0]+cx, box[1]+cy) == pytest.approx((boxes[0][0]+camera[0][0], boxes[0][1]+camera[0][1]), abs=1)


def test_missing_detection_uses_neighbour_trend_and_long_gap_fails(tmp_path, monkeypatch):
    frames, camera, bird = scene()
    monkeypatch.setattr(bird_follow, 'detector', red_detector(missing={4}))
    sequence = prepare_sequence_preview(seeds(tmp_path, frames), cancel_event=threading.Event())
    plans = sequence.subject_plans
    assert plans[path_key(tmp_path/'04.png')].status == 'bird_follow_trend'
    assert plans[path_key(tmp_path/'04.png')].observed == ()
    _, subject = output_positions(sequence, frames, camera, bird, tmp_path)
    assert np.ptp(subject, axis=0).max() <= 3
    # 身份中断后轨迹不跨越失败帧：后续全部无位置，窗口内证据不足须明确失败。
    gap = tmp_path/'gap'; gap.mkdir()
    monkeypatch.setattr(bird_follow, 'detector', red_detector(missing={2,3,4,5,6,7,8}))
    with pytest.raises(SequencePhotoError, match='连续丢失'):
        prepare_sequence_preview(seeds(gap, frames), cancel_event=threading.Event())


def test_multiple_birds_require_target_choice(tmp_path, monkeypatch):
    frames, _, _ = scene(4)
    two = lambda image, **kw: (BirdCandidate((.1,.1,.2,.2),.9), BirdCandidate((.6,.6,.7,.7),.8))
    monkeypatch.setattr(bird_follow, 'detector', two)
    with pytest.raises(SequencePhotoError, match='选择目标鸟'):
        prepare_sequence_preview(seeds(tmp_path, frames), cancel_event=threading.Event())


def test_settings_roundtrip_and_cache_key():
    assert SubjectSettings.from_settings({FOLLOW_KEY:True}).follow_bird
    assert not SubjectSettings.from_settings({FOLLOW_KEY:'true'}).follow_bird            # 只接受显式布尔
    assert not SubjectSettings.from_settings({METHOD_KEY:'subject_local',FOLLOW_KEY:True}).follow_bird
    assert SubjectSettings.from_settings({FOLLOW_WINDOW_KEY:8}).follow_window == 9
    assert SubjectSettings.from_settings(SubjectSettings(follow_bird=True,follow_window=11).as_settings()) == \
        SubjectSettings(follow_bird=True,follow_window=11)


def test_cache_key_changes_with_follow(tmp_path):
    frames, _, _ = scene(3)
    a, b = seeds(tmp_path, frames, follow=False), seeds(tmp_path, frames, follow=True)
    assert sequence_input_key(a) != sequence_input_key(b)


def test_robust_trend_ignores_single_box_jump():
    times = np.arange(9.)
    values = np.column_stack((times*3, times*0))
    values[4] = (12+80, 0)                       # 展翅导致检测框中心突跳
    trend, support = robust_trend(times, values, 7)
    assert trend[:,0] == pytest.approx(times*3, abs=1.5)
    values[3:6] = np.nan
    trend, support = robust_trend(times, values, 3)
    assert np.isnan(trend[4]).all() and support[4] == 0


def test_cli_follow_bird_writes_plans(tmp_path, monkeypatch):
    import json
    from birdstamp.subject_stabilization_cli import stabilize_files
    frames, _, _ = scene(5)
    monkeypatch.setattr(bird_follow, 'detector', red_detector())
    paths = [seed.path for seed in seeds(tmp_path, frames)]
    roi = tmp_path/'roi.json'; roi.write_text(json.dumps({'regions':[list(REGION)]}), encoding='utf-8')
    out = tmp_path/'out'; out.mkdir()
    folder = stabilize_files(paths, paths[0], roi, out, method='reference_region', follow_bird=True, follow_window=5)
    report = json.loads((folder/'report.json').read_text(encoding='utf-8'))
    assert report['status'] == 'complete'
    assert {f['plan']['status'] for f in report['frames']} == {'bird_follow'}
    with pytest.raises(ValueError, match='基本参考区'):
        stabilize_files(paths, paths[0], roi, out, method='subject_local', follow_bird=True)
