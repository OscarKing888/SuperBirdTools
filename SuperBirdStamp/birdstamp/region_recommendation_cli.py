"""一键推荐的命令行入口；输出可直接用于 stabilize --regions。"""
from dataclasses import asdict
from pathlib import Path
import json
from .image_dejitter.region_recommendation import recommend_regions


def recommend_files(frames, reference, output, *, method='reference_region', part='auto', target_index=None,
                    experimental=False, target_count=9, model_file=None, progress=lambda text:None):
    from .image_dejitter.bird_candidates import detect_bird_candidates
    from .decoders.image_decoder import decode_image
    from .image_dejitter.bird_parts.pose import predict_parts
    reference=Path(reference).resolve()
    target=None
    if target_index is not None:
        with decode_image(reference,decoder='auto') as image:
            birds=detect_bird_candidates(image)
        if not 1 <= target_index <= len(birds):
            raise ValueError(f'目标编号超出范围；共 {len(birds)} 只鸟。')
        target=birds[target_index-1].box
    predictor=(lambda image,box,**kwargs:predict_parts(image,box,path=Path(model_file),**kwargs)) if model_file else None
    result=recommend_regions(reference,tuple(Path(p).resolve() for p in frames),method=method,part=part,
        target=target,experimental=experimental,target_count=target_count,pose_predictor=predictor,progress=progress)
    payload=asdict(result)
    payload['method']=method
    # 不覆盖已有报告；输入照片不写入。
    with Path(output).open('x',encoding='utf-8') as stream:
        json.dump(payload,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return result
