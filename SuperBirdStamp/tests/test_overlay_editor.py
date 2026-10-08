"""隔离配置的模板/照片编辑和画布手势测试。"""
from copy import deepcopy
from pathlib import Path
import pytest
from PIL import Image
from PyQt6.QtCore import Qt,QPointF,QEvent,QCoreApplication
from PyQt6.QtGui import QMouseEvent,QKeyEvent
from PyQt6.QtTest import QTest
from test_template_text_scale import _APP
from test_overlay_layers import overlay_doc
from birdstamp.gui.overlay_panel import OverlayPanel
from birdstamp.gui.overlay_edit import OverlaySession
from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas
from birdstamp.gui.editor import BirdStampEditorWindow
from birdstamp.gui.editor_template_dialog import TemplateManagerDialog
from birdstamp.gui.editor_template import save_template_payload,default_template_payload,load_template_payload
from birdstamp.gui.editor_utils import pil_to_qpixmap,path_key
from birdstamp.overlays.render import build_scene
from birdstamp import config


@pytest.fixture
def window(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'get_user_data_dir',lambda:tmp_path/'user')
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'cache'))
    for name in ('_start_bird_detector_preload','_run_deferred_startup_tasks',
                 '_restart_photo_list_metadata_loader','_schedule_async_bird_detect'):
        monkeypatch.setattr(BirdStampEditorWindow,name,lambda *args,**kwargs:None)
    instance=BirdStampEditorWindow()
    try:
        yield instance
    finally:
        # closeEvent 等后台线程退出后才保存；隔离路径须维持到真正关闭。
        closed=instance.close()
        for _ in range(500):
            if closed: break
            QTest.qWait(10)
            closed=instance.close()
        assert closed, '测试窗口后台任务未能退出'
        instance.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        _APP.processEvents()


def mouse(kind,point,button=Qt.MouseButton.LeftButton,modifiers=Qt.KeyboardModifier.NoModifier):
    return QMouseEvent(kind,point,point,button,button,modifiers)


def test_panel_crud_history_and_context(overlay_doc):
    panel=OverlayPanel(); panel.set_document(overlay_doc,'photo:one',following=True)
    panel.select(overlay_doc['overlays'][1]['id']); panel.edit('text','中文自定义')
    assert not panel.following and panel.selected()['text']=='中文自定义'
    panel.undo(); assert panel.following
    panel.redo(); assert panel.selected()['text']=='中文自定义'
    panel.duplicate(); assert len(panel.doc['overlays'])==3
    panel.delete(); assert len(panel.doc['overlays'])==2
    panel.set_document(overlay_doc,'photo:two',following=True); panel.undo()
    assert panel.following
    panel.close(); panel.deleteLater()


def test_image_color_controls_undo_lock_and_type_visibility(overlay_doc,tmp_path,monkeypatch):
    monkeypatch.setattr(config,'get_user_data_dir',lambda:tmp_path/'user')
    panel=OverlayPanel(); panel.set_document(overlay_doc,'photo:color',following=True)
    try:
        panel.select(overlay_doc['overlays'][0]['id'])
        enabled=panel.widgets['tint_enabled']; color=panel.widgets['tint_color']
        assert not enabled.isHidden() and not color.isEnabled()
        enabled.setChecked(True)
        color.set_value('#00CC77',emit=True)
        assert color.isEnabled() and not panel.following
        assert panel.selected()['tint_color']=='#00CC77'
        panel.undo(); assert panel.selected()['tint_color']=='#FFFFFF'
        panel.undo(); assert not panel.selected()['tint_enabled'] and panel.following
        panel.redo(); panel.redo()
        enabled.setChecked(False)
        assert not color.isEnabled() and panel.selected()['tint_color']=='#00CC77'
        enabled.setChecked(True); panel.edit('locked',True)
        assert not enabled.isEnabled() and not color.isEnabled()
        panel.select(overlay_doc['overlays'][1]['id'])
        assert enabled.isHidden() and color.isHidden()
    finally:
        panel.close(); panel.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def test_pending_text_survives_list_selection_and_new_layer(overlay_doc):
    panel=OverlayPanel(); panel.set_document(overlay_doc,'photo:one')
    text_id=overlay_doc['overlays'][1]['id']
    panel.select(text_id)
    panel.text.setPlainText('切换前输入的中文')
    panel.list.setCurrentRow(1)
    assert panel.selected_id==overlay_doc['overlays'][0]['id']
    assert next(i for i in panel.doc['overlays'] if i['id']==text_id)['text']=='切换前输入的中文'
    panel.select(text_id); panel.text.setPlainText('新增前输入的中文')
    panel.add('text')
    assert next(i for i in panel.doc['overlays'] if i['id']==text_id)['text']=='新增前输入的中文'
    panel.text.setPlainText('尚未等待防抖')
    panel.undo()
    assert panel.selected()['text']=='自定义文本'
    panel.close(); panel.deleteLater()


