"""Automatic reference suggestions preserve manual regions and reject flat patches."""

import numpy as np
import pytest
from PIL import Image
from itertools import combinations

from birdstamp.image_dejitter.auto_regions import suggest_reference_regions, _grid_cells
from birdstamp.image_dejitter.reference_region_tracker import ReferenceRegionTracker
from birdstamp.image_dejitter.rigid_alignment import estimate_alignment


def test_textured_regions_are_distributed_and_do_not_overlap_existing():
    pixels = np.full((400, 600), 128, dtype=np.uint8)
    rng = np.random.default_rng(207)
    for y, x in ((20, 20), (20, 430), (260, 20), (260, 430)):
        pixels[y:y + 120, x:x + 150] = rng.integers(25, 225, (120, 150), dtype=np.uint8)
    existing = ((.03, .03, .28, .38),)
    with Image.fromarray(pixels) as image:
        boxes = suggest_reference_regions(image, existing, target_count=4)
    assert 2 <= len(boxes) <= 3
    for left, top, right, bottom in boxes:
        assert 0 <= left < right <= 1 and 0 <= top < bottom <= 1
        assert right <= existing[0][0] or left >= existing[0][2] or bottom <= existing[0][1] or top >= existing[0][3]
    assert max(box[0] for box in boxes) - min(box[0] for box in boxes) > .35
    assert max(box[1] for box in boxes) - min(box[1] for box in boxes) > .3


def test_flat_image_and_fully_occupied_image_have_no_suggestions():
    with Image.new('RGB', (600, 400), '#808080') as flat:
        assert suggest_reference_regions(flat) == ()
    rng = np.random.default_rng(11)
    with Image.fromarray(rng.integers(0, 255, (400, 600), dtype=np.uint8)) as textured:
        assert suggest_reference_regions(textured, ((0, 0, 1, 1),)) == ()


def _noise(size=(600, 400)):
    return Image.fromarray(np.random.default_rng(421).integers(20, 230, size[::-1], dtype=np.uint8))


def _assert_separated(boxes):
    for l, t, r, b in boxes:
        assert 0 <= l < r <= 1 and 0 <= t < b <= 1
    for a, b in combinations(boxes, 2):
        assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


def _nine_cell(box):
    return int((box[0]+box[2])*1.5), int((box[1]+box[3])*1.5)


@pytest.mark.parametrize('size', [(600,400), (400,600)])
@pytest.mark.parametrize('count', [1,5,8,9,10,16,36])
def test_target_counts_cover_equal_area_cells_in_both_orientations(size, count):
    cells = _grid_cells(count, portrait=size[1] > size[0])
    assert len(cells) == count
    _assert_separated(cells)
    assert all((r-l)*(b-t) == pytest.approx(1/count) for l,t,r,b in cells)
    with _noise(size) as image:
        boxes = suggest_reference_regions(image, target_count=count)
        assert suggest_reference_regions(image, boxes, target_count=count) == ()
    assert len(boxes) == count
    _assert_separated(boxes)
    # 每一格都有纹理时，新框完整在格内，且每格正好一个。
    for l,t,r,b in cells:
        assert sum(l <= q[0] and t <= q[1] and q[2] <= r and q[3] <= b for q in boxes) == 1


def test_default_nine_and_existing_center_occupancy():
    existing = ((.12,.12,.20,.20),)
    with _noise() as image:
        added = suggest_reference_regions(image, existing)
    assert len(added) == 8
    assert {_nine_cell(box) for box in (*existing, *added)} == {(x,y) for x in range(3) for y in range(3)}
    _assert_separated((*existing, *added))


def test_empty_cells_are_filled_elsewhere_without_selecting_flat_sky():
    with _noise() as image:
        image.paste(128, (0,0,image.width,image.height//3))
        boxes = suggest_reference_regions(image)
    assert len(boxes) == 9
    assert all(box[1] >= 1/3 for box in boxes)
    assert len({_nine_cell(box) for box in boxes}) == 6
    _assert_separated(boxes)


def test_existing_overlapping_boxes_count_toward_target_and_are_not_replaced():
    existing = ((.1,.1,.3,.3), (.12,.12,.32,.32))
    with _noise() as image:
        assert suggest_reference_regions(image, existing, target_count=1) == ()
        added = suggest_reference_regions(image, existing, target_count=5)
    assert len(added) == 3
    for old in existing:
        _assert_separated((old, *added))


def test_single_diagonal_edge_is_rejected_while_textured_corners_are_usable():
    y,x = np.indices((400,600))
    edge = np.where(x-y > 100, 210, 30).astype(np.uint8)
    with Image.fromarray(edge) as image:
        assert suggest_reference_regions(image) == ()
        with _noise((160,160)) as textured:
            image.paste(textured, (400,200))
        boxes = suggest_reference_regions(image)
        assert boxes
        assert all(box[0] >= 1/3 for box in boxes)


def test_suggested_regions_track_known_translation_and_rotation():
    # 有限尺度的纹理经旋转仍有可比较的结构；逐像素白噪声不作成功匹配样本。
    with _noise((300,200)) as small, small.resize((900,600), Image.Resampling.BICUBIC) as image:
        boxes = suggest_reference_regions(image)
        assert len(boxes) == 9
        tracker = ReferenceRegionTracker(image, boxes)
        with Image.new('L', image.size, 128) as shifted:
            shifted.paste(image, (12,-8))
            result = tracker.track(shifted)
            assert result.matched_count == 9, result.error
            for source, target in zip(boxes, result.boxes):
                np.testing.assert_allclose(np.subtract(target,source), (12/900,-8/600)*2, atol=.002)
        with image.rotate(1.2, resample=Image.Resampling.BICUBIC, translate=(12,-8)) as rotated:
            result = tracker.track(rotated)
            assert result.matched_count >= 6, result.error
            alignment = estimate_alignment(boxes, result, image.size, image.size, mode='rigid')
            assert alignment.status == 'rigid'
            assert alignment.applied_degrees == pytest.approx(1.2, abs=.1)
