"""识鸟协议、真实中文 XMP、原图/报告保护和取消；测试不访问网络。"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.report_db import ReportDB
from SuperViewer.superviewer import bird_identification as bird


@pytest.fixture
def response():
    return {"success": True, "results": [
        {"rank": 1, "cn_name": "白头鹎", "en_name": "Light-vented Bulbul", "scientific_name": "Pycnonotus sinensis",
         "display_name": "白头鹎", "confidence": 95.5, "ebird_match": False, "description": "常见留鸟，叫声清脆。"},
        {"rank": 2, "cn_name": "红耳鹎", "en_name": "Red-whiskered Bulbul", "confidence": 51.2}],
        "yolo_info": "conf=0.9, size=(400, 300)", "gps_info": {"latitude": 0.0, "longitude": 110.5},
        "geo_info": {"enabled": True, "tier": "country", "region_code": "CN"}, "warning": "仅使用国家级过滤"}


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "中文鸟片.jpg"
    Image.new("RGB", (32, 24), "green").save(path)
    return path


def client_for(monkeypatch, response, options=None):
    client = bird.BirdIDClient(options or bird.BirdIDOptions())
    monkeypatch.setattr(client, "recognize", lambda path: copy.deepcopy(response))
    return client


def test_chinese_fields_full_response_and_existing_metadata_roundtrip(photo, response, monkeypatch):
    store = PhotoMetaDataXMP()
    assert store.write(str(photo), {"XMP-dc:Description": "我的拍摄笔记", "XMP-dc:Subject": ["观鸟", "收藏"], "XMP-xmp:Rating": 4,
                                    "XMP-superpicky:alt_species_cn": "旧候选"})
    before = photo.read_bytes()
    result = bird.identify_file(str(photo), client_for(monkeypatch, response))
    assert result.status == "success", result.message
    values = store.read(str(photo))
    assert values["Title"] == values["bird_species_cn"] == "白头鹎"
    assert values["bird_species_en"] == "Light-vented Bulbul"
    assert float(values["birdid_confidence"]) == 95.5
    assert json.loads(values["birdid_response"]) == response
    assert values["Description"] == "我的拍摄笔记" and values["rating"] == 4
    assert store.read_subjects(str(photo)) == ["观鸟", "收藏"]
    assert "alt_species_cn" not in values
    assert "白头鹎" in photo.with_suffix(".xmp").read_text(encoding="utf-8")
    assert photo.read_bytes() == before


def test_low_confidence_uses_existing_candidate_fields_and_preserves_confirmed(photo, response, monkeypatch):
    store = PhotoMetaDataXMP()
    assert store.write(str(photo), {"XMP-dc:Title": "原有鸟名", "XMP-superpicky:bird_species_cn": "原有鸟名"})
    result = bird.identify_file(str(photo), client_for(monkeypatch, response, bird.BirdIDOptions(threshold=99)))
    assert result.status == "candidate"
    values = store.read(str(photo))
    assert values["Title"] == values["bird_species_cn"] == "原有鸟名"
    assert values["alt_species_cn"] == "白头鹎" and float(values["alt_confidence"]) == 95.5


@pytest.mark.parametrize("bad", [{"success": False, "error": "无鸟"}, {"success": True, "results": []},
    {"success": True, "results": [{"cn_name": "鸟", "confidence": float("nan")}]},
    {"success": True, "results": [{"cn_name": "鸟", "confidence": 101}]},
    {"success": True, "results": [{"cn_name": "", "confidence": 90}]}])
def test_failure_never_writes(photo, bad, monkeypatch):
    assert bird.identify_file(str(photo), client_for(monkeypatch, bad)).status == "failed"
    assert not photo.with_suffix(".xmp").exists()


@pytest.mark.parametrize("change", ["edit", "rename", "replace", "cancel"])
def test_inflight_changes_or_cancellation_do_not_commit(photo, response, monkeypatch, change):
    client = client_for(monkeypatch, response)
    def recognize(path):
        if change == "edit":
            PhotoMetaDataXMP().write_title(path, "用户新编辑")
        elif change == "rename":
            photo.rename(photo.with_name("已移动.jpg"))
        elif change == "replace":
            photo.write_bytes(b"replacement")
        else:
            client.cancel()
        return response
    monkeypatch.setattr(client, "recognize", recognize)
    result = bird.identify_file(str(photo), client)
    assert result.status == ("cancelled" if change == "cancel" else "skipped")
    if change == "edit":
        assert PhotoMetaDataXMP().read(str(photo))["Title"] == "用户新编辑"
    else:
        assert not photo.with_suffix(".xmp").exists()


def test_corrupt_sidecar_missing_photo_and_skip_existing(photo, response, monkeypatch):
    client = client_for(monkeypatch, response, bird.BirdIDOptions(skip_existing=True))
    assert PhotoMetaDataXMP().write_superpicky_fields(str(photo), {"bird_species_cn": "已有鸟名"})
    monkeypatch.setattr(client, "recognize", lambda _: pytest.fail("不应请求服务"))
    assert bird.identify_file(str(photo), client).status == "skipped"
    sidecar = photo.with_suffix(".xmp")
    sidecar.write_bytes(b"<broken")
    assert bird.identify_file(str(photo), client).status == "failed"
    assert sidecar.read_bytes() == b"<broken"
    photo.unlink()
    assert bird.identify_file(str(photo), client).status == "failed"


def test_directory_scope_shared_xmp_raw_priority_and_no_symlink_recursion(photo):
    raw = photo.with_suffix(".ARW")
    raw.write_bytes(b"raw")
    nested = photo.parent / "子目录"
    nested.mkdir()
    child = nested / "第二张.HIF"
    child.write_bytes(b"heif")
    (photo.parent / "video.mp4").write_bytes(b"video")
    (photo.parent / "loop").symlink_to(photo.parent, target_is_directory=True)
    assert bird.collect_paths([photo.parent]) == [str(raw)]
    assert bird.collect_paths([photo.parent], recursive=True) == [str(raw), str(child)]
    assert bird.collect_paths([photo, raw, photo]) == [str(raw)]
    assert bird.collect_paths([photo.parent], recursive=True, cancelled=lambda: True) == []


def test_report_hydration_does_not_write_database(photo, response, monkeypatch):
    db = ReportDB(str(photo.parent))
    try:
        db.insert_photo({"filename": photo.stem, "original_path": str(photo), "current_path": str(photo),
                         "bird_species_cn": "旧鸟名", "rating": 0, "pick": 0, "caption": "旧库说明"})
    finally:
        db.close()
    db_path = photo.parent / ".superpicky/report.db"
    before = db_path.read_bytes()
    result = bird.identify_file(str(photo), client_for(monkeypatch, response))
    assert result.status == "success", result.message
    values = PhotoMetaDataXMP().read(str(photo))
    assert values["bird_species_cn"] == "白头鹎" and values["Description"] == "旧库说明"
    assert values["rating"] == 0 and values["pick"] == 0
    assert db_path.read_bytes() == before


def test_atomic_publish_failure_preserves_complete_old_sidecar(photo, response, monkeypatch):
    store = PhotoMetaDataXMP()
    assert store.write_title(str(photo), "原始标题")
    before = photo.with_suffix(".xmp").read_bytes()
    monkeypatch.setattr(store.__class__, "_write_tree_atomic", staticmethod(lambda *_: False))
    result = bird.identify_file(str(photo), client_for(monkeypatch, response))
    assert result.status == "failed"
    assert photo.with_suffix(".xmp").read_bytes() == before


def test_http_payload_health_errors_and_owned_connection_cleanup(photo, response, monkeypatch):
    requests = []
    responses = [{"status": "ok", "service": "SuperPicky BirdID API"}, response]
    connections = []
    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 5156, 3)
            self.sock = SimpleNamespace(settimeout=lambda _: None)
            self.closed = False
            connections.append(self)
        def connect(self): pass
        def request(self, method, endpoint, body, headers): requests.append((method, endpoint, body))
        def getresponse(self):
            body = json.dumps(responses.pop(0)).encode()
            return SimpleNamespace(status=200, read=lambda _: body)
        def close(self): self.closed = True
    monkeypatch.setattr(bird.http.client, "HTTPConnection", Connection)
    client = bird.BirdIDClient(bird.BirdIDOptions())
    client.health()
    assert client.recognize(str(photo)) == response
    assert requests[0][:2] == ("GET", "/health")
    assert requests[1][:2] == ("POST", "/recognize")
    payload = json.loads(requests[1][2])
    assert payload["image_path"] == str(photo) and payload["top_k"] == 3
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize("url", ["https://127.0.0.1:5156", "http://example.com", "http://user:secret@localhost", "http://localhost/path", "http://localhost:0"])
def test_remote_and_invalid_endpoints_rejected(url):
    with pytest.raises(ValueError):
        bird.BirdIDOptions(url=url).validate()


def test_cli_uses_same_core(photo, response, monkeypatch, capsys):
    monkeypatch.setattr(bird.BirdIDClient, "health", lambda _: {})
    monkeypatch.setattr(bird.BirdIDClient, "recognize", lambda *_: response)
    assert bird.main([str(photo)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "success"


@pytest.mark.parametrize('scenario', ['http', 'invalid_json', 'oversize', 'timeout', 'wrong_health'])
def test_http_failures_close_connections(monkeypatch, scenario):
    seen = []
    class Connection:
        def __init__(self, *args, **kwargs):
            self.sock = SimpleNamespace(settimeout=lambda _: None)
            self.closed = False
            seen.append(self)
        def connect(self): pass
        def request(self, *args):
            if scenario == 'timeout': raise TimeoutError('超时')
        def getresponse(self):
            body = {
                'http': b'{"error":"failed"}', 'invalid_json': b'<html>',
                'oversize': b'x' * (bird.MAX_RESPONSE_BYTES + 1),
                'wrong_health': b'{"status":"ok", "service":"other"}',
            }[scenario]
            return SimpleNamespace(status=500 if scenario == 'http' else 200, reason='Error', read=lambda _: body)
        def close(self): self.closed = True
    monkeypatch.setattr(bird.http.client, 'HTTPConnection', Connection)
    with pytest.raises(bird.BirdIDError):
        bird.BirdIDClient(bird.BirdIDOptions()).health()
    assert len(seen) == 1 and seen[0].closed


def test_cancel_interrupts_http10_response_after_connection_detaches_socket(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    reading, interrupted = threading.Event(), threading.Event()
    class Sock:
        def settimeout(self, _): pass
        def shutdown(self, _): interrupted.set()
    class Connection:
        def __init__(self, *args, **kwargs): self.sock = Sock()
        def connect(self): pass
        def request(self, *args): pass
        def getresponse(self):
            # HTTP/1.0 / Connection: close 会在读取 body 前清除 connection.sock。
            self.sock = None
            def read(_):
                reading.set()
                assert interrupted.wait(3)
                return b''
            return SimpleNamespace(status=200, read=read)
        def close(self): pass
    monkeypatch.setattr(bird.http.client, 'HTTPConnection', Connection)
    client = bird.BirdIDClient(bird.BirdIDOptions())
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.recognize, '中文照片.jpg')
        assert reading.wait(3)
        client.cancel()
        with pytest.raises(bird.BirdIDCancelled):
            pending.result(timeout=3)


@pytest.mark.parametrize("threshold", [50, 99])
def test_optional_service_pinyin_uses_canonical_xmp_only_for_confirmed(photo, response, monkeypatch, threshold):
    response["results"][0]["pinyin_name"] = "bái tóu bēi"
    store = PhotoMetaDataXMP()
    assert store.write(str(photo), {"XMP-superpicky:pinyin_name": "old", "XMP-dc:Title": "旧鸟名"})
    result = bird.identify_file(str(photo), client_for(monkeypatch, response, bird.BirdIDOptions(threshold=threshold)))
    values = store.read(str(photo))
    assert result.status == ("success" if threshold == 50 else "candidate")
    assert values["pinyin_name"] == ("bái tóu bēi" if threshold == 50 else "old")
    if threshold == 50:
        assert values["pinyin_name_source"] == "白头鹎"


@pytest.mark.parametrize('score,category', [(0, 'LC'), (75.5, 'VU'), (100, 'CR'), (None, None)])
def test_rarity_and_iucn_roundtrip_and_browser_reload(photo, response, monkeypatch, score, category):
    from app_common.bird_rarity import rarity_metadata
    from app_common.file_browser._workers import MetadataLoader
    response['results'][0].update(gbif_rarity_100=score, iucn_category=category)
    before = photo.read_bytes()
    result = bird.identify_file(str(photo), client_for(monkeypatch, response))
    assert result.status == 'success', result.message
    meta = PhotoMetaDataXMP().read(str(photo))
    assert rarity_metadata(meta) == (score, category or '')
    if score is not None:
        assert float(meta['gbif_rarity_100']) == score
        assert float(meta['XMP-Iptc4xmpExt:Event']) == score
        assert meta['XMP-Iptc4xmpCore:IntellectualGenre'] == category
    loader = MetadataLoader([str(photo)], meta_proxy=object())
    parsed = loader._parse_rec(meta)
    assert rarity_metadata(parsed) == (score, category or '')
    assert photo.read_bytes() == before


@pytest.mark.parametrize('confirmed', [False, True])
def test_missing_rarity_preserves_unconfirmed_but_clears_confirmed_old_species(photo, response, monkeypatch, confirmed):
    from app_common.bird_rarity import rarity_metadata
    store = PhotoMetaDataXMP()
    assert store.write(str(photo), {'XMP-dc:Title': '家燕', 'XMP-superpicky:gbif_rarity_100': 99,
        'XMP-superpicky:iucn_category': 'CR', 'XMP-iptcExt:Event': '99', 'XMP-iptcCore:IntellectualGenre': 'CR'})
    response['results'][0].update(gbif_rarity_100=None, iucn_category=None)
    result = bird.identify_file(str(photo), client_for(monkeypatch, response, bird.BirdIDOptions(threshold=50 if confirmed else 99)))
    assert result.status == ('success' if confirmed else 'candidate'), result.message
    meta = store.read(str(photo))
    # 模拟 report.db 中仍有原鸟种数据：明确 null 不能回填成原先的 99/CR。
    meta.update({'report.gbif_rarity_100': 99, 'report.iucn_category': 'CR'})
    assert rarity_metadata(meta) == ((None, '') if confirmed else (99, 'CR'))
    if confirmed:
        assert 'XMP-Iptc4xmpExt:Event' not in meta and 'XMP-Iptc4xmpCore:IntellectualGenre' not in meta


@pytest.mark.parametrize('field,value', [('gbif_rarity_100', True), ('gbif_rarity_100', -1),
    ('gbif_rarity_100', 101), ('gbif_rarity_100', '75'), ('gbif_rarity_100', float('nan')),
    ('gbif_rarity_100', float('inf')), ('iucn_category', 0)])
def test_invalid_rarity_response_does_not_modify_sidecar(photo, response, monkeypatch, field, value):
    store = PhotoMetaDataXMP()
    assert store.write_title(str(photo), '保留原值')
    before = photo.with_suffix('.xmp').read_bytes()
    response['results'][0][field] = value
    assert bird.identify_file(str(photo), client_for(monkeypatch, response)).status == 'failed'
    assert photo.with_suffix('.xmp').read_bytes() == before


@pytest.mark.parametrize('score,category', [(0, 'LC'), (None, None), (75, None), (None, 'NT')])
def test_report_old_values_do_not_revive_after_browser_or_proxy_reload(photo, response, monkeypatch, score, category):
    from app_common.bird_rarity import rarity_metadata
    from app_common.exif_io.photo_meta import PhotoMetaDataProxy, PhotoMetaDataReportDB
    from app_common.file_browser._workers import MetadataLoader
    row = {'filename': photo.stem, 'original_path': str(photo), 'current_path': str(photo),
           'bird_species_cn': '家燕', 'gbif_rarity_100': 99, 'iucn_category': 'CR', 'rating': 0, 'caption': '保留说明'}
    db = ReportDB(str(photo.parent))
    try:
        db.insert_photo(row)
    finally:
        db.close()
    db_path = photo.parent / '.superpicky/report.db'
    before = db_path.read_bytes()
    response['results'][0].update(gbif_rarity_100=score, iucn_category=category)
    result = bird.identify_file(str(photo), client_for(monkeypatch, response))
    assert result.status == 'success', result.message
    proxy = PhotoMetaDataProxy(report_db=PhotoMetaDataReportDB(str(photo.parent)))
    assert rarity_metadata(proxy.read(str(photo))) == (score, category or '')
    loader = MetadataLoader([str(photo)], meta_proxy=proxy, report_rows_by_path={str(photo): row})
    parsed, _, _ = loader._read_parse_chunk([str(photo)])
    assert parsed[str(photo)]['bird_species_cn'] == '白头鹎'
    assert rarity_metadata(parsed[str(photo)]) == (score, category or '')
    assert db_path.read_bytes() == before
