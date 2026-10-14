"""Editing-to-preview adapter. Each A/B viewport keeps its own mode and request."""
from __future__ import annotations
import numpy as np
try:
    from PyQt6.QtCore import QObject,QTimer
    from PyQt6.QtGui import QImage
except ImportError:
    from PyQt5.QtCore import QObject,QTimer
    from PyQt5.QtGui import QImage

from app_common.photo_edit.pipeline import render_recipe
from app_common.video import is_video
from .photo_edit_controller import source_key


def render_for_viewer(snapshot,**kwargs):
    result = render_recipe(snapshot,**kwargs)
    pixels = np.ascontiguousarray(np.rint(result.rgba*255).astype(np.uint8))
    height,width = pixels.shape[:2]
    format = getattr(QImage,"Format",QImage).Format_RGBA8888
    result.display_image = QImage(pixels.data,width,height,pixels.strides[0],format).copy()
    return result


class PhotoEditPreviewBridge(QObject):
    def __init__(self,owner,controller,ab,panels):
        super().__init__(owner)
        self.controller,self.ab,self.panels = controller,ab,panels
        self._requests = {}
        self._descriptions = {}
        self._full_timers = {}
        self._suspended = False
        for viewport in (ab.a_panel,ab.b_panel):
            preview = viewport.preview
            preview.edit_preview_requested.connect(lambda token,path,preview=preview:self._request(preview,token,path))
            preview.source_changed.connect(lambda preview=preview:self._source_changed(preview))
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(450)
            timer.timeout.connect(lambda preview=preview:self._request_full(preview))
            self._full_timers[preview] = timer
        controller.state_changed.connect(self._state_changed)
        controller.render_ready.connect(self._ready)
        controller.render_failed.connect(self._failed)
        for panel in panels:
            panel.editing_started.connect(self.activate_editing)

    def activate_editing(self,source):
        preview = self.ab.active_panel()
        if source_key(source)==source_key(preview.source_identity_path()):
            preview.set_preview_source_mode("edited")

    def sync_target(self,source):
        if source_key(source)!=source_key(self.controller.current_source):
            self.controller.finish_gesture()
        name = self.ab.active_viewport().name if self.ab.enabled.isChecked() else ""
        for panel in self.panels:
            panel.set_target_name(name)
        # Hidden tabs do not cause XMP reads; edited viewports can load independently.
        self.controller.current_source = source if source and not is_video(source) else ""

    def set_suspended(self,value):
        self._suspended = bool(value)
        self.controller.set_suspended(value)
        if value:
            for timer in self._full_timers.values():
                timer.stop()
            self._requests.clear()
            self._descriptions.clear()

    def _source_changed(self,preview):
        if preview.preview_source_mode()!="edited" or not preview.source_identity_path() or is_video(preview.source_identity_path()):
            self.controller.cancel_render(preview)
            self._full_timers[preview].stop()
            self._requests.pop(preview,None)
            self._descriptions.pop(preview,None)

    def _description(self,preview):
        session = self.controller.session(preview.source_identity_path())
        if session is None:
            return None
        return (source_key(session.state.source),session.state.active,
                session.state.current.recipe.signature(),preview.edit_before())

    def _state_changed(self,source):
        if self._suspended:
            return
        for preview in self._full_timers:
            if preview.preview_source_mode()=="edited" and source_key(preview.source_identity_path())==source_key(source):
                description = self._description(preview)
                if description!=self._descriptions.get(preview):
                    self._descriptions[preview] = description
                    preview.refresh_edited_preview()

    def _request(self,preview,token,path,*,full=False):
        if self._suspended or is_video(path) or preview.preview_source_mode()!="edited":
            return
        self._full_timers[preview].stop()
        request_id = self.controller.request_render(path,preview,before=preview.edit_before(),full=full)
        self._requests[preview] = (request_id,token,path,full)
        self._descriptions[preview] = self._description(preview)

    def _ready(self,source,consumer,request_id,result):
        request = self._requests.get(consumer)
        if self._suspended or not request or request[0]!=request_id:
            return
        _,token,path,full = request
        accepted = consumer.apply_edited_preview(token,path,result.display_image,
            full=full or not result.approximate,camera_crop=result.camera_crop)
        if accepted and not full and result.approximate:
            self._full_timers[consumer].start()

    def _request_full(self,preview):
        request = self._requests.get(preview)
        if request and not self._suspended:
            self._request(preview,request[1],request[2],full=True)

    def _failed(self,source,consumer,request_id,error):
        request = self._requests.get(consumer)
        if request and request[0]==request_id:
            consumer.set_edit_preview_error(request[1],request[2],error)
