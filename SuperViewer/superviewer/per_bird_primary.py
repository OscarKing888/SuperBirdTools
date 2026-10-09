# -*- coding: utf-8 -*-
"""从已保存的逐只结果选定照片主要鸟名；离线、仅写 XMP。"""
import os
from pathlib import Path

from app_common.bird_pinyin import PINYIN_FIELD, PINYIN_SOURCE_FIELD
from app_common.exif_io.photo_meta import PhotoMetaDataXMP, xmp_sidecar_write_lock
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from .bird_identification import BirdIDError, BirdIDResult, _fingerprint, candidate_fields, parse_response
from .per_bird_identification import FIELD, INFO_FIELD, read_individuals, individual_species_metadata


def individual_record_snapshot(metadata):
    return tuple(metadata.get(f'XMP-superpicky:{key}', metadata.get(key)) for key in (FIELD, INFO_FIELD))


def can_set_primary(item):
    return (item.get('status') in {'confirmed', 'candidate'}
            and bool((item.get('cn_name') or item.get('en_name') or '').strip())
            and item.get('confidence') is not None)


def set_primary_individual(source, index, expected, *, cancelled=lambda: False):
    """核对菜单绑定的列表快照；所有读取及提交均在工作线程执行。"""
    source = os.path.normpath(os.path.abspath(source))
    store = PhotoMetaDataXMP()
    try:
        with xmp_sidecar_write_lock(source):
            if cancelled():
                return BirdIDResult(source, 'cancelled', '已取消设置主要鸟名')
            if Path(source).suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS or not Path(source).is_file():
                raise BirdIDError('照片原文件不存在或格式不受支持')
            sidecar = store.sidecar_path_for(source)
            before = _fingerprint(source), _fingerprint(sidecar)
            if before[1] is None or store._load_or_create_xmp_tree(sidecar) is None:
                raise BirdIDError('逐只识别 XMP 不存在或已损坏')
            metadata = store.read(source)
            if individual_record_snapshot(metadata) != expected:
                return BirdIDResult(source, 'skipped', '逐只识别结果已变化，请刷新后重试')
            matches = [item for item in read_individuals(metadata) if item['index'] == index]
            if len(matches) != 1 or not can_set_primary(matches[0]):
                raise BirdIDError('此鸟没有可设为主要鸟名的识别结果')
            item = matches[0]
            species = individual_species_metadata(item)
            best = {**species, **{key: item.get(key, '') for key in ('cn_name', 'en_name', 'confidence')}}
            for key in ('scientific_name', 'pinyin_name', 'description'):
                best[key] = best.get(key) or ''
            parse_response({'success': True, 'results': [best]})
            response = item.get('response')
            try:
                parse_response(response)
            except BirdIDError:
                response = {'success': True, 'results': [best]}
            values, fields = candidate_fields(response, best, confirmed=True)
            # 清除前一主鸟的拼音等辅助字段，缺失等级不沿用旧鸟种。
            extra = {PINYIN_FIELD: best['pinyin_name'], PINYIN_SOURCE_FIELD: values['title'],
                     'china_protection_level': species['china_protection_level'] or '',
                     'bird_species_source': 'individual'}
            values.update(extra)
            fields.update({f'XMP-superpicky:{key}': value for key, value in extra.items()})
            if cancelled():
                return BirdIDResult(source, 'cancelled', '已取消设置主要鸟名')
            if before != (_fingerprint(source), _fingerprint(sidecar)):
                return BirdIDResult(source, 'skipped', '照片或 XMP 已变化，请重试')
            if not store.write(source, fields):
                raise BirdIDError('XMP 保存失败，原元数据已保留')
            saved = _fingerprint(sidecar)
        return BirdIDResult(source, 'success', f"已设为主要鸟名：{values['title']}",
                            updates={**values, **fields, 'Title': values['title']},
                            saved_fingerprint=saved, source_fingerprint=before[0])
    except Exception as exc:
        return BirdIDResult(source, 'failed', f'[BirdID 主要鸟名] {exc}')


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description='从已保存的逐只结果设置照片主要鸟名（无需识鸟服务）')
    parser.add_argument('photo')
    parser.add_argument('--bird', type=int, required=True, help='列表显示的鸟编号，从 1 开始')
    args = parser.parse_args(argv)
    if args.bird < 1:
        parser.error('鸟编号必须大于 0')
    expected = individual_record_snapshot(PhotoMetaDataXMP().read(args.photo))
    result = set_primary_individual(args.photo, args.bird - 1, expected)
    print(json.dumps({'status': result.status, 'message': result.message}, ensure_ascii=False))
    return int(result.status != 'success')


if __name__ == '__main__':
    raise SystemExit(main())
