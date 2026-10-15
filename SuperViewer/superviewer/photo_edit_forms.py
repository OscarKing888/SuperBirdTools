"""Declarative Qt parameter forms for the shared PhotoCraft recipe schema."""
from __future__ import annotations
from copy import deepcopy
import json

try:
    from PyQt6.QtCore import pyqtSignal, QSignalBlocker, Qt
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QWidget, QFormLayout, QVBoxLayout, QHBoxLayout, QComboBox, QCheckBox, QLabel, QPushButton, QDoubleSpinBox
except ImportError:
    from PyQt5.QtCore import pyqtSignal, QSignalBlocker, Qt
    from PyQt5.QtGui import QColor
    from PyQt5.QtWidgets import QWidget, QFormLayout, QVBoxLayout, QHBoxLayout, QComboBox, QCheckBox, QLabel, QPushButton, QDoubleSpinBox

from app_common.collapsible_section import CollapsibleSection
from app_common.photo_edit.catalog import (CAMERA_GROUPS,COLOR_BANDS,HUE_BANDS,GRADE_WHEELS,VIGNETTE_STYLES,
    camera_defaults,normalize_camera,adjustment_defaults,normalize_adjustment)
from app_common.photo_edit.qt_controls import ScalarEditor,CurveEditor,ColorWheel
from .rarity_badge import ColorButton


def form_layout(widget):
    layout = QFormLayout(widget)
    layout.setContentsMargins(0,0,0,0)
    layout.setRowWrapPolicy(getattr(QFormLayout,"RowWrapPolicy",QFormLayout).WrapLongRows)
    layout.setFieldGrowthPolicy(getattr(QFormLayout,"FieldGrowthPolicy",QFormLayout).AllNonFixedFieldsGrow)
    return layout


class ParameterForm(QWidget):
    changed = pyqtSignal(object,bool)
    invalid = pyqtSignal(str)

    def __init__(self,values,normalizer,parent=None):
        super().__init__(parent)
        self.values = deepcopy(values)
        self.normalizer = normalizer
        self._bindings = []

    def _path(self,path):
        return path() if callable(path) else (path,) if isinstance(path,str) else path

    def get(self,path):
        value = self.values
        for key in self._path(path):
            value = value[key]
        return value

    def patch(self,path,value,commit=True):
        candidate = deepcopy(self.values)
        target = candidate
        parts = self._path(path)
        for key in parts[:-1]:
            target = target[key]
        target[parts[-1]] = value
        try:
            self.values = self.normalizer(candidate)
        except ValueError as exc:
            self.invalid.emit(str(exc))
            self.refresh()
            return
        self.refresh()
        self.changed.emit(deepcopy(self.values),commit)

    def set_values(self,values):
        self.values = self.normalizer(values)
        self.refresh()

    def refresh(self,*_):
        for update in self._bindings:
            update()

    def scalar(self,layout,label,path,minimum=-100,maximum=100,decimals=0):
        widget = ScalarEditor(minimum,maximum,decimals,self)
        widget.setAccessibleName(label)
        widget.spin.setAccessibleName(label+"数值")
        widget.slider.setAccessibleName(label+"滑块")
        widget.changed.connect(lambda value,commit:self.patch(path,value,commit))
        self._bindings.append(lambda:widget.set_value(self.get(path)))
        layout.addRow(label,widget)
        return widget

    def check(self,layout,label,path):
        widget = QCheckBox(label,self)
        widget.toggled.connect(lambda value:self.patch(path,value))
        def update():
            with QSignalBlocker(widget):
                widget.setChecked(bool(self.get(path)))
        self._bindings.append(update)
        layout.addRow(widget)
        return widget

    def choice(self,layout,label,choices,*,path=None):
        widget = QComboBox(self)
        for value,text in choices:
            widget.addItem(text,value)
        if path is not None:
            widget.currentIndexChanged.connect(lambda _:self.patch(path,widget.currentData()))
            def update():
                with QSignalBlocker(widget):
                    widget.setCurrentIndex(widget.findData(self.get(path)))
            self._bindings.append(update)
        else:
            widget.currentIndexChanged.connect(self.refresh)
        layout.addRow(label,widget)
        return widget

    def color(self,layout,label,path,*,fallback=None):
        button = ColorButton("#FFFFFF",lambda:None,self,title="选择"+label)
        def changed():
            color = QColor(button.color)
            self.patch(path,[color.redF(),color.greenF(),color.blueF()])
        button._changed = changed
        def update():
            value = self.get(path)
            button.setEnabled(value is not None)
            value = value if value is not None else fallback or [1,1,1]
            button.set_color(QColor.fromRgbF(*value).name().upper())
        self._bindings.append(update)
        layout.addRow(label,button)
        return button

    def curve(self,layout,path,*,camera=False):
        editor = CurveEditor(self)
        layout.addRow(editor)
        def update():
            values = self.get(path)
            points = [[a/255,b/255] for a,b in values] if camera else [[p["input"],p["output"]] for p in values]
            editor.set_points(points)
        self._bindings.append(update)
        def changed(points,commit):
            value = [[x*255,y*255] for x,y in points] if camera else [dict(input=x,output=y) for x,y in points]
            self.patch(path,value,commit)
        editor.changed.connect(changed)
        reset = QPushButton("重置当前曲线",self)
        reset.clicked.connect(lambda:self.patch(path,[] if camera else [dict(input=0,output=0),dict(input=1,output=1)]))
        layout.addRow(reset)
        return editor


