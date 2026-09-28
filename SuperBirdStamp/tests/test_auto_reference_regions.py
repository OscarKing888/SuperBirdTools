"""Automatic reference suggestions preserve manual regions and reject flat patches."""

import numpy as np
from PIL import Image

from birdstamp.image_dejitter.auto_regions import suggest_reference_regions


def test_textured_regions_are_distributed_and_do_not_overlap_existing():
    pixels = np.full((400, 600), 128, dtype=np.uint8)
    rng = np.random.default_rng(207)
    for y, x in ((20, 20), (20, 430), (260, 20), (260, 430)):
        pixels[y:y + 120, x:x + 150] = rng.integers(25, 225, (120, 150), dtype=np.uint8)
    existing = ((.03, .03, .28, .38),)
    with Image.fromarray(pixels) as image:
        boxes = suggest_reference_regions(image, existing)
    assert 2 <= len(boxes) <= 4
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
