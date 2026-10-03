"""来源几何只影响当前视口，原图参考定义和成片坐标保持独立。"""
import pytest

from birdstamp.gui.editor_preview_canvas import EditorPreviewOverlayState
from birdstamp.gui.preview_source_geometry import (
    camera_to_preview_box, camera_to_preview_point, preview_to_camera_box, preview_to_camera_point,
    transform_source_overlays,
)


CROP = (.1, .2, .9, .8)


def test_preview_reference_roundtrip_and_sensor_margin_rejection():
    box = (.2, .3, .7, .8)
    displayed = camera_to_preview_box(box, CROP)
    assert displayed == pytest.approx((.26, .38, .66, .68))
    assert preview_to_camera_box(displayed, CROP) == pytest.approx(box)
    assert preview_to_camera_box((0, 0, .05, .1), CROP) is None
    assert preview_to_camera_box((0, 0, .5, .5), CROP) == pytest.approx((0, 0, .5, .5))
    extended = (-.2, -.3, 1.2, 1.3)
    assert preview_to_camera_box(camera_to_preview_box(extended, CROP), CROP, clip=False) == pytest.approx(extended)
    assert preview_to_camera_point(camera_to_preview_point((-.2, 1.3), CROP), CROP) == pytest.approx((-.2, 1.3))
    assert preview_to_camera_box(None, CROP) is None


def test_source_overlay_mapping_preserves_focus_bird_and_supports_polygons():
    box = (.2, .3, .7, .8)
    polygon = ((.2, .3), (.7, .3), (.7, .8), (.2, .8))
    focus, bird = (.3, .4, .4, .5), (.2, .2, .6, .6)
    state = EditorPreviewOverlayState(
        reference_regions=(box,), reference_diagnostics=((box, '1', True), (polygon, '2', False)),
        subject_points=((.2, .3, .7, .8, True),), crop_effect_box=box,
        alignment_crop_box=box, crop_polygon=polygon, focus_box=focus, bird_box=bird,
    )
    transform_source_overlays(state, CROP)
    expected = camera_to_preview_box(box, CROP)
    assert state.reference_regions[0] == pytest.approx(expected)
    assert state.reference_diagnostics[0] == (expected, '1', True)
    assert state.reference_diagnostics[1] == (tuple(camera_to_preview_point(point, CROP) for point in polygon), '2', False)
    assert state.subject_points[0] == pytest.approx((*expected, True))
    assert state.crop_effect_box == state.alignment_crop_box == expected
    assert state.crop_polygon == state.reference_diagnostics[1][0]
    assert state.focus_box == focus and state.bird_box == bird


@pytest.mark.parametrize('crop', [None, (0, 0, 0, 1), ('bad', 0, 1, 1), (0, 0, float('nan'), 1)])
def test_missing_or_invalid_geometry_preserves_camera_coordinates(crop):
    box = (.2, .3, .7, .8)
    assert camera_to_preview_box(box, crop) == box
    assert camera_to_preview_point((.2, .3), crop) == (.2, .3)
    assert preview_to_camera_box(box, crop) == box
