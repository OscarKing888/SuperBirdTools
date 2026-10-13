"""真实像素、布局边界与失败时数据保留；不联网。"""
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from PIL import Image
from typer.testing import CliRunner

from birdstamp.collage import CollageCancelled, CollageOptions, PRESETS, export_collage, plan_collage, render_collage
from birdstamp.overlays.assets import import_image
from birdstamp.overlays.model import new_item


@pytest.fixture
def photos(tmp_path):
    paths = []
    for name, size, color in [('红鸟', (120, 60), 'red'), ('绿鸟', (60, 120), 'lime')]:
        path = tmp_path / (name + '.png')
        with Image.new('RGB', size, color) as image:
            image.save(path)
        paths.append(path)
    return paths


@pytest.mark.parametrize('preset,size', [('vertical', (120, 275)), ('horizontal', (275, 120)),
                                       ('grid4', (225, 225)), ('grid6', (330, 225)),
                                       ('grid9', (330, 330)), ('grid16', (435, 435))])
def test_layout_and_pixels(photos, preset, size):
    options = CollageOptions(preset, 100, 5, 10, '#112233')
    image, plan = render_collage(photos, options)
    try:
        assert image.size == plan.size == size
        assert image.getpixel((0, 0)) == (17, 34, 51)
        for box, color in zip(plan.boxes, [(255, 0, 0), (0, 255, 0)]):
            left, top, right, bottom = box
            assert image.getpixel(((left + right) // 2, (top + bottom) // 2)) == color
    finally:
        image.close()


def test_grid_contain_cover_and_preview_geometry(photos):
    options = CollageOptions('grid4', 100, 0, 0)
    contained, plan = render_collage(photos, options)
    covered, _ = render_collage(photos, replace(options, fit='cover'))
    preview, small_plan = render_collage(photos, options, preview_edge=100)
    try:
        assert plan == small_plan
        assert preview.size == (100, 100)
        assert contained.getpixel((50, 5)) == (255, 255, 255)
        assert covered.getpixel((50, 5)) == (255, 0, 0)
        assert covered.getpixel((150, 150)) == (255, 255, 255)  # 空格不重复照片。
        assert preview.getpixel((25, 25)) == contained.getpixel((50, 50))
    finally:
        for image in (contained, covered, preview):
            image.close()


def test_orientation_and_transparency(tmp_path):
    path = tmp_path / '旋转.jpg'
    with Image.new('RGB', (120, 60), 'red') as image:
        exif = Image.Exif()
        exif[274] = 6
        image.save(path, exif=exif)
    image, plan = render_collage([path], CollageOptions(cell_size=100, margin=0))
    assert plan.size == (100, 200)
    image.close()
    path = tmp_path / '透明.png'
    with Image.new('RGBA', (50, 50), (0, 0, 0, 0)) as image:
        image.save(path)
    image, _ = render_collage([path], CollageOptions(background='#123456'))
    assert image.getpixel((50, 50)) == (18, 52, 86)
    image.close()


def test_limits_and_invalid_input():
    with pytest.raises(ValueError, match='最多容纳 4'):
        plan_collage([(100, 100)] * 5, CollageOptions('grid4'))
    with pytest.raises(ValueError, match='拼图过大'):
        plan_collage([(100, 100)] * 64, CollageOptions(cell_size=4096))
    with pytest.raises(ValueError):
        plan_collage([], CollageOptions())
    with pytest.raises(ValueError):
        CollageOptions(preset='unknown')


@pytest.mark.parametrize('suffix', ['.png', '.jpg'])
def test_export_overlay_and_source_preservation(photos, tmp_path, suffix):
    stamp = tmp_path / '蓝色素材.png'
    with Image.new('RGB', (20, 20), 'blue') as image:
        image.save(stamp)
    key, asset = import_image(stamp)
    item = new_item('image')
    item.update(asset_id=key, width=.3, x=.5, y=.5)
    doc = dict(overlays=[item], overlay_assets={key: asset})
    before = [p.read_bytes() for p in photos]
    output = tmp_path / ('中文拼图' + suffix)
    export_collage(photos, CollageOptions('grid4', 100, 0, 0), output, overlays=doc)
    with Image.open(output) as image:
        assert image.size == (200, 200)
        assert image.getpixel((100, 100))[2] >= 250
    assert [p.read_bytes() for p in photos] == before
    assert not list(tmp_path.glob('.birdstamp-collage-*'))


def test_cancel_and_write_failure_keep_existing_output(photos, tmp_path, monkeypatch):
    output = tmp_path / '已有输出.png'
    output.write_bytes(b'previous complete output')
    event = Event()
    with pytest.raises(CollageCancelled):
        export_collage(photos, CollageOptions(), output, cancel=event.is_set,
                       progress=lambda *_: event.set())
    assert output.read_bytes() == b'previous complete output'
    import birdstamp.collage as collage
    def fail(*_args):
        raise OSError('模拟写入失败')
    monkeypatch.setattr(collage.os, 'replace', fail)
    with pytest.raises(OSError, match='模拟写入失败'):
        export_collage(photos, CollageOptions(), output)
    assert output.read_bytes() == b'previous complete output'
    assert not list(tmp_path.glob('.birdstamp-collage-*'))
    with pytest.raises(ValueError, match='不能.*覆盖'):
        export_collage(photos, CollageOptions(), photos[0])


def test_cli(photos, tmp_path):
    from birdstamp.cli import app
    output = tmp_path / '命令行.png'
    result = CliRunner().invoke(app, ['collage', *map(str, photos), '--preset', 'horizontal',
                                    '--cell-size', '100', '--gap', '0', '--margin', '0', '-o', str(output)])
    assert result.exit_code == 0, result.output
    with Image.open(output) as image:
        assert image.size == (250, 100)


@pytest.mark.parametrize('preset', PRESETS)
def test_fixed_canvas_and_automatic_cells(photos, preset):
    options = CollageOptions(preset=preset, width=301, height=401, gap=5, margin=11, fit='cover')
    image, plan = render_collage(photos, options)
    try:
        assert plan.size == image.size == (301, 401)
        for box, color in zip(plan.boxes, [(255, 0, 0), (0, 255, 0)]):
            left, top, right, bottom = box
            assert 11 <= left < right <= 290 and 11 <= top < bottom <= 390
            assert image.getpixel((left, top)) == color
            assert image.getpixel((right - 1, bottom - 1)) == color
        assert image.getpixel((300, 400)) == (255, 255, 255)
    finally:
        image.close()


@pytest.mark.parametrize('preset,columns,rows', [('grid4', 2, 2), ('grid6', 3, 2), ('grid9', 3, 3), ('grid16', 4, 4)])
def test_grid_remainder_pixels_fill_canvas(preset, columns, rows):
    plan = plan_collage([(20, 10)] * (columns * rows),
                        CollageOptions(preset=preset, width=301, height=401, gap=5, margin=11))
    assert plan.boxes[0][:2] == (11, 11)
    assert plan.boxes[-1][2:] == (290, 390)
    widths = [r - x for x, y, r, b in plan.boxes]
    heights = [b - y for x, y, r, b in plan.boxes]
    assert max(widths) - min(widths) <= 1
    assert max(heights) - min(heights) <= 1
    for index, box in enumerate(plan.boxes):
        if index % columns:
            assert box[0] - plan.boxes[index - 1][2] == 5
        if index >= columns:
            assert box[1] - plan.boxes[index - columns][3] == 5


def test_default_portrait_and_weighted_strips():
    assert plan_collage([(100, 100)], CollageOptions()).size == (1080, 1440)
    vertical = plan_collage([(200, 100), (100, 200)],
                            CollageOptions(width=240, height=640, gap=20, margin=10))
    assert vertical.boxes == ((10, 10, 230, 130), (10, 150, 230, 630))
    horizontal = plan_collage([(200, 100), (100, 200)],
                              CollageOptions(preset='horizontal', width=640, height=240, gap=20, margin=10))
    assert horizontal.boxes == ((10, 10, 490, 230), (510, 10, 630, 230))
    tiny = plan_collage([(1, 100000)] + [(100000, 1)] * 63,
                        CollageOptions(width=64, height=64, margin=0, gap=0))
    assert all(bottom - top == 1 for _, top, _, bottom in tiny.boxes)


def test_fixed_canvas_limits():
    with pytest.raises(ValueError, match='空间不足'):
        plan_collage([(10, 10)], CollageOptions(preset='grid16', width=64, height=64, gap=20))
    with pytest.raises(ValueError, match='拼图过大'):
        plan_collage([(10, 10)], CollageOptions(width=10000, height=10000))


def test_portrait_preview_and_overlay_export(photos, tmp_path):
    options = CollageOptions(preset='grid4', fit='cover')
    preview, plan = render_collage(photos, options, preview_edge=400)
    assert preview.size == (300, 400)
    assert plan.size == (1080, 1440)
    preview.close()
    key, asset = import_image(photos[1])
    item = new_item('image')
    item.update(asset_id=key, width=.2, x=.75, y=.75)
    output = tmp_path / '竖版.png'
    export_collage(photos, options, output, overlays=dict(overlays=[item], overlay_assets={key: asset}))
    with Image.open(output) as image:
        assert image.size == (1080, 1440)
        assert image.getpixel((810, 1080)) == (0, 255, 0)


def test_cli_canvas_dimensions(photos, tmp_path):
    from birdstamp.cli import app
    output = tmp_path / '成图.png'
    args = ['collage', *map(str, photos), '-o', str(output)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    with Image.open(output) as image:
        assert image.size == (1080, 1440)
    result = CliRunner().invoke(app, args + ['--width', '640', '--height', '360'])
    assert result.exit_code == 0, result.output
    with Image.open(output) as image:
        assert image.size == (640, 360)
    result = CliRunner().invoke(app, args + ['--width', '640', '--cell-size', '100'])
    assert result.exit_code != 0 and '不能与' in result.output


def test_custom_grid_row_order_and_empty_cells(photos):
    options = CollageOptions(preset='custom', columns=3, rows=2, width=301, height=401,
                             gap=5, margin=11, fit='cover', background='#112233')
    image, plan = render_collage(photos * 2, options)
    try:
        assert image.size == plan.size == (301, 401)
        assert plan.boxes == ((11, 11, 101, 198), (106, 11, 195, 198),
                              (200, 11, 290, 198), (11, 203, 101, 390))
        expected = [(255, 0, 0), (0, 255, 0), (255, 0, 0), (0, 255, 0), (17, 34, 51), (17, 34, 51)]
        points = [(56, 104), (150, 104), (245, 104), (56, 296), (150, 296), (245, 296)]
        assert [image.getpixel(point) for point in points] == expected
    finally:
        image.close()


@pytest.mark.parametrize('columns,rows', [(1, 64), (64, 1), (8, 8)])
def test_custom_grid_capacity_boundary(columns, rows):
    options = CollageOptions(preset='custom', columns=columns, rows=rows,
                             width=512, height=512, gap=0, margin=0)
    plan = plan_collage([(20, 10)] * 64, options)
    assert len(plan.boxes) == 64
    assert plan.boxes[0] == (0, 0, 512 // columns, 512 // rows)
    assert plan.boxes[-1][2:] == (512, 512)


def test_custom_grid_invalid_capacity():
    with pytest.raises(ValueError, match='最多容纳 6'):
        plan_collage([(20, 10)] * 7, CollageOptions(preset='custom', columns=3, rows=2))
    with pytest.raises(ValueError, match='最多 64 格'):
        plan_collage([(20, 10)], CollageOptions(preset='custom', columns=9, rows=8))
    for kwargs in ({'rows': 0}, {'columns': 65}, {'rows': 1.5}):
        with pytest.raises(ValueError):
            CollageOptions(preset='custom', **kwargs)


def test_cli_custom_grid(photos, tmp_path):
    from birdstamp.cli import app
    output = tmp_path / '自定义拼图.png'
    args = ['collage', *map(str, photos), '--columns', '1', '--rows', '3',
            '--width', '300', '--height', '400', '--margin', '0', '--gap', '0',
            '--fit', 'cover', '-o', str(output)]
    result = CliRunner().invoke(app, args + ['--preset', 'custom'])
    assert result.exit_code == 0, result.output
    with Image.open(output) as image:
        assert image.size == (300, 400)
        assert image.getpixel((150, 66)) == (255, 0, 0)
        assert image.getpixel((150, 200)) == (0, 255, 0)
        assert image.getpixel((150, 333)) == (255, 255, 255)
    result = CliRunner().invoke(app, args)
    assert result.exit_code != 0 and '--preset custom' in result.output
