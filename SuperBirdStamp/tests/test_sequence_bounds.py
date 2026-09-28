"""Independent geometry review and simultaneous complete/common range display."""
from itertools import combinations
from math import cos, sin, degrees
import random

from PyQt6.QtGui import QColor

from birdstamp.export_stage.sequence_preview import SequencePreview
from birdstamp.export_stage.sequence_intersection import compute_intersection_box, compute_union_box
from birdstamp.image_dejitter.rigid_alignment import FrameAlignment, rectangle_points
from birdstamp.gui.sequence_bounds_overview import SequenceBoundsOverview
from test_editor_dejitter import _APP


def test_common_rectangle_matches_exhaustive_source_footprint_containment():
    rng = random.Random(812)
    for _ in range(35):
        alignments = {}
        for key in ('a', 'b', 'c'):
            angle = rng.uniform(-.15, .15)
            c, s = cos(angle), sin(angle)
            alignments[key] = FrameAlignment(
                (c, -s, rng.uniform(-2, 2), s, c, rng.uniform(-2, 2)),
                measured_degrees=degrees(angle), applied_degrees=degrees(angle), status='rigid')
        sequence = SequencePreview('', dict.fromkeys(alignments), (), alignments=alignments,
                                   canvas_box=(0,0,12,10), output_size=(12,10),
                                   source_sizes=dict.fromkeys(alignments, (12,10)))
        # 穷举每个整数矩形，直接检查其四角在每张源图的安全画幅内；
        # 不借用被测多边形裁切、扫描行或直方图算法。
        def contained(box):
            for alignment in alignments.values():
                a,b,c,d,e,f = alignment.reference_to_source
                for x,y in rectangle_points(box):
                    sx, sy = a*x+b*y+c, d*x+e*y+f
                    if not (2-1e-8 <= sx <= 10+1e-8 and 2-1e-8 <= sy <= 8+1e-8):
                        return False
            return True
        boxes = ((l,t,r,b) for l,r in combinations(range(13),2) for t,b in combinations(range(11),2))
        expected = min((box for box in boxes if contained(box)), default=None,
                       key=lambda box: (-(box[2]-box[0])*(box[3]-box[1]),box[1],box[0],box[3],box[2]))
        assert compute_intersection_box(sequence) == expected


def test_different_source_sizes_and_negative_translation():
    sequence = SequencePreview('', {'a':None,'b':None}, (),
                               output_size=(200,170), canvas_box=(0,-10,200,160),
                               source_sizes={'a':(200,160),'b':(180,140)},
                               alignments={'a':FrameAlignment(),
                                           'b':FrameAlignment((1,0,20,0,1,-10))})
    assert compute_union_box(sequence) == (0,0,200,170)
    assert compute_intersection_box(sequence) == (20,10,200,140)


def test_overview_shows_both_ranges_even_when_output_has_already_been_cropped():
    overview = SequenceBoundsOverview()
    overview.resize(260,100)
    overview.set_bounds((-50,-20,200,160), (0,0,100,80))
    image = overview.grab().toImage()
    colors = {image.pixelColor(x,y).name() for y in range(image.height()) for x in range(image.width())}
    assert QColor('#F5A623').name() in colors
    assert QColor('#45D6E8').name() in colors
    overview.set_bounds((-50,-20,200,160), None)
    assert not overview.isHidden()
    overview.set_bounds(None, None)
    assert overview.isHidden()
    overview.close()