class CameraRawForm(ParameterForm):
    def __init__(self,parent=None):
        super().__init__(camera_defaults(),normalize_camera,parent)
        self.groups = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        for index,(title,scalars) in enumerate(CAMERA_GROUPS):
            group = CollapsibleSection(title,expanded=index<2,parent=self)
            self.groups[title] = group
            body = form_layout(group.body)
            for scalar in scalars:
                self.scalar(body,scalar.label,scalar.key,scalar.minimum,scalar.maximum,scalar.decimals)
            if title=="效果":
                self.choice(body,"暗角样式",VIGNETTE_STYLES,path="vignetteStyle")
                seed = QDoubleSpinBox(self)
                seed.setDecimals(0)
                seed.setRange(0,2**32-1)
                seed.setKeyboardTracking(False)
                seed.valueChanged.connect(lambda value:self.patch("seed",int(value)))
                def update_seed():
                    with QSignalBlocker(seed):
                        seed.setValue(self.values["seed"])
                self._bindings.append(update_seed)
                body.addRow("颗粒种子",seed)
            elif title=="曲线":
                for i,label in enumerate(("阴影分区","中间分区","高光分区")):
                    self.scalar(body,label,("curveSplits",i),0,100)
                channel = self.choice(body,"点曲线通道",(("pointCurve","RGB"),("pointCurveRed","红"),
                    ("pointCurveGreen","绿"),("pointCurveBlue","蓝")))
                self.curve(body,lambda channel=channel:(channel.currentData(),),camera=True)
            elif title=="颜色混合器":
                channel = self.choice(body,"调整",(("hslHue","色相"),("hslSat","饱和度"),("hslLum","明亮度")))
                for i,label in enumerate(COLOR_BANDS):
                    self.scalar(body,label,lambda i=i,channel=channel:(channel.currentData(),i))
            elif title=="色彩分级":
                channel = self.choice(body,"范围",GRADE_WHEELS)
                wheel = ColorWheel(self)
                body.addRow(wheel)
                def pick(hue,sat,commit,channel=channel):
                    value = dict(self.get(channel.currentData()),hue=hue,sat=sat)
                    self.patch(channel.currentData(),value,commit)
                wheel.changed.connect(pick)
                self._bindings.append(lambda channel=channel,wheel=wheel:wheel.set_value(
                    self.get(channel.currentData())["hue"],self.get(channel.currentData())["sat"]))
                for key,label,lo,hi in (("hue","色相",0,360),("sat","饱和度",0,100),("lum","明亮度",-100,100)):
                    self.scalar(body,label,lambda key=key,channel=channel:(channel.currentData(),key),lo,hi,1)
            reset = QPushButton("重置本组",self)
            reset.clicked.connect(lambda _=False,title=title:self.reset_group(title))
            body.addRow(reset)
            layout.addWidget(group)
        layout.addStretch(1)
        self.refresh()

    def reset_group(self,title):
        defaults = camera_defaults()
        keys = [s.key for group,scalars in CAMERA_GROUPS if group==title for s in scalars]
        keys += {"效果":["vignetteStyle","seed"],"曲线":["curveSplits","pointCurve","pointCurveRed","pointCurveGreen","pointCurveBlue"],
                 "颜色混合器":["hslHue","hslSat","hslLum"],"色彩分级":[key for key,_ in GRADE_WHEELS]}.get(title,[])
        for key in keys:
            self.values[key] = defaults[key]
        self.refresh()
        self.changed.emit(deepcopy(self.values),True)


