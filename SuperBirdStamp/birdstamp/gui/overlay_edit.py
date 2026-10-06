"""叠加层画布编辑：显示坐标与成片坐标隔离，手势只在松手时提交。"""
from __future__ import annotations

from dataclasses import replace
import math
from PyQt6.QtCore import QObject, QEvent, Qt, QPointF, QRectF
from PyQt6.QtGui import QColor, QPen, QPolygonF, QKeySequence
from .edit_modes import EditMode
from .editor_utils import pil_to_qpixmap
from . import editor_options
from birdstamp.overlays.render import Scene, compose_scene

EDIT_MODE_OVERLAY = 'overlay'


class OverlaySession(QObject):
    def __init__(self, canvas, panel, *, committed=lambda:None):
        super().__init__(canvas)
        self.canvas, self.panel = canvas, panel
        self.committed = committed
        panel.manual_geometry = lambda: self.selected_layer().manual_item(self.scene.size) if self.selected_layer() else None
        self.scene = None
        self.base = None
        self.crop = (0,0,1,1)
        self.postprocess = None
        self.drag = None
        self.space = False
        self.guides = []
        self._drawing = False
        self._original_pixmap = None
        canvas.overlay_session = self
        canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        canvas.installEventFilter(self)
        panel.selectionChanged.connect(lambda _id:canvas.update())
        canvas._edit_modes.register(OverlayEditMode())

    def capture(self, full_image, scene, crop=None, postprocess=None):
        self.cancel()
        self.clear()
        self.scene = scene
        self.base = full_image.copy()
        self.base.thumbnail((editor_options.OVERLAY_PREVIEW_MAX_EDGE,)*2)
        self.crop = crop or (0,0,1,1)
        self.postprocess = postprocess

    def clear(self):
        if self.scene: self.scene.close()
        if self.base: self.base.close()
        self.scene = self.base = None
        self.postprocess = None

    def active(self):
        return self.canvas.edit_mode()==EDIT_MODE_OVERLAY and self.scene is not None

    def target(self):
        rect=self.canvas.display_rect()
        if rect is None: return QRectF()
        l,t,r,b=self.crop
        return QRectF(rect.left()+l*rect.width(),rect.top()+t*rect.height(),(r-l)*rect.width(),(b-t)*rect.height())

    def to_widget(self,point):
        r=self.target()
        return QPointF(r.left()+point[0]*r.width()/self.scene.size[0],r.top()+point[1]*r.height()/self.scene.size[1])

    def to_scene(self,point):
        r=self.target()
        if r.width()<=0 or r.height()<=0: return (0,0)
        return ((point.x()-r.left())*self.scene.size[0]/r.width(),(point.y()-r.top())*self.scene.size[1]/r.height())

    def selected_layer(self):
        if self.drag: return self.drag['preview']
        return next((v for v in self.scene.layers if v.item['id']==self.panel.selected_id),None) if self.scene else None

    def handles(self,layer):
        points=[self.to_widget(p) for p in layer.corners()]
        top=(points[0]+points[1])/2
        center=self.to_widget(layer.center)
        vector=top-center
        length=max(.001,math.hypot(vector.x(),vector.y()))
        rotate=top+vector*(28/length)
        result={f'corner{i}':p for i,p in enumerate(points)}
        if layer.item['type']=='background':
            result.update({f'edge{i}':(points[i]+points[(i+1)%4])/2 for i in range(4)})
        result['rotate']=rotate
        return result

    def press(self,event):
        if not self.active() or self.space or event.button()!=Qt.MouseButton.LeftButton: return False
        self.panel.flush_text()
        self.canvas.setFocus(Qt.FocusReason.MouseFocusReason)
        layer=self.selected_layer(); handle=None
        if layer and not layer.item['locked']:
            distances=[(math.hypot(p.x()-event.position().x(),p.y()-event.position().y()),h) for h,p in self.handles(layer).items()]
            distance,hit=min(distances)
            if distance<=9: handle=hit
        point=self.to_scene(event.position())
        if handle is None:
            layer=next((v for v in reversed(self.scene.layers) if not v.item['locked'] and v.contains(point)),None)
            self.panel.select(layer.item['id'] if layer else '')
            handle='move'
        if layer:
            self._original_pixmap=self.canvas._source_pixmap
            self.drag=dict(original=layer,preview=layer,start=point,handle=handle,changed=False)
            self.canvas.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept(); return True

    def move(self,event):
        if not self.active() or not self.drag: return False
        point=self.to_scene(event.position()); d=self.drag; layer=d['original']; handle=d['handle']
        x,y=layer.center; sx,sy=d['start']; px,py=point
        self.guides=[]
        if handle=='move':
            center=[x+px-sx,y+py-sy]
            if not event.modifiers() & Qt.KeyboardModifier.AltModifier:
                r=self.target(); thresholds=(editor_options.OVERLAY_SNAP_DISTANCE*self.scene.size[0]/max(1,r.width()),editor_options.OVERLAY_SNAP_DISTANCE*self.scene.size[1]/max(1,r.height()))
                for axis in (0,1):
                    targets=[0,self.scene.size[axis]/2,self.scene.size[axis]]
                    for other in self.scene.layers:
                        if other is layer or other.item['type']=='background': continue
                        coords=[p[axis] for p in other.corners()]; targets.extend([min(coords),other.center[axis],max(coords)])
                    coords=[p[axis]+center[axis]-layer.center[axis] for p in layer.corners()]
                    anchors=[min(coords),center[axis],max(coords)]
                    delta,target=min(((target-a,target) for target in targets for a in anchors),key=lambda p:abs(p[0]))
                    if abs(delta)<=thresholds[axis]: center[axis]+=delta; self.guides.append((axis,target))
            preview=replace(layer,center=tuple(center))
        elif handle=='rotate':
            delta=math.degrees(math.atan2(py-y,px-x)-math.atan2(sy-y,sx-x))
            angle=(layer.rotation+delta)%360
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier: angle=round(angle/editor_options.OVERLAY_ROTATION_STEP)*editor_options.OVERLAY_ROTATION_STEP
            preview=replace(layer,rotation=angle)
        elif handle.startswith('edge'):
            angle=math.radians(-layer.rotation)
            dx,dy=(px-x)*math.cos(angle)-(py-y)*math.sin(angle),(px-x)*math.sin(angle)+(py-y)*math.cos(angle)
            w,h=layer.size
            if int(handle[-1])%2: w=max(1,abs(dx)*2)
            else: h=max(1,abs(dy)*2)
            preview=replace(layer,size=(w,h))
        else:
            index=int(handle[-1])
            corner=layer.corners()[index]
            anchor=layer.center if event.modifiers() & Qt.KeyboardModifier.AltModifier else layer.corners()[(index+2)%4]
            vx,vy=corner[0]-anchor[0],corner[1]-anchor[1]
            tx,ty=corner[0]+px-sx-anchor[0],corner[1]+py-sy-anchor[1]
            ratio=max(.01,min(100,(tx*vx+ty*vy)/max(.001,vx*vx+vy*vy)))
            center=(anchor[0]+(x-anchor[0])*ratio,anchor[1]+(y-anchor[1])*ratio)
            preview=replace(layer,center=center,size=(max(1,layer.size[0]*ratio),max(1,layer.size[1]*ratio)),
                            effective_scale=layer.effective_scale*ratio)
        d['preview']=preview; d['changed']=preview.center!=layer.center or preview.size!=layer.size or preview.rotation!=layer.rotation
        self.draw_preview(); event.accept(); return True

    def draw_preview(self):
        if self.base is None or self.scene is None: return
        scene=self.scene
        if self.drag:
            scene=Scene(scene.size,[self.drag['preview'] if v is self.drag['original'] else v for v in scene.layers])
        l,t,r,b=self.crop
        box=(round(l*self.base.width),round(t*self.base.height),round(r*self.base.width),round(b*self.base.height))
        region=self.base.crop(box)
        rendered=compose_scene(region,scene); region.close()
        result=self.base.copy(); result.paste(rendered,box[:2]); rendered.close()
        if self.postprocess:
            processed=self.postprocess(result)
            if processed is not result: result.close(); result=processed
        self._drawing=True
        try:
            self.canvas.set_source_pixmap(pil_to_qpixmap(result),preserve_view=True,preserve_scale=True,log_performance=False)
        finally:
            self._drawing=False; result.close()
        self.canvas.update()

    def release(self,event):
        if not self.drag or event.button()!=Qt.MouseButton.LeftButton: return False
        d=self.drag; self.drag=None; self.guides=[]; self.canvas.unsetCursor()
        if d['changed']:
            self.panel.replace_item(d['preview'].manual_item(self.scene.size))
            self.committed()
        else:
            self.cancel()
        self._original_pixmap=None; self.canvas.update(); event.accept(); return True

    def cancel(self):
        self.drag=None; self.guides=[]; self.canvas.unsetCursor()
        if self._original_pixmap is not None:
            self._drawing=True
            try: self.canvas.set_source_pixmap(self._original_pixmap,preserve_view=True,preserve_scale=True,log_performance=False)
            finally: self._drawing=False
        self._original_pixmap=None; self.canvas.update()

    def paint(self,painter):
        if not self.active(): return
        layer=self.selected_layer()
        if layer is None: return
        painter.save()
        pen=QPen(QColor('#2C91FF'),1.5); pen.setCosmetic(True); painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(QPolygonF([self.to_widget(p) for p in layer.corners()]))
        if not layer.item['locked']:
            handles=self.handles(layer)
            points=[self.to_widget(p) for p in layer.corners()]
            painter.drawLine((points[0]+points[1])/2,handles['rotate'])
            painter.setBrush(QColor('white'))
            for key,p in handles.items():
                if key=='rotate': painter.drawEllipse(p,5,5)
                else: painter.drawRect(QRectF(p.x()-4,p.y()-4,8,8))
        painter.setPen(QPen(QColor('#EF58D8'),1,Qt.PenStyle.DashLine))
        for axis,value in self.guides:
            start=(value,0) if axis==0 else (0,value)
            end=(value,self.scene.size[1]) if axis==0 else (self.scene.size[0],value)
            painter.drawLine(self.to_widget(start),self.to_widget(end))
        painter.restore()

    def eventFilter(self,watched,event):
        # Qt 销毁窗口时仍可能分发事件，此时 Python 循环引用已被清理。
        if not hasattr(self, 'canvas'):
            return False
        kind=event.type()
        if kind in (QEvent.Type.FocusOut,QEvent.Type.WindowDeactivate,QEvent.Type.Hide):
            if self.drag: self.cancel()
            self.space=False
        if not self.active(): return False
        if kind==QEvent.Type.MouseButtonDblClick:
            self.cancel()
            point=self.to_scene(event.position())
            layer=next((v for v in reversed(self.scene.layers) if not v.item['locked'] and v.contains(point)),None)
            if layer:
                self.panel.select(layer.item['id']); self.panel.focus_content()
                return True
        if kind==QEvent.Type.KeyRelease and event.key()==Qt.Key.Key_Space:
            self.space=False; return True
        if kind!=QEvent.Type.KeyPress: return False
        key=event.key()
        if key==Qt.Key.Key_Space: self.space=True; return True
        if key==Qt.Key.Key_Escape:
            if self.drag: self.cancel()
            else:
                self.panel.select(''); self.canvas.set_edit_mode('none')
                self.canvas.overlay_exit_requested.emit()
            return True
        if event.matches(QKeySequence.StandardKey.Undo): self.panel.undo(); return True
        if event.matches(QKeySequence.StandardKey.Redo): self.panel.redo(); return True
        if key in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace): self.panel.delete(); return True
        directions={Qt.Key.Key_Left:(-1,0),Qt.Key.Key_Right:(1,0),Qt.Key.Key_Up:(0,-1),Qt.Key.Key_Down:(0,1)}
        layer=self.selected_layer()
        if key in directions and layer and not layer.item['locked']:
            dx,dy=directions[key]; step=10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            item=replace(layer,center=(layer.center[0]+dx*step,layer.center[1]+dy*step)).manual_item(self.scene.size)
            self.panel.replace_item(item); self.committed(); return True
        return False


class OverlayEditMode(EditMode):
    mode_id=EDIT_MODE_OVERLAY
    label='编辑叠加层'
    def deactivate(self,canvas):
        session=getattr(canvas,'overlay_session',None)
        if session: session.cancel(); session.space=False
    def on_mouse_press(self,canvas,event):
        return bool(getattr(canvas,'overlay_session',None) and canvas.overlay_session.press(event))
    def on_mouse_move(self,canvas,event):
        return bool(getattr(canvas,'overlay_session',None) and canvas.overlay_session.move(event))
    def on_mouse_release(self,canvas,event):
        return bool(getattr(canvas,'overlay_session',None) and canvas.overlay_session.release(event))
    def paint(self,canvas,painter,draw_rect,content_rect):
        session=getattr(canvas,'overlay_session',None)
        if session: session.paint(painter)
