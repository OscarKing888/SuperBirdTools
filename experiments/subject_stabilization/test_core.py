"""Synthetic ground-truth checks; no user photographs or network needed."""
import unittest
import numpy as np
import cv2
from core import Options, analyze_pair, consensus_translation, warp_translation


class TestSubjectTranslation(unittest.TestCase):
    def fixture(self):
        rng = np.random.default_rng(761)
        im = cv2.GaussianBlur(rng.integers(0, 256, (256, 384), np.uint8), (3, 3), .6)
        masks = {}
        for name, x in [('left', 45), ('right', 230)]:
            mask = np.zeros_like(im); mask[60:200, x:x+75] = 255
            masks[name] = mask
        return im, masks

    def test_known_translation_and_direction(self):
        im, masks = self.fixture()
        moving = warp_translation(im, (8.25, -5.5))
        report, _ = analyze_pair(im, moving, masks, ['left', 'right'])
        self.assertEqual(report['status'], 'ok')
        np.testing.assert_allclose(report['correction'], [-8.25, 5.5], atol=.45)

    def test_outside_mask_motion_does_not_vote(self):
        im, masks = self.fixture()
        moving = warp_translation(im, (5.0, 3.0))
        moving[:35] = np.random.default_rng(8).integers(0, 256, moving[:35].shape, np.uint8)
        report, _ = analyze_pair(im, moving, masks, ['left', 'right'])
        self.assertEqual(report['status'], 'ok')
        np.testing.assert_allclose(report['correction'], [-5.0, -3.0], atol=.3)

    def test_empty_image_refuses(self):
        im, masks = self.fixture(); im[:] = 128
        report, _ = analyze_pair(im, im, masks, ['left', 'right'])
        self.assertEqual(report['status'], 'needs_keyframe')
        self.assertIsNone(report['correction'])

    def test_incompatible_local_motion_refuses(self):
        im, masks = self.fixture()
        moving = warp_translation(im, (8, 0))
        other = warp_translation(im, (-8, 0))
        moving[:, 190:] = other[:, 190:]
        report, _ = analyze_pair(im, moving, masks, ['left', 'right'])
        self.assertEqual(report['status'], 'needs_keyframe')
        self.assertIsNone(report['correction'])

    def test_overlap_refuses(self):
        im, masks = self.fixture(); masks['right'] = masks['left'].copy()
        with self.assertRaises(ValueError):
            analyze_pair(im, im, masks, ['left', 'right'])

    def test_consensus_rejects_outliers(self):
        a = np.arange(40).reshape(-1, 2); b = a + [12, -4]
        b[-3:] += [35, 22]
        delta, selected = consensus_translation(a, b, 2)
        np.testing.assert_allclose(delta, [12, -4])
        self.assertEqual(int(selected.sum()), 17)

    def test_bad_shape_and_options(self):
        im, masks = self.fixture()
        with self.assertRaises(ValueError): analyze_pair(im, im[:-1], masks, ['left'])
        with self.assertRaises(ValueError): Options(fb_threshold=-1).validate()
        with self.assertRaises(ValueError): warp_translation(im, [float('nan'), 1])


if __name__ == '__main__':
    unittest.main()
