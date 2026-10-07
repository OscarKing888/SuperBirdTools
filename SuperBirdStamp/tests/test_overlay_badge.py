"""徽章分级、颜色继承、真实像素及模板/工作区/导出回归。"""
from pathlib import Path
import json

from PIL import Image, ImageChops, ImageStat
import pytest
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication
from birdstamp import config
from birdstamp.overlays import badge
from birdstamp.overlays.model import document, new_item
from birdstamp.overlays.render import build_scene
from birdstamp.gui import editor_template as template
from birdstamp.gui.template_context import PhotoInfo
from birdstamp.gui.overlay_panel import OverlayPanel
from birdstamp.export_stage import VideoFrameJob, render_video_frame, source_frame_signature_for_job
from birdstamp.workspace import read_workspace_json, write_workspace_json
from app_common.bird_rarity import RARITY_DEFAULT_OPTIONS

_palette_paths = badge.palette_paths

_APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path/'user')
    path = tmp_path/'SuperViewerUser.cfg'
    path.write_text(json.dumps(RARITY_DEFAULT_OPTIONS), encoding='utf-8')
    monkeypatch.setattr(badge, 'palette_paths', lambda: [path])
    yield path
    badge._read_palette.cache_clear()


def photo(raw):
    return PhotoInfo(Path('bird.jpg'), raw_metadata=raw, metadata_is_snapshot=True)


def content(item, raw):
    return badge.badge_content(template, item, photo(raw), raw, badge.load_badge_palette())


@pytest.mark.parametrize('score,level', [
    (0,'common'), (7.99,'common'), (8,'uncommon'), (24.99,'uncommon'),
    (25,'rare'), (49.99,'rare'), (50,'epic'), (74.99,'epic'),
    (75,'legendary'), (100,'legendary'), (None,'unknown'),
    (-1,'unknown'), (101,'unknown'), ('nan','unknown'), (True,'unknown'),
])
def test_rarity_matches_existing_badges(score, level):
    item = new_item('badge')
    raw = {'XMP-superpicky:gbif_rarity_100':score}
    assert content(item,raw) == tuple(RARITY_DEFAULT_OPTIONS[f'rarity_badge_{level}_{key}']
                                    for key in ('text','background','foreground'))


def test_xmp_compat_report_zero_and_stale_species():
    item = new_item('badge')
    for raw in ({'XMP-iptcExt:Event':[60]}, {'report.gbif_rarity_100':60},
                {'XMP-superpicky:gbif_rarity_100':60, 'report.gbif_rarity_100':90}):
        assert content(item,raw)[0] == '史诗'
    assert content(item,{'XMP-superpicky:gbif_rarity_100':0,'report.gbif_rarity_100':90})[0] == '普通'
    raw = {'XMP-superpicky:birdid_rarity_source':'白鹭', 'XMP-dc:Title':'苍鹭',
           'XMP-superpicky:gbif_rarity_100':90}
    assert content(item,raw)[0] == '未知'
    raw.update({'XMP-dc:Title':'白鹭', 'XMP-superpicky:birdid_rarity_missing':'gbif_rarity_100'})
    assert content(item,raw)[0] == '未知'


def test_palette_changes_follow_auto_but_preserve_custom(isolated_config):
    item = new_item('badge'); raw = {'gbif_rarity_100':60}
    item.update(badge_background='#123456', color='#FEDCBA')
    palette = dict(RARITY_DEFAULT_OPTIONS, rarity_badge_epic_text='珍稀鸟种',
                   rarity_badge_epic_background='#12ab34', rarity_badge_epic_foreground='#223344')
    isolated_config.write_text(json.dumps(palette,ensure_ascii=False),encoding='utf-8')
    assert content(item,raw) == ('珍稀鸟种','#12AB34','#223344')
    item['badge_color_mode'] = 'custom'
    assert content(item,raw) == ('珍稀鸟种','#123456','#FEDCBA')
    item['badge_text_format'] = 'raw'
    assert float(content(item,raw)[0]) == 60
    item['badge_color_mode'] = 'auto'
    assert content(item,raw)[1:] == ('#12AB34','#223344')
    assert item['badge_background']=='#123456' and item['color']=='#FEDCBA'