def test_canvas_drag_rotate_cancel_and_scale(overlay_doc):
    panel=OverlayPanel(); panel.set_document(overlay_doc,'photo:one')
    canvas=EditorPreviewCanvas(); canvas.resize(800,450)
    image=Image.new('RGB',(800,450),'#657080')
    session=OverlaySession(canvas,panel)
    scene=build_scene(overlay_doc,image.size)
    canvas.set_source_pixmap(pil_to_qpixmap(image)); session.capture(image,scene)
    canvas.set_edit_mode('overlay')
    panel.select(scene.layers[0].item['id'])
    initial=deepcopy(panel.doc)
    start=session.to_widget(scene.layers[0].center)
    assert session.press(mouse(QEvent.Type.MouseButtonPress,start))
    assert session.move(mouse(QEvent.Type.MouseMove,start+QPointF(35,20),modifiers=Qt.KeyboardModifier.AltModifier))
    assert panel.doc==initial  # 手势中不改持久配置。
    session.release(mouse(QEvent.Type.MouseButtonRelease,start+QPointF(35,20)))
    assert panel.selected()['x']>initial['overlays'][0]['x']
    panel.undo(); assert panel.doc==initial
    start=session.handles(scene.layers[0])['rotate']
    session.press(mouse(QEvent.Type.MouseButtonPress,start))
    session.move(mouse(QEvent.Type.MouseMove,start+QPointF(70,60)))
    session.cancel(); assert panel.doc==initial
    corner=session.handles(scene.layers[0])['corner2']
    session.press(mouse(QEvent.Type.MouseButtonPress,corner))
    session.move(mouse(QEvent.Type.MouseMove,corner+QPointF(80,80)))
    session.release(mouse(QEvent.Type.MouseButtonRelease,corner+QPointF(80,80)))
    assert panel.selected()['width']>initial['overlays'][0]['width']
    session.clear(); canvas.close(); panel.close(); image.close()
    # 在 QApplication 仍存活时释放事件过滤器，避免循环引用拖到解释器退出。
    canvas.deleteLater(); panel.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def test_template_manager_new_layers_save_roundtrip(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'get_user_data_dir',lambda:tmp_path/'user')
    monkeypatch.setattr(TemplateManagerDialog,'_load_preview_source',lambda self:None)
    folder=tmp_path/'templates'; folder.mkdir()
    save_template_payload(folder/'test.json',default_template_payload())
    dlg=TemplateManagerDialog(folder,Image.new('RGB',(800,450)))
    dlg.overlay_panel.add('text')
    dlg.overlay_panel.edit('text','自定义中文\n第二行')
    assert dlg.overlay_edit_check.isChecked()
    payload=load_template_payload(folder/'test.json')
    assert payload['overlays'][-1]['text']=='自定义中文\n第二行'
    # 保存仍同步，预览会合并连续参数修改。
    dlg._refresh_preview()
    assert dlg.overlay_session.scene is not None
    dlg.overlay_panel.undo()
    assert load_template_payload(folder/'test.json')['overlays'][-1]['text']=='自定义文本'
    dlg.overlay_edit_check.setChecked(False)
    dlg.close(); dlg.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    _APP.processEvents()


def test_photo_instance_switch_and_batch(window,overlay_doc,tmp_path):
    settings=window._build_current_render_settings()
    settings.update(template_payload=dict(name='test',fields=[]),template_name='test',ratio='no_crop')
    window.template_paths={}
    paths=[tmp_path/'one.png',tmp_path/'two.png']
    for path in paths:
        image=Image.new('RGB',(800,450),'#637080'); image.save(path)
        window._append_photo_path_to_list(path,existing_keys=set(),default_settings=settings)
        window._store_preview_image_cache(window._preview_image_cache_signature(path),image)
    def select(path):
        item=window._find_photo_item_by_path(path)
        window.photo_list.blockSignals(True); window.photo_list.setCurrentItem(item); window.photo_list.blockSignals(False)
        window._on_photo_selected(item,None); window.render_preview()
    select(paths[0]); window.overlay_panel.commit(overlay_doc)
    assert window.photo_render_overrides[path_key(paths[0])]['overlay_override'] is not None
    window._activate_overlay_edit()
    assert window.preview_label.canvas.edit_mode()=='overlay'
    assert window.preview_label.canvas.overlay_session.scene is not None
    select(paths[1]); assert window._overlay_override is None
    select(paths[0]); assert window._overlay_override is not None
    window._apply_overlays(True)
    select(paths[1]); assert window._overlay_override is not None
    window._restore_overlay_template(); assert window._overlay_override is None
