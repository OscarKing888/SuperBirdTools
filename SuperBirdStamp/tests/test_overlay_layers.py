"""真实图层像素、可移植工作区、实例优先级和旋转几何回归。"""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from PIL import Image, ImageChops, ImageStat
from birdstamp.overlays.model import document,new_item,with_document
from birdstamp.overlays.assets import import_image,decode_asset
from birdstamp.overlays.render import build_scene,compose_scene
from birdstamp.gui import editor_template as template
from birdstamp.export_stage import VideoFrameJob,render_video_frame,source_frame_signature_for_job
from birdstamp.export_stage.core import _resolve_template_payload_for_render
from birdstamp.gui.editor_renderer import _BirdStampRendererMixin
from birdstamp.workspace import read_workspace_json,write_workspace_json


@pytest.fixture
def overlay_doc(tmp_path):
    path=tmp_path/'中文透明素材.png'
    with Image.new('RGBA',(80,40),(255,0,0,180)) as im: im.save(path)
    key,asset=import_image(path)
    item=new_item('image'); item.update(asset_id=key,width=.4,rotation=30)
    text=new_item('text'); text.update(text='白鹭 Bird\n2026',font_size=80,y=.75,stroke_enabled=True,shadow_enabled=True)
    return dict(overlay_version=1,overlays=[item,text],overlay_assets={key:asset})


def test_new_normalization_roundtrip_and_legacy_unchanged(tmp_path,overlay_doc):
    legacy=template.default_template_payload()
    source=Image.new('RGB',(800,450),'#657080')
    original=template.render_template_overlay(source,raw_metadata={},metadata_context={},template_payload=legacy)
    normalized=template.normalize_template_payload(legacy,'default')
    again=template.render_template_overlay(source,raw_metadata={},metadata_context={},template_payload=normalized)
    assert original.tobytes()==again.tobytes()
    doc=document(legacy)
    assert all(i['layout_mode']=='auto' for i in doc['overlays'])
    assert doc==document(legacy)
    payload=with_document(legacy,overlay_doc)
    target=tmp_path/'模板.json'; template.save_template_payload(target,payload)
    loaded=template.load_template_payload(target)
    assert loaded['overlays']==document(overlay_doc)['overlays']
    assert 'fields' not in loaded and 'banner_color' not in loaded
    assert loaded['ratio']==legacy['ratio']
    for im in (source,original,again): im.close()