def test_generic_metadata_and_literal_colors():
    item = new_item('badge')
    item.update(text_source={'type':'auto','key':'iso'}, badge_background='#123456', color='#FEDCBA')
    assert content(item,{'EXIF:ISO':800}) == ('800','#123456','#FEDCBA')
    item.update(text_mode='literal',text='白鹭\n拍摄于秋天')
    assert content(item,{'gbif_rarity_100':90}) == ('白鹭\n拍摄于秋天','#123456','#FEDCBA')


def test_palette_paths_for_source_windows_and_macos(tmp_path):
    # 恢复真实路径函数，验证不同平台的安装布局，无需对应系统运行。
    paths = _palette_paths
    assert paths(frozen=False,source_root=tmp_path)[0] == tmp_path/'SuperViewer/SuperViewerUser.cfg'
    assert paths(frozen=True,executable=tmp_path/'SuperBirdStamp/SuperBirdStamp.exe')[0] == tmp_path/'SuperViewer/SuperViewerUser.cfg'
    assert paths(frozen=True,executable=tmp_path/'SuperBirdStamp.app/Contents/MacOS/SuperBirdStamp')[0] == tmp_path/'SuperViewer.app/Contents/MacOS/SuperViewerUser.cfg'


def test_invalid_palette_falls_back(isolated_config,monkeypatch):
    from app_common import superviewer_user_options as options
    monkeypatch.setattr(options,'get_runtime_user_options',lambda:dict(RARITY_DEFAULT_OPTIONS))
    isolated_config.write_text('{',encoding='utf-8')
    assert badge.load_badge_palette()==RARITY_DEFAULT_OPTIONS
    isolated_config.write_text('{"rarity_badge_epic_background":"invalid"}',encoding='utf-8')
    assert badge.load_badge_palette()==RARITY_DEFAULT_OPTIONS


