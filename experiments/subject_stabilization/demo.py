"""Run pairs with existing detector boxes and manually selected local polygons.

python demo.py --images INPUT_DIR --manifest manifest.json --out OUTPUT_DIR
No detector inference, weights, Qt, EXIF writes, or source-file modifications.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps
from core import Options, analyze_pair, warp_translation


def read_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(ImageOps.exif_transpose(image).convert('RGB')).copy()


def polygon_masks(shape: tuple[int, int], regions: dict, target_box=None) -> dict:
    import cv2
    h, w = shape
    masks = {}
    for name, polygons in regions.items():
        mask = np.zeros((h, w), dtype=np.uint8)
        for polygon in polygons:
            points = np.asarray(polygon, dtype=np.float64)
            if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3:
                raise ValueError(f'{name}: polygon needs >=3 (x, y) points')
            if not np.isfinite(points).all() or (points < 0).any() or (points > 1).any():
                raise ValueError(f'{name}: expected finite normalized coordinates [0, 1]')
            pixels = np.rint(points * [w, h]).astype(np.int32)
            pixels[:, 0] = np.clip(pixels[:, 0], 0, w-1)
            pixels[:, 1] = np.clip(pixels[:, 1], 0, h-1)
            cv2.fillPoly(mask, [pixels], 255)
        masks[name] = mask
    if target_box is not None:
        box = np.asarray(target_box, dtype=float)
        if (box.shape != (4,) or not np.isfinite(box).all() or (box < 0).any()
                or (box > 1).any() or box[2] <= box[0] or box[3] <= box[1]):
            raise ValueError('target_box must be normalized xyxy')
        x0, y0, x1, y1 = np.rint(box*[w, h, w, h]).astype(int)
        limit = np.zeros((h, w), np.uint8); limit[y0:y1, x0:x1] = 255
        masks = {name: cv2.bitwise_and(mask, limit) for name, mask in masks.items()}
    return masks


def basic_overlay(image, masks, tracks, report, *, moving=False):
    import cv2
    out = image.copy()
    correction = report.get('correction')
    for name, mask in masks.items():
        track = tracks[name]
        selected = name in report['selected_regions']
        color = np.array((55, 231, 147) if selected else (255, 201, 75))
        display_mask = mask
        if moving and track.displacement is not None:
            dx, dy = track.displacement
            display_mask = cv2.warpAffine(mask, np.float32([[1,0,dx],[0,1,dy]]),
                                          (mask.shape[1],mask.shape[0]), flags=cv2.INTER_NEAREST)
        active = display_mask > 0
        out[active] = (out[active]*.73 + color*.27).astype(np.uint8)
        points = track.moving if moving else track.reference
        for idx, p in enumerate(points):
            if not np.isfinite(p).all():
                continue
            used = bool(selected and correction is not None and track.valid[idx]
                        and np.linalg.norm(track.moving[idx]-track.reference[idx]+correction)
                        < report['options']['consensus_threshold'])
            if used:
                cv2.circle(out, tuple(np.rint(p).astype(int)), 3, (55,255,147), -1)
            elif selected:
                cv2.drawMarker(out, tuple(np.rint(p).astype(int)), (255,70,100),
                               markerType=cv2.MARKER_TILTED_CROSS, markerSize=6, thickness=1)
    return out


def run(images: Path, manifest: dict, out: Path) -> list:
    import cv2
    if images.resolve() == out.resolve():
        raise ValueError('output directory must differ from source directory')
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    for number, pair in enumerate(manifest['pairs'], 1):
        a = read_rgb(images/pair['reference']); b = read_rgb(images/pair['moving'])
        if a.shape != b.shape:
            raise ValueError('pair image dimensions differ; normalize coordinates explicitly')
        gray_a = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY); gray_b = cv2.cvtColor(b, cv2.COLOR_RGB2GRAY)
        masks = polygon_masks(gray_a.shape, pair['regions'], pair.get('target_box'))
        report, tracks = analyze_pair(gray_a, gray_b, masks, pair['fit_regions'],
                                     Options(**manifest.get('options', {})))
        report.update(reference=pair['reference'], moving=pair['moving'],
                      input_size=[a.shape[1],a.shape[0]], roi_provenance=manifest.get('roi_provenance','caller supplied'))
        folder = out/f'pair_{number}'; folder.mkdir(exist_ok=True)
        Image.fromarray(basic_overlay(a,masks,tracks,report)).save(folder/'debug_reference.png')
        Image.fromarray(basic_overlay(b,masks,tracks,report,moving=True)).save(folder/'debug_moving.png')
        if report['status'] == 'ok':
            stabilized = warp_translation(b, report['correction'])
            Image.fromarray(stabilized).save(folder/'stabilized.png')
            dx, dy = report['correction']; h,w = gray_a.shape
            # Common valid support for a 4-pixel interpolation margin.
            crop = (max(0,int(np.ceil(dx)))+4, max(0,int(np.ceil(dy)))+4,
                    min(w,int(np.floor(w+dx)))-4, min(h,int(np.floor(h+dy)))-4)
            if crop[2] > crop[0] and crop[3] > crop[1]:
                Image.fromarray(a).crop(crop).save(folder/'reference_common_crop.png')
                Image.fromarray(stabilized).crop(crop).save(folder/'stabilized_common_crop.png')
                report['common_crop_xyxy'] = list(crop)
        (folder/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        track_data = {}
        for name, t in tracks.items():
            for key in ['reference','moving','valid','inliers','fb_error']:
                track_data[f'{name}_{key}'] = getattr(t,key)
        np.savez_compressed(folder/'tracks.npz', **track_data)
        reports.append(report)
    (out/'report.json').write_text(json.dumps(reports,indent=2,ensure_ascii=False),encoding='utf-8')
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    reports=run(args.images,json.loads(args.manifest.read_text(encoding='utf-8')),args.out)
    for report in reports:
        print(report['reference'],'->',report['moving'],report['status'],report['correction'])


if __name__ == '__main__':
    main()
