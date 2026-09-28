from __future__ import annotations

from PIL import Image
from dataclasses import replace

from .region_consensus import resolve_tracking_consensus, select_translation

from .constants import DEFAULT_MIN_CONFIDENCE, DEFAULT_PATCH_SIZE
from .de_jitter_frame import DeJitterFrame
from .normalized_box import NormalizedBox
from .numpy_phase_correlation_aligner import NumpyPhaseCorrelationAligner
from .reference_region_stabilization_strategy import ReferenceRegionStabilizationStrategy
from .region_patch import extract_region_patch
from .region_tracking_result import RegionTrackingResult
from .region_template_search import RegionTemplateSearch
from .matching_options import MatchingOptions


class ReferenceRegionTracker:
    """逐区追踪固定参考图；复用导出的平移核验，结果与补偿强度无关。"""

    def __init__(self, reference: Image.Image, regions: tuple[NormalizedBox, ...], *, options=MatchingOptions()):
        self.options = options
        self.regions = tuple(regions)
        self.reference_size = reference.size
        self.patches = tuple(extract_region_patch(reference, box, DEFAULT_PATCH_SIZE) for box in regions)
        self.aligner = NumpyPhaseCorrelationAligner()
        self.search = RegionTemplateSearch(reference, self.regions)

    def track(self, image: Image.Image, *, cancelled=lambda: False) -> RegionTrackingResult:
        boxes, errors, scores = [], [], []
        search_image = None
        for index, (region, patch) in enumerate(zip(self.regions, self.patches)):
            if cancelled():
                raise InterruptedError("参考区预处理已取消")
            target_patch = extract_region_patch(image, region, DEFAULT_PATCH_SIZE)
            frame = DeJitterFrame(image.width, image.height, (0, 0), (0, 0), region_patches=(target_patch,))
            delta = ReferenceRegionStabilizationStrategy._estimate_frame_displacement(
                frame=frame, regions=(region,), reference_patches=(patch,),
                aligner=self.aligner, min_confidence=DEFAULT_MIN_CONFIDENCE,
            )
            if delta is not None and self.search.verify_displacement(
                    image, index, (delta[0]/image.width, delta[1]/image.height)) < .65:
                delta = None
            if delta is None:
                if search_image is None:
                    search_image = self.search.search_image(image)
                displacement, reason = self.search.locate(image, search_image, index, cancelled=cancelled)
                if displacement is None:
                    boxes.append(None)
                    errors.append(reason)
                    scores.append(0.0)
                    continue
                dx, dy = displacement
            else:
                dx, dy = delta[0] / image.width, delta[1] / image.height
            box = tuple(float(value) for value in (region[0] + dx, region[1] + dy, region[2] + dx, region[3] + dy))
            # 跟踪框保持原尺寸；完全离开画面的匹配不显示为成功。
            score = self.search.verify_displacement(image, index, (dx,dy))
            valid = 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1 and score >= .65
            boxes.append(box if valid else None)
            scores.append(score)
            errors.append('' if valid else '匹配区域越界或相似度不足')
        raw = RegionTrackingResult(tuple(boxes), scores=tuple(scores), reasons=tuple(errors))
        result = resolve_tracking_consensus(self.regions, raw, image.size, self.reference_size, options=self.options)
        translation = select_translation(self.regions, result, image.size, self.reference_size, options=self.options)
        if translation is not None:
            boxes = list(result.boxes)
            for index, region in enumerate(self.regions):
                if cancelled():
                    raise InterruptedError('参考区预处理已取消')
                if boxes[index] is not None:
                    continue
                # 转动时各处位移不同；使用该区中心的刚性预测，不能套用整组中位平移。
                predicted = result.predicted_boxes[index]
                expected = ((predicted[0]+predicted[2]-region[0]-region[2])/2,
                            (predicted[1]+predicted[3]-region[1]-region[3])/2)
                found = self.search.locate_near(image, index, expected, cancelled=cancelled,
                                              tolerance=self.options.pixel_tolerance(image.size))
                if found is not None:
                    (rx,ry), score = found
                    box = tuple(float(value) for value in (region[0]+rx, region[1]+ry, region[2]+rx, region[3]+ry))
                    if 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1:
                        boxes[index] = box
                        scores[index] = score
            result = resolve_tracking_consensus(self.regions, replace(result, boxes=tuple(boxes), scores=tuple(scores)),
                                                image.size, self.reference_size, options=self.options)
        return result

    def recover(self, image, result, previous, following, *, cancelled=lambda: False):
        """孤立遮挡帧的第二遍核验：邻帧仅限定搜索位置，不用插值代替匹配。"""
        boxes = list(result.boxes)
        for index, region in enumerate(self.regions):
            if boxes[index] is not None:
                continue
            before, after = previous.boxes[index], following.boxes[index]
            if before is None or after is None:
                continue
            expected = ((before[0]+after[0])/2-region[0], (before[1]+after[1])/2-region[1])
            span = (abs(after[0]-before[0])/2, abs(after[1]-before[1])/2)
            displacement = self.search.recover_occlusion(image, index, expected, span=span, cancelled=cancelled)
            if displacement is not None:
                dx, dy = displacement
                boxes[index] = tuple(float(value) for value in (region[0]+dx, region[1]+dy, region[2]+dx, region[3]+dy))
        return resolve_tracking_consensus(self.regions, replace(result, boxes=tuple(boxes)),
                                          image.size, self.reference_size, options=self.options)
