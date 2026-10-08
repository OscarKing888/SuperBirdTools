"""SuperPicky 鸟名目录客户端与手动指定 XMP；GUI/CLI 共用。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from app_common.bird_pinyin import PINYIN_FIELD, PINYIN_SOURCE_FIELD
from app_common.bird_rarity import (RARITY_FIELD, IUCN_FIELD, RARITY_SOURCE_FIELD,
                                    RARITY_MISSING_FIELD, RARITY_COMPAT_FIELDS, rarity_score)
from app_common.exif_io.photo_meta import PhotoMetaDataXMP, xmp_sidecar_write_lock
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from .bird_identification import (BirdIDClient, BirdIDOptions, BirdIDError, BirdIDCancelled,
                                  BirdIDResult, DEFAULT_URL, _fingerprint, collect_paths)


def validate_bird(bird, *, detail=False):
    """在写入前校验身份、文本及可空等级，保留零分。"""
    if not isinstance(bird, dict):
        raise BirdIDError('鸟名服务返回格式错误')
    for key in ('bird_id', 'version_id'):
        if type(bird.get(key)) is not int or bird[key] <= 0:
            raise BirdIDError(f'鸟名服务缺少有效 {key}')
    for key in ('cn_name', 'en_name', 'scientific_name', 'pinyin_name', 'pinyin_plain', 'abbreviation'):
        if not isinstance(bird.get(key), str):
            raise BirdIDError(f'鸟名字段格式错误：{key}')
    if not (bird['cn_name'].strip() or bird['en_name'].strip()):
        raise BirdIDError('鸟名为空')
    if detail:
        if not {RARITY_FIELD, IUCN_FIELD, 'description', 'china_protection_level', 'version_name'} <= bird.keys():
            raise BirdIDError('鸟种详情不完整，请更新 SuperPicky')
        score = bird[RARITY_FIELD]
        if score is not None and (type(score) not in (int, float) or rarity_score(score) is None):
            raise BirdIDError('稀有度必须为 0–100 的数值或 null')
        for key in (IUCN_FIELD, 'china_protection_level'):
            if bird[key] is not None and not isinstance(bird[key], str):
                raise BirdIDError(f'等级字段格式错误：{key}')
        for key in ('description', 'version_name'):
            if not isinstance(bird[key], str):
                raise BirdIDError(f'鸟种详情格式错误：{key}')
    return bird


class BirdCatalogClient(BirdIDClient):
    """沿用本机地址限制、超时与可取消连接；独立 GET 路由。"""

    def search(self, query='', *, offset=0, limit=100, version_id=None):
        params = dict(q=query, offset=offset, limit=limit)
        if version_id is not None:
            params['version_id'] = version_id
        data = self._request('/birds/search?' + urlencode(params))
        if data.get('success') is not True or not isinstance(data.get('results'), list):
            raise BirdIDError('鸟名搜索失败，请确认 SuperPicky 已升级并启用鸟名服务')
        for key in ('total', 'offset', 'limit', 'version_id'):
            if type(data.get(key)) is not int or data[key] < 0:
                raise BirdIDError(f'鸟名列表分页格式错误：{key}')
        if data['offset'] != offset or data['limit'] != limit or len(data['results']) > limit:
            raise BirdIDError('鸟名列表分页与请求不匹配')
        for bird in data['results']:
            validate_bird(bird)
            if bird['version_id'] != data['version_id']:
                raise BirdIDError('鸟名列表版本不一致')
        return data

    def detail(self, bird_id, version_id):
        data = self._request('/birds/detail?' + urlencode(dict(bird_id=bird_id, version_id=version_id)))
        if data.get('success') is not True:
            raise BirdIDError(str(data.get('error', '鸟种详情查询失败')))
        bird = validate_bird(data.get('bird'), detail=True)
        if (bird['bird_id'], bird['version_id']) != (bird_id, version_id):
            raise BirdIDError('返回鸟种与所选鸟种不一致，未保存')
        return bird


def manual_fields(bird):
    """手动指定不伪造模型置信度；简介独立保存，保留用户备注。"""
    validate_bird(bird, detail=True)
    title = bird['cn_name'].strip() or bird['en_name'].strip()
    values = dict(bird_species_cn=bird['cn_name'], bird_species_en=bird['en_name'], title=title,
                  birdid_confidence='', alt_species_cn='', alt_species_en='', alt_confidence='',
                  scientific_name=bird['scientific_name'], pinyin_plain=bird['pinyin_plain'],
                  bird_species_abbreviation=bird['abbreviation'], bird_species_description=bird['description'],
                  china_protection_level=bird['china_protection_level'] or '',
                  bird_species_source='manual', bird_catalog_id=bird['bird_id'],
                  bird_catalog_version=bird['version_name'],
                  bird_catalog_response=json.dumps(bird, ensure_ascii=False, allow_nan=False))
    values.update({PINYIN_FIELD: bird['pinyin_name'], PINYIN_SOURCE_FIELD: title,
                   RARITY_FIELD: bird[RARITY_FIELD] if bird[RARITY_FIELD] is not None else '',
                   IUCN_FIELD: bird[IUCN_FIELD] or '', RARITY_SOURCE_FIELD: title})
    values[RARITY_MISSING_FIELD] = ','.join(k for k in (RARITY_FIELD, IUCN_FIELD) if values[k] == '')
    fields = {f'XMP-superpicky:{k}': v for k, v in values.items()}
    fields['XMP-dc:Title'] = title
    fields[RARITY_COMPAT_FIELDS[RARITY_FIELD]] = f'{values[RARITY_FIELD]:.2f}' if values[RARITY_FIELD] != '' else ''
    fields[RARITY_COMPAT_FIELDS[IUCN_FIELD]] = values[IUCN_FIELD]
    return values, fields


def snapshot(source):
    """网络请求前记录照片和侧车身份；不阻塞 GUI。"""
    with xmp_sidecar_write_lock(source):
        return _fingerprint(source), _fingerprint(PhotoMetaDataXMP().sidecar_path_for(source))


def apply_bird(source, bird, before, *, cancelled=lambda: False):
    """原子保存到同名 XMP，拒绝查询期间的移动/编辑。"""
    source = os.path.normpath(os.path.abspath(source))
    store = PhotoMetaDataXMP()
    try:
        values, fields = manual_fields(bird)
        with xmp_sidecar_write_lock(source):
            if cancelled():
                raise BirdIDCancelled('已停止，未写入此照片')
            if Path(source).suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS or not Path(source).is_file():
                raise BirdIDError('不是存在的受支持照片')
            if before != snapshot(source):
                return BirdIDResult(source, 'skipped', '照片或 XMP 已变化，请重试')
            if store._load_or_create_xmp_tree(store.sidecar_path_for(source)) is None:
                raise BirdIDError('已有 XMP 损坏，未写入')
            if not store.write(source, fields):
                raise BirdIDError('XMP 保存失败，原元数据已保留')
            saved = _fingerprint(store.sidecar_path_for(source))
        return BirdIDResult(source, 'success', f'已指定：{values["title"]}',
                            updates={**values, **fields, 'Title': values['title']},
                            saved_fingerprint=saved, source_fingerprint=before[0])
    except BirdIDCancelled as exc:
        return BirdIDResult(source, 'cancelled', str(exc))
    except Exception as exc:
        return BirdIDResult(source, 'failed', f'[BirdCatalog] {exc}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='查询 SuperPicky 鸟名目录，或指定鸟种到照片 XMP')
    parser.add_argument('--url', default=DEFAULT_URL)
    parser.add_argument('--query', default='')
    parser.add_argument('--bird-id', type=int)
    parser.add_argument('--version-id', type=int)
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('paths', nargs='*')
    args = parser.parse_args(argv)
    if args.paths and args.bird_id is None:
        parser.error('写入照片需要 --bird-id 和 --version-id')
    if args.bird_id is not None and args.version_id is None:
        parser.error('指定 bird-id 时需要 --version-id')
    client = BirdCatalogClient(BirdIDOptions(url=args.url))
    try:
        if args.bird_id is None:
            print(json.dumps(client.search(args.query, offset=args.offset), ensure_ascii=False))
            return 0
        paths = collect_paths(args.paths)
        before = {p: snapshot(p) for p in paths}
        bird = client.detail(args.bird_id, args.version_id)
        if not args.paths:
            print(json.dumps(bird, ensure_ascii=False))
            return 0
        if not paths:
            raise BirdIDError('没有找到受支持的照片')
        failed = False
        for path in paths:
            result = apply_bird(path, bird, before[path], cancelled=client.cancelled.is_set)
            print(json.dumps({'source': path, 'status': result.status, 'message': result.message}, ensure_ascii=False))
            failed |= result.status != 'success'
        return int(failed)
    except (BirdIDError, KeyboardInterrupt) as exc:
        print(f'[BirdCatalog] {exc}')
        return 1
    finally:
        client.cancel()


if __name__ == '__main__':
    raise SystemExit(main())
