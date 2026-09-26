from __future__ import annotations

from PIL import Image

from .constants import DEFAULT_MIN_CONFIDENCE, DEFAULT_PATCH_SIZE
from .de_jitter_frame import DeJitterFrame
from .normalized_box import NormalizedBox
from .numpy_phase_correlation_aligner import NumpyPhaseCorrelationAligner
from .reference_region_stabilization_strategy import ReferenceRegionStabilizationStrategy
from .region_patch import extract_region_patch
from .region_tracking_result import RegionTrackingResult
from .region_template_search import RegionTemplateSearch


class ReferenceRegionTracker:
    """逐区追踪固定参考图；复用导出的平移核验，结果与补偿强度无关。"""

    def __init__(self, reference: Image.Image, regions: tuple[NormalizedBox, ...]):
        self.regions = tuple(regions)
        self.patches = tuple(extract_region_patch(reference, box, DEFAULT_PATCH_SIZE) for box in regions)
        self.aligner = NumpyPhaseCorrelationAligner()
        self.search = RegionTemplateSearch(reference, self.regions)

    def track(self, image: Image.Image, *, cancelled=lambda: False) -> RegionTrackingResult:
        boxes, errors = [], []
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
            if delta is None:
                if search_image is None:
                    search_image = self.search.search_image(image)
                displacement, reason = self.search.locate(image, search_image, index, cancelled=cancelled)
                if displacement is None:
                    boxes.append(None)
                    errors.append(f'选区 {index+1}：{reason}')
                    continue
                dx, dy = displacement
            else:
                dx, dy = delta[0] / image.width, delta[1] / image.height
            box = tuple(float(value) for value in (region[0] + dx, region[1] + dy, region[2] + dx, region[3] + dy))
            # 跟踪框保持原尺寸；完全离开画面的匹配不显示为成功。
            boxes.append(box if box[2] > 0 and box[3] > 0 and box[0] < 1 and box[1] < 1 else None)
        return RegionTrackingResult(tuple(boxes), error='；'.join(errors))

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
            displacement = self.search.recover_occlusion(image, index, expected, cancelled=cancelled)
            if displacement is not None:
                dx, dy = displacement
                boxes[index] = tuple(float(value) for value in (region[0]+dx, region[1]+dy, region[2]+dx, region[3]+dy))
        return RegionTrackingResult(tuple(boxes), signature=result.signature,
                                    error=result.error if any(box is None for box in boxes) else '')