@pytest.mark.parametrize('size',[(1600,900),(900,1600)])
@pytest.mark.parametrize('rotation',[0,30,90,179])
@pytest.mark.parametrize('tint',[False,True])
def test_rotated_alpha_preview_matches_pipeline(overlay_doc,size,rotation,tint):
    overlay_doc['overlays'][0]['rotation']=rotation
    overlay_doc['overlays'][0].update(tint_enabled=tint,tint_color='#00CC77')
    settings=dict(template_payload=overlay_doc,ratio='no_crop',draw_banner=False)
    with Image.new('RGB',size,'#708090') as source:
        result=render_video_frame(VideoFrameJob(Path('bird.jpg'),settings,{}, {},source_image=source))
        small=source.resize((size[0]//4,size[1]//4))
        preview=template.render_template_overlay(small,template_payload=overlay_doc,raw_metadata={},metadata_context={},layout_size=size)
        expected=result.resize(preview.size,Image.Resampling.LANCZOS)
        assert max(ImageStat.Stat(ImageChops.difference(expected,preview)).mean)<1.5
        assert preview.tobytes()!=small.tobytes()
        result.close(); small.close(); preview.close(); expected.close()


def test_image_tint_preserves_alpha_original_asset_and_opacity(tmp_path):
    path=tmp_path/'多色透明图像.png'
    source_pixels=[(255,0,0,0),(255,255,255,64),(0,0,0,128),(0,0,255,255)]
    with Image.new('RGBA',(4,1)) as image:
        image.putdata(source_pixels); image.save(path)
    key,asset=import_image(path)
    item=new_item('image')
    item.update(asset_id=key,width=1,opacity=50,tint_enabled=True,tint_color='#33CC66')
    payload=dict(overlays=[item],overlay_assets={key:asset})
    scene=build_scene(payload,(4,1))
    try:
        assert [scene.layers[0].pixels.getpixel((x,0)) for x in range(4)]==[(51,204,102,p[3]) for p in source_pixels]
        with Image.new('RGBA',(4,1)) as base:
            result=compose_scene(base,scene)
        try:
            assert [result.getpixel((x,0))[3] for x in range(4)]==[0,32,64,128]
        finally: result.close()
    finally: scene.close()
    with decode_asset(key,payload['overlay_assets']) as original:
        assert [original.getpixel((x,0)) for x in range(4)]==source_pixels
    item['tint_enabled']=False
    scene=build_scene(payload,(4,1))
    try:
        assert [scene.layers[0].pixels.getpixel((x,0)) for x in range(4)]==source_pixels
    finally: scene.close()


def test_image_tint_defaults_persistence_and_cache_signature(overlay_doc,tmp_path):
    old=deepcopy(overlay_doc)
    old['overlays'][0].pop('tint_enabled',None); old['overlays'][0].pop('tint_color',None)
    assert not document(old)['overlays'][0]['tint_enabled']
    item=overlay_doc['overlays'][0]
    item.update(tint_enabled='true',tint_color='bad-color')
    assert document(overlay_doc)['overlays'][0]['tint_color']=='#FFFFFF'
    item.update(tint_enabled=True,tint_color='#12ab34')
    path=tmp_path/'彩色模板.json'; template.save_template_payload(path,overlay_doc)
    saved=template.load_template_payload(path)
    assert saved['overlays'][0]['tint_enabled'] is True
    assert saved['overlays'][0]['tint_color']=='#12AB34'
    workspace=tmp_path/'颜色工作区.json'
    write_workspace_json(workspace,dict(photos=[{'render_settings':{'overlay_override':saved}}]))
    restored=read_workspace_json(workspace)['photos'][0]['render_settings']['overlay_override']
    assert restored==saved
    job=VideoFrameJob(Path('bird.jpg'),dict(template_payload=old,overlay_override=restored),{}, {})
    colored=source_frame_signature_for_job(job)
    restored['overlays'][0]['tint_color']='#FF0000'
    recolored=source_frame_signature_for_job(job)
    restored['overlays'][0]['tint_enabled']=False
    assert len({colored,recolored,source_frame_signature_for_job(job)})==3


def test_scene_hits_rotated_geometry_and_manual_conversion(overlay_doc):
    scene=build_scene(overlay_doc,(1000,500))
    try:
        layer=scene.layers[0]
        assert layer.contains(layer.center)
        corner=layer.corners()[0]
        assert not layer.contains((corner[0]-50,corner[1]-50))
        item=layer.manual_item(scene.size)
        assert item['x']==pytest.approx(.5)
        assert item['width']==pytest.approx(.4)
        assert item['rotation']==30
    finally: scene.close()


def test_auto_text_to_manual_preserves_geometry_and_effects():
    item=new_item('text',metadata=True)
    item.update(text_mode='literal',text='中文 Bird',font_size=72,align_horizontal='right',
                align_vertical='bottom',stroke_enabled=True,stroke_width=3,shadow_enabled=True)
    payload=dict(overlay_version=1,overlays=[item])
    automatic=build_scene(payload,(1600,900))
    manual=build_scene(dict(payload,overlays=[automatic.layers[0].manual_item(automatic.size)]),(1600,900))
    try:
        assert manual.layers[0].center==pytest.approx(automatic.layers[0].center)
        assert manual.layers[0].size==automatic.layers[0].size
        assert manual.layers[0].pixels.tobytes()==automatic.layers[0].pixels.tobytes()
    finally: automatic.close(); manual.close()


def test_asset_portable_deduplicated_workspace_and_corruption(tmp_path,overlay_doc):
    payload=dict(photos=[{'render_settings':{'overlay_override':deepcopy(overlay_doc)}} for _ in range(5)])
    path=tmp_path/'工作区.json'; write_workspace_json(path,payload)
    text=path.read_text(encoding='utf8')
    data=next(iter(overlay_doc['overlay_assets'].values()))['data']
    assert text.count(data)==1
    restored=read_workspace_json(path)
    assert restored['photos']==payload['photos']
    key=next(iter(overlay_doc['overlay_assets']))
    (tmp_path/'中文透明素材.png').unlink()
    im=decode_asset(key,restored['photos'][0]['render_settings']['overlay_override']['overlay_assets'])
    assert im.mode=='RGBA'; im.close()
    bad=deepcopy(overlay_doc); bad['overlay_assets'][key]['data']='YWJj'
    with pytest.raises(ValueError,match='损坏'): build_scene(bad,(800,450))


def test_instance_snapshot_wins_over_template_reload(tmp_path,overlay_doc):
    path=tmp_path/'default.json'; template.save_template_payload(path,template.default_template_payload())
    settings={'template_name':'default','overlay_override':deepcopy(overlay_doc)}
    renderer=_BirdStampRendererMixin(); renderer.template_paths={'default':path}
    for resolve in (lambda:_resolve_template_payload_for_render(settings,renderer.template_paths),
                    lambda:renderer._resolve_template_payload_for_render(settings)):
        assert resolve()['overlays']==document(overlay_doc)['overlays']
    updated=template.default_template_payload(); updated['fields'][0]['font_size']=100
    template.save_template_payload(path,updated)
    assert renderer._resolve_template_payload_for_render(settings)['overlays']==document(overlay_doc)['overlays']
    settings['overlay_override']=None
    assert renderer._resolve_template_payload_for_render(settings)['fields'][0]['font_size']==100
    fallback=dict(template_payload=overlay_doc,overlay_override=overlay_doc)
    assert renderer._normalize_render_settings({},fallback)['overlay_override'] is None


def test_visibility_draw_images_and_cache_identity(overlay_doc):
    doc=deepcopy(overlay_doc); doc['overlays']=doc['overlays'][:1]
    with Image.new('RGB',(800,450)) as source:
        visible=template.render_template_overlay(source,template_payload=doc,raw_metadata={},metadata_context={},draw_text=False,draw_banner=False)
        hidden=template.render_template_overlay(source,template_payload=doc,raw_metadata={},metadata_context={},draw_images=False)
        assert visible.getbbox() and hidden.getbbox() is None
        visible.close(); hidden.close()
    job=VideoFrameJob(Path('photo.jpg'),dict(template_payload=doc),{}, {})
    first=source_frame_signature_for_job(job)
    job.settings['overlay_override']=deepcopy(doc); job.settings['overlay_override']['overlays'][0]['rotation']=180
    assert source_frame_signature_for_job(job)!=first


@pytest.mark.parametrize('order', [('template_crop','resize_limit','template_overlay'),('template_crop','template_overlay','resize_limit')])
def test_crop_aligned_overlay_preview_matches_export(overlay_doc,order):
    renderer=_BirdStampRendererMixin(); renderer.template_paths={}
    renderer.current_photo_info=None; renderer.current_metadata_context={}
    settings=dict(template_payload=overlay_doc,ratio='free',center_mode='custom',crop_box=(.2,.1,.8,.9),max_long_edge=1000,pipeline_stage_order=order)
    with Image.new('RGB',(2000,1200),'#576677') as source:
        renderer.current_source_image=source.resize((1000,600))
        exported=render_video_frame(VideoFrameJob(Path('bird.jpg'),settings,{}, {},source_image=source))
        preview=renderer._render_preview_pipeline_image(renderer.current_source_image.copy(),{},
            settings=settings,source_image=renderer.current_source_image,crop_box=(.2,.1,.8,.9),
            outer_pad=(0,0,0,0),crop_output_size=(1200,960))
        region=preview.crop((200,60,800,540))
        expected=exported.resize(region.size,Image.Resampling.LANCZOS)
        assert max(ImageStat.Stat(ImageChops.difference(expected,region)).mean)<1.5
        for im in (exported,preview,region,expected,renderer.current_source_image): im.close()


@pytest.mark.parametrize('tint',[False,True])
def test_image_only_cli_overlay(overlay_doc,tmp_path,monkeypatch,tint):
    from typer.testing import CliRunner
    from birdstamp import cli,config
    monkeypatch.setattr(config,'get_user_data_dir',lambda:tmp_path/'user')
    source=tmp_path/'source.png'; Image.new('RGB',(800,450)).save(source)
    overlay_doc['overlays']=overlay_doc['overlays'][:1]
    overlay_doc['overlays'][0].update(tint_enabled=tint,tint_color='#00FF00')
    path=tmp_path/'template.json'; template.save_template_payload(path,dict(overlay_doc,ratio='no_crop'))
    monkeypatch.setattr(cli,'extract_many_with_xmp_priority',lambda *a,**k:{source:{'SourceFile':str(source)}})
    for enabled in (True,False):
        output=tmp_path/str(enabled)
        args=['render',str(source),'--out',str(output),'--template',str(path),'--format','png','--no-draw-text','--no-draw-banner']
        if not enabled: args.append('--no-draw-images')
        result=CliRunner().invoke(cli.app,args)
        assert result.exit_code==0, result.output+str(result.exception)
        with Image.open(next(output.glob('*.png'))) as rendered:
            assert bool(rendered.getbbox())==enabled
            if enabled:
                r,g,b=rendered.getpixel((400,225))[:3]
                assert (g>0 and r==0) if tint else (r>0 and g==0)