class AdjustmentForm(ParameterForm):
    lut_requested = pyqtSignal()

    def __init__(self,kind,parent=None):
        super().__init__(adjustment_defaults(kind),lambda value:normalize_adjustment(kind,value),parent)
        self.kind = kind
        body = form_layout(self)
        simple = {
            "brightnessContrast":(("brightness","亮度",-150,150,0),("contrast","对比度",-50,100,0)),
            "exposure":(("exposure","曝光",-20,20,2),("offset","位移",-.5,.5,3),("gamma","灰度系数",.01,9.99,2)),
            "vibrance":(("vibrance","自然饱和度",-100,100,0),("saturation","饱和度",-100,100,0)),
            "hueSaturation":(("hue","主色相",-180,360,0),("saturation","主饱和度",-100,100,0),("lightness","主明度",-100,100,0)),
            "posterize":(("levels","色阶数",2,255,0),),"threshold":(("level","阈值",0,255,0),),
        }
        for key,label,lo,hi,decimals in simple.get(kind,()):
            self.scalar(body,label,key,lo,hi,decimals)
        if kind=="brightnessContrast":
            self.check(body,"使用旧版算法","legacy")
        elif kind in {"levels","curves"}:
            channel = self.choice(body,"通道",(("master","RGB"),(0,"红"),(1,"绿"),(2,"蓝")))
            self.channel_selector = channel
            def prefix():
                return ("master",) if channel.currentData()=="master" else ("per_channel",channel.currentData())
            if kind=="curves":
                self.curve(body,prefix)
            else:
                for key,label,lo,hi,decimals in (("in_black","输入黑场",0,1,3),("in_white","输入白场",0,1,3),
                    ("gamma","中间调",.01,9.99,2),("out_black","输出黑场",0,1,3),("out_white","输出白场",0,1,3)):
                    self.scalar(body,label,lambda key=key:prefix()+(key,),lo,hi,decimals)
        elif kind=="hueSaturation":
            self.check(body,"着色","colorize")
            channel = self.choice(body,"独立色域",tuple(enumerate(HUE_BANDS)))
            for key,label,lo,hi in (("hue","色域色相",-180,180),("saturation","色域饱和度",-100,100),("lightness","色域明度",-100,100)):
                self.scalar(body,label,lambda key=key:("ranges",channel.currentData(),key),lo,hi)
            for i,label in enumerate(("渐入起点","完整起点","完整终点","渐出终点")):
                self.scalar(body,label,lambda i=i:("ranges",channel.currentData(),"bounds",i),-360,720)
        elif kind=="colorBalance":
            channel = self.choice(body,"范围",(("shadows","阴影"),("midtones","中间调"),("highlights","高光")))
            self.channel_selector = channel
            for i,label in enumerate(("青 ↔ 红","洋红 ↔ 绿","黄 ↔ 蓝")):
                self.scalar(body,label,lambda i=i:(channel.currentData(),i))
            self.check(body,"保留明度","preserve_luminosity")
        elif kind=="blackWhite":
            for i,label in enumerate(HUE_BANDS):
                self.scalar(body,label,("weights",i),-200,300)
            tint = QCheckBox("着色",self)
            tint.toggled.connect(lambda enabled:self.patch("tint",[.9,.75,.5] if enabled else None))
            def update_tint():
                with QSignalBlocker(tint):
                    tint.setChecked(self.values["tint"] is not None)
            self._bindings.append(update_tint)
            body.addRow(tint)
            self.color(body,"着色色彩","tint")
        elif kind=="photoFilter":
            self.color(body,"滤镜颜色","color")
            self.scalar(body,"浓度","density",0,1,2)
            self.check(body,"保留明度","preserve_luminosity")
        elif kind=="channelMixer":
            channel = self.choice(body,"输出通道",tuple(enumerate(("红","绿","蓝"))))
            for i,label in enumerate(("红","绿","蓝","常数")):
                self.scalar(body,label,lambda i=i:("matrix",channel.currentData(),i),-2,2,2)
            self.check(body,"单色","monochrome")
        elif kind=="colorLookup":
            from app_common.photo_edit.native import backend
            choices = [("","无")]
            try:
                looks = json.loads(backend().info_json())["looks"]
                choices += [(item[0],item[1]) for item in looks]
            except Exception as exc:
                note = QLabel(str(exc),self)
                note.setWordWrap(True)
                body.addRow(note)
            combo = self.choice(body,"内置外观",choices)
            combo.currentIndexChanged.disconnect(self.refresh)
            def choose():
                candidate = dict(self.values,builtin=combo.currentData(),asset=None)
                self.set_values(candidate)
                self.changed.emit(deepcopy(self.values),True)
            combo.currentIndexChanged.connect(choose)
            label = QLabel(self)
            label.setWordWrap(True)
            def update_lookup():
                with QSignalBlocker(combo):
                    combo.setCurrentIndex(max(0,combo.findData(self.values["builtin"])))
                ref = self.values["asset"]
                label.setText("外部 LUT："+ref["sha256"][:12] if ref else "")
            self._bindings.append(update_lookup)
            body.addRow(label)
            load = QPushButton("导入 LUT…",self)
            load.clicked.connect(self.lut_requested)
            body.addRow(load)
            self.check(body,"四面体插值","tetrahedral")
            self.check(body,"抖动减轻色带","dither")
        elif kind=="invert":
            note = QLabel("将各 RGB 通道反相，透明度保持不变。",self)
            note.setWordWrap(True)
            body.addRow(note)
        elif kind=="gradientMap":
            self._gradient(body)
        elif kind=="selectiveColor":
            channel = self.choice(body,"颜色",tuple(enumerate(("红","黄","绿","青","蓝","洋红","白","中性色","黑"))))
            for i,label in enumerate(("青色","洋红","黄色","黑色")):
                self.scalar(body,label,lambda i=i:("adjustments",channel.currentData(),i))
            self.check(body,"相对调整（关闭为绝对）","relative")
        self.refresh()

    def show_auto_values(self):
        """Expose the automatically estimated channel without editing the recipe."""
        if self.kind in {"levels", "curves", "colorBalance"}:
            value = "midtones" if self.kind == "colorBalance" else "master"
            self.channel_selector.setCurrentIndex(self.channel_selector.findData(value))
            self.refresh()

    def _gradient(self,body):
        stop = QComboBox(self)
        body.addRow("色标",stop)
        stop.currentIndexChanged.connect(self.refresh)
        def index():
            return max(0,min(len(self.values["stops"])-1,stop.currentIndex()))
        def update_stops():
            selected = index()
            with QSignalBlocker(stop):
                stop.clear()
                for i,(position,_) in enumerate(self.values["stops"]):
                    stop.addItem(f"{i+1} · {position:.0%}")
                stop.setCurrentIndex(selected)
        self._bindings.append(update_stops)
        self.scalar(body,"位置",lambda:("stops",index(),0),0,1,3)
        self.color(body,"色标颜色",lambda:("stops",index(),1))
        row = QHBoxLayout()
        add,remove = QPushButton("添加色标",self),QPushButton("删除色标",self)
        row.addWidget(add)
        row.addWidget(remove)
        body.addRow(row)
        def change(add_new):
            points = deepcopy(self.values["stops"])
            selected = index()
            if add_new:
                if len(points)>=64:
                    return
                gaps = [(points[i+1][0]-points[i][0],i) for i in range(len(points)-1)]
                gap,i = max(gaps)
                if gap<.002:
                    return
                left,right = points[i],points[i+1]
                points.insert(i+1,[(left[0]+right[0])/2,[(a+b)/2 for a,b in zip(left[1],right[1])]])
                selected = i+1
            elif len(points)>2:
                points.pop(selected)
            self.patch("stops",points)
            stop.setCurrentIndex(min(selected,len(points)-1))
        add.clicked.connect(lambda:change(True))
        remove.clicked.connect(lambda:change(False))
        self.check(body,"反向","reverse")
        self.check(body,"抖动减轻色带","dither")