def test_rounded_pixels_transparency_and_geometry():
    item = new_item('badge'); item.update(font_size=120, badge_color_mode='custom',
        badge_background='#FF0000',color='#00FF00', layout_mode='auto',
        align_horizontal='right',align_vertical='bottom',rotation=25)
    raw = {'gbif_rarity_100':60}
    scene = build_scene({'overlays':[item]}, (1600,900), raw_metadata=raw,photo_info=photo(raw))
    try:
        layer = scene.layers[0]; pixels=layer.pixels
        assert pixels.getpixel((0,0))[3] == 0
        assert pixels.getpixel((pixels.width//2,2)) == (255,0,0,255)
        assert any(g>r and a>0 for count,(r,g,b,a) in pixels.getcolors(pixels.width*pixels.height))
        assert layer.contains(layer.center)
        manual = layer.manual_item(scene.size)
        second = build_scene({'overlays':[manual]}, scene.size,raw_metadata=raw,photo_info=photo(raw))
        try:
            assert second.layers[0].center == pytest.approx(layer.center)
            assert second.layers[0].pixels.tobytes() == pixels.tobytes()
        finally: second.close()
    finally: scene.close()


@pytest.mark.parametrize('size',[(1600,900),(900,1600)])
@pytest.mark.parametrize('mode',['auto','custom'])
def test_preview_matches_image_gif_video_pipeline(size,mode):
    item = new_item('badge'); item.update(font_size=100, rotation=23,opacity=75,
        badge_color_mode=mode,badge_background='#339966',color='#FFFFFF',shadow_enabled=True)
    payload = dict(overlays=[item],ratio='no_crop')
    raw = {'gbif_rarity_100':60}
    settings = dict(template_payload=payload,ratio='no_crop',draw_banner=False,draw_images=False)
    with Image.new('RGB',size,'#182030') as source:
        result = render_video_frame(VideoFrameJob(Path('bird.jpg'),settings,raw,{},photo_info=photo(raw),source_image=source))
        small = source.resize((size[0]//4,size[1]//4))
        preview = template.render_template_overlay(small,template_payload=payload,raw_metadata=raw,
            metadata_context={},photo_info=photo(raw),layout_size=size)
        expected = result.resize(preview.size,Image.Resampling.LANCZOS)
        assert max(ImageStat.Stat(ImageChops.difference(expected,preview)).mean)<1.5
        assert preview.tobytes()!=small.tobytes()
        hidden = template.render_template_overlay(source,template_payload=payload,raw_metadata=raw,
            metadata_context={},photo_info=photo(raw),draw_text=False)
        assert hidden.tobytes()==source.tobytes()
        for image in (result,small,preview,expected,hidden): image.close()


def test_template_workspace_and_cache_invalidation(tmp_path,isolated_config):
    item = new_item('badge'); item.update(badge_background='#abcdef',badge_radius=.42)
    payload = dict(overlays=[item],ratio='no_crop')
    path = tmp_path/'徽章模板.json'; template.save_template_payload(path,payload)
    loaded = template.load_template_payload(path)
    assert loaded['overlays']==document(payload)['overlays']
    workspace = tmp_path/'徽章工作区.json'
    write_workspace_json(workspace,dict(photos=[{'render_settings':{'overlay_override':loaded}}]))
    assert read_workspace_json(workspace)['photos'][0]['render_settings']['overlay_override']==loaded
    job = VideoFrameJob(Path('bird.jpg'),dict(template_payload=loaded),{}, {})
    before = source_frame_signature_for_job(job)
    palette = dict(RARITY_DEFAULT_OPTIONS,rarity_badge_epic_background='#123456')
    isolated_config.write_text(json.dumps(palette),encoding='utf-8')
    assert source_frame_signature_for_job(job)!=before
    before=source_frame_signature_for_job(job)
    loaded['overlays'][0]['badge_color_mode']='custom'
    assert source_frame_signature_for_job(job)!=before


def test_panel_add_fields_custom_switch_undo_and_lock():
    panel = OverlayPanel(); panel.set_document({'fields':[]},'photo:badge',following=True)
    try:
        action=next(a for a in panel.add_button.menu().actions() if 'Badge' in a.text())
        action.trigger()
        assert panel.selected()['type']=='badge'
        assert panel.selected()['text_source']=={'type':'auto','key':'gbif_rarity_100'}
        assert not panel.widgets['color'].isEnabled()
        mode=panel.widgets['badge_color_mode']
        mode.setCurrentIndex(1); mode.activated.emit(1)
        panel.widgets['badge_background'].set_value('#123456',emit=True)
        panel.widgets['color'].set_value('#ABCDEF',emit=True)
        assert panel.widgets['color'].isEnabled()
        mode.setCurrentIndex(0); mode.activated.emit(0)
        assert not panel.widgets['color'].isEnabled() and panel.selected()['color']=='#ABCDEF'
        panel.undo(); assert panel.selected()['badge_color_mode']=='custom'
        panel.redo(); assert panel.selected()['badge_color_mode']=='auto'
        index=next(i for i in range(panel.metadata.count()) if panel.metadata.itemData(i)==('auto','iso'))
        panel.metadata.setCurrentIndex(index); panel.metadata.activated.emit(index)
        assert panel.widgets['color'].isEnabled()
        assert panel.selected()['text_source']['key']=='iso'
        panel.duplicate(); assert panel.selected()['badge_background']=='#123456'
        panel.edit('locked',True); assert not panel.widgets['badge_color_mode'].isEnabled()
        panel.add('text'); assert panel.widgets['badge_color_mode'].isHidden()
    finally:
        panel.close(); panel.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def test_cli_metadata_badge(tmp_path,monkeypatch):
    from typer.testing import CliRunner
    from birdstamp import cli
    source=tmp_path/'bird.png'
    with Image.new('RGB',(800,450)) as image: image.save(source)
    path=tmp_path/'badge.json'
    template.save_template_payload(path,dict(overlays=[new_item('badge')],ratio='no_crop'))
    monkeypatch.setattr(cli,'extract_many_with_xmp_priority',lambda *a,**k:{source:{'SourceFile':str(source),'gbif_rarity_100':60}})
    output=tmp_path/'out'
    result=CliRunner().invoke(cli.app,['render',str(source),'--out',str(output),'--template',str(path),
        '--format','png','--no-draw-banner','--no-draw-images'])
    assert result.exit_code==0, result.output+str(result.exception)
    with Image.open(next(output.glob('*.png'))) as rendered:
        assert rendered.getbbox()
        assert any(b>100 and r>50 and g<100 for count,(r,g,b) in rendered.getcolors(rendered.width*rendered.height))


def test_template_manager_badge_save_and_scene(tmp_path,monkeypatch):
    from birdstamp.gui.editor_template_dialog import TemplateManagerDialog
    monkeypatch.setattr(TemplateManagerDialog,'_load_preview_source',lambda self:None)
    folder=tmp_path/'templates'; folder.mkdir()
    template.save_template_payload(folder/'test.json',template.default_template_payload())
    with Image.new('RGB',(800,450)) as source:
        dialog=TemplateManagerDialog(folder,source)
        try:
            dialog.overlay_panel.add('badge')
            saved=template.load_template_payload(folder/'test.json')
            assert saved['overlays'][-1]['type']=='badge'
            assert any(layer.item['type']=='badge' for layer in dialog.overlay_session.scene.layers)
            dialog.overlay_panel.edit('badge_color_mode','custom')
            dialog.overlay_panel.edit('badge_background','#0066CC')
            assert template.load_template_payload(folder/'test.json')['overlays'][-1]['badge_background']=='#0066CC'
        finally:
            dialog.close(); dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            _APP.processEvents()


# 复用已在构造前隔离配置、并等待关闭线程的主窗口 fixture。
from test_overlay_editor import window


def test_main_window_badge_activation_instance_and_restore(window,tmp_path):
    from birdstamp.gui.editor_utils import path_key
    settings=window._build_current_render_settings()
    settings.update(template_payload=dict(name='test',fields=[]),template_name='test',ratio='no_crop')
    window.template_paths={}
    path=tmp_path/'photo.png'
    with Image.new('RGB',(800,450),'#637080') as image:
        image.save(path)
        window._append_photo_path_to_list(path,existing_keys=set(),default_settings=settings)
        window._store_preview_image_cache(window._preview_image_cache_signature(path),image)
    item=window._find_photo_item_by_path(path)
    window.photo_list.blockSignals(True); window.photo_list.setCurrentItem(item); window.photo_list.blockSignals(False)
    window._on_photo_selected(item,None); window.render_preview()
    window.draw_text_check.setChecked(False)
    window.overlay_panel.add('badge')
    assert window.draw_text_check.isChecked()
    assert window.preview_label.canvas.edit_mode()=='overlay'
    assert window.photo_render_overrides[path_key(path)]['overlay_override']['overlays'][-1]['type']=='badge'
    assert any(layer.item['type']=='badge' for layer in window.preview_label.canvas.overlay_session.scene.layers)
    window._restore_overlay_template()
    assert window._overlay_override is None


def test_export_sidecar_priority_and_preview_does_not_read(monkeypatch):
    from birdstamp.gui import template_context as context
    raw={'gbif_rarity_100':90, 'report.gbif_rarity_100':90}
    info=photo(raw); info.metadata_is_snapshot=False
    monkeypatch.setattr(context,'_read_sidecar_metadata',lambda _: {'XMP-superpicky:gbif_rarity_100':60})
    item=new_item('badge')
    assert badge.badge_content(template,item,info,raw,badge.load_badge_palette())[0]=='史诗'
    def forbidden(*args,**kwargs):
        raise AssertionError('快照预览不得读取磁盘元数据')
    monkeypatch.setattr(context,'_read_sidecar_metadata',forbidden)
    monkeypatch.setattr(context,'_read_file_metadata_with_xmp_priority_cached',forbidden)
    assert content(item,{'XMP-superpicky:gbif_rarity_100':0})[0]=='普通'
