# -*- coding: utf-8 -*-
"""只读载入已保存候选，复用识鸟采纳流程；不需要 HTTP 服务。"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re

from app_common.bird_pinyin import bird_name
from app_common.exif_io.photo_meta import PhotoMetaDataReportDB, PhotoMetaDataXMP, xmp_sidecar_write_lock
from .bird_identification import BirdIDError, BirdIDResult, _fingerprint, parse_response


def _field(metadata, name):
    return metadata.get(f"XMP-superpicky:{name}", metadata.get(name))


def _alt_candidate(metadata):
    """旧版结构化候选的置信度同样为 0–100；不根据数值大小猜单位。"""
    cn = _field(metadata, 'alt_species_cn') or ''
    en = _field(metadata, 'alt_species_en') or ''
    if not cn and not en:
        return None
    confidence = _field(metadata, 'alt_confidence')
    try:
        if not isinstance(cn, str) or not isinstance(en, str) or isinstance(confidence, bool):
            raise ValueError('鸟名或置信度格式错误')
        candidate = {'cn_name': cn.strip(), 'en_name': en.strip(), 'confidence': float(confidence)}
        parse_response({'success': True, 'results': [candidate]})
        return candidate
    except (ValueError, TypeError, BirdIDError) as exc:
        raise BirdIDError(f'旧版待确定候选格式错误：{exc}') from exc


def _legacy_candidates(metadata, report_metadata):
    # 只接受完整的旧版待确定标题，不能把普通标题或说明中的数字猜成候选。
    pending = re.fullmatch(r'(.+?)\s*[（(]\s*待确定\s+(\d+(?:\.\d+)?)\s*[%％]\s*[）)]',
                           bird_name(metadata))
    candidate = None
    if pending:
        candidate = {'cn_name': pending[1].strip(), 'en_name': '', 'confidence': float(pending[2])}
        parse_response({'success': True, 'results': [candidate]})
    # XMP 自身候选优先；报告只是只读补充，不能把另一鸟种的名称/分数接到当前标题上。
    alternate = _alt_candidate(metadata)
    if alternate is None:
        alternate = _alt_candidate(report_metadata or {})
    if candidate is None:
        candidate = alternate
    elif (alternate and alternate['cn_name'] == candidate['cn_name']
          and round(alternate['confidence'], len(pending[2].partition('.')[2])) == candidate['confidence']):
        candidate = alternate
    if candidate is None:
        raise BirdIDError('没有可用的已保存候选，请先识别鸟种，或使用“手动指定鸟名”')
    return {'success': True, 'results': [candidate],
            'warning': '旧版待确定记录，仅保留一个候选；请核对后采纳'}


def saved_candidates(metadata, *, report_metadata=None):
    """新字段决定候选集合；旧响应仅补充同名同分候选的详情。"""
    raw = _field(metadata, 'birdid_response')
    try:
        original = json.loads(raw) if isinstance(raw, str) else deepcopy(raw)
        parse_response(original)
    except (ValueError, TypeError, BirdIDError):
        original = None
    compact = _field(metadata, 'birdid_candidates')
    missing = compact is None or compact == ''
    if missing:
        if original is None:
            if raw is not None and raw != '':
                raise BirdIDError('已保存识鸟响应格式错误，请重新识别鸟种')
            # 旧标题/alt 字段不是服务完整响应；采纳只补存候选，不伪造服务原文。
            return _legacy_candidates(metadata, report_metadata), '', True
        return original, raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False), True
    try:
        candidates = json.loads(compact) if isinstance(compact, str) else deepcopy(compact)
        if not isinstance(candidates, list):
            raise ValueError('候选必须为数组')
        for item in candidates:
            if (not isinstance(item, dict) or not isinstance(item.get('confidence'), (int, float))
                    or isinstance(item.get('confidence'), bool)):
                raise ValueError('置信度必须为 0–100 的数值')
        response = {'success': True, 'results': candidates}
        parse_response(response)
    except (ValueError, TypeError, BirdIDError) as exc:
        raise BirdIDError(f'已保存候选格式错误：{exc}') from exc
    # 匹配候选身份，不能凭数组下标从旧响应拿来另一个鸟种的稀有度等字段。
    enriched = []
    for item in candidates:
        match = next((entry for entry in original['results'] if all(
            entry.get(key, '') == item.get(key, '') for key in ('cn_name', 'en_name'))
            and float(entry['confidence']) == float(item['confidence'])), {}) if original else {}
        enriched.append({**match, 'cn_name': item.get('cn_name', ''),
                         'en_name': item.get('en_name', ''), 'confidence': item['confidence']})
    response = {**(original or {}), 'success': True, 'results': enriched}
    record = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False) if raw is not None else None
    return response, record, False


def load_saved_candidates(source, *, cancelled=lambda: False, report_reader=None):
    source = os.path.normpath(os.path.abspath(source))
    store = PhotoMetaDataXMP()
    try:
        with xmp_sidecar_write_lock(source):
            if cancelled():
                return BirdIDResult(source, 'cancelled', '已停止读取候选')
            if not Path(source).is_file():
                raise BirdIDError('照片已不存在')
            sidecar = store.sidecar_path_for(source)
            before = _fingerprint(source), _fingerprint(sidecar)
            if before[1] is None:
                return BirdIDResult(source, 'skipped', '没有已保存候选，请先识别鸟种')
            if store._load_or_create_xmp_tree(sidecar) is None:
                raise BirdIDError('已有 XMP 损坏，无法读取候选')
            metadata = store.read(source)
            report = None
            if not _field(metadata, 'birdid_candidates') and not _field(metadata, 'birdid_response'):
                report = (report_reader or PhotoMetaDataReportDB()).row_for(source)
            response, record, missing = saved_candidates(metadata, report_metadata=report)
            if before != (_fingerprint(source), _fingerprint(sidecar)):
                raise BirdIDError('读取期间照片或 XMP 已变化，请重试')
        if cancelled():
            return BirdIDResult(source, 'cancelled', '已停止读取候选')
        name = bird_name(metadata) or _field(metadata, 'bird_species_en') or ''
        accepted = next((i for i, item in enumerate(response['results'])
                         if name and name == (item.get('cn_name') or item.get('en_name'))), None)
        best = max(response['results'], key=lambda item: float(item['confidence']))
        message = f"当前鸟名：{name or '未确认'}；最高置信度：{best.get('cn_name') or best['en_name']}"
        if missing:
            message += '；采纳时补存候选列表'
        if not record and missing:
            message += '；' + response['warning']
        return BirdIDResult(source, 'success' if accepted is not None else 'candidate', message,
                            response=response, saved_fingerprint=before[1], source_fingerprint=before[0],
                            accepted_index=accepted, response_record=record, candidates_missing=missing)
    except Exception as exc:
        return BirdIDResult(source, 'failed', f'[BirdID] {exc}')
