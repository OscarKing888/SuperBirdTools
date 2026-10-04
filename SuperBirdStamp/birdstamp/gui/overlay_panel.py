"""模板和逐照片实例共用的叠加层列表、属性和有界撤销历史。"""
from __future__ import annotations

from copy import deepcopy
from collections import OrderedDict
from pathlib import Path
import uuid
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QRunnable, QThreadPool, pyqtSlot, QTimer
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QListWidget,QListWidgetItem,
    QAbstractItemView,QPushButton,QToolButton,QMenu,QLabel,QLineEdit,QPlainTextEdit,QComboBox,
    QCheckBox,QDoubleSpinBox,QFileDialog,QMessageBox,QGroupBox,QTabWidget,QSizePolicy,QScrollArea)
from birdstamp.overlays.model import document, new_item
from birdstamp.overlays.assets import import_image
from birdstamp.render.text_effects import DEFAULT_TEXT_EFFECTS, TEXT_EFFECT_RANGES
from .color_editor import ColorEditor
from .editor_utils import get_template_context_field_options, template_font_choices


class _ImportSignals(QObject):
    done = pyqtSignal(str, object, object)


class _Import(QRunnable):
    def __init__(self, context, path, replace_id=None):
        super().__init__()
        self.context, self.path = context, path
        self.replace_id=replace_id
        self.signals = _ImportSignals()

    def run(self):
        try:
            result, error = import_image(self.path), None
        except Exception as exc:
            result, error = None, str(exc)
        self.signals.done.emit(self.context, result, error)


class OverlayPanel(QWidget):
    changed = pyqtSignal(object)
    selectionChanged = pyqtSignal(str)
    activateRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = document({'fields': []})
        self.context = ''
        self.following = False
        self.selected_id = ''
        self._updating = False
        self._history = OrderedDict()
        self._jobs = []
        self._context_generation = 0
        self._text_timer = QTimer(self)
        self._text_timer.setSingleShot(True)
        self._text_timer.setInterval(350)
        self._text_timer.timeout.connect(lambda: self.edit('text', self.text.toPlainText()))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.scope = QLabel('叠加层')
        self.scope.setWordWrap(True)
        layout.addWidget(self.scope)
        row = QHBoxLayout()
        self.add_button = QToolButton()
        self.add_button.setText('新增叠加层')
        self.add_button.setMinimumWidth(105)
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self)
        menu.addAction('自定义文本', lambda: self.add('text'))
        menu.addAction('元数据文本', lambda: self.add('text', metadata=True))
        menu.addAction('图像…', self.import_file)
        menu.addAction('背景', lambda: self.add('background'))
        self.add_button.setMenu(menu)
        row.addWidget(self.add_button)
        for label, slot in [('复制',self.duplicate),('删除',self.delete)]:
            button=QPushButton(label); button.clicked.connect(slot); row.addWidget(button)
            if label=='删除': self.delete_button=button
            else: self.duplicate_button=button
        layout.addLayout(row)
        self.list = QListWidget()
        self.list.setMinimumHeight(105)
        self.list.setMaximumHeight(140)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemChanged.connect(self._visibility_changed)
        self.list.model().rowsMoved.connect(self._reorder)
        layout.addWidget(self.list)
        row=QHBoxLayout()
        self.undo_button=QPushButton('撤销'); self.undo_button.clicked.connect(self.undo)
        self.redo_button=QPushButton('重做'); self.redo_button.clicked.connect(self.redo)
        row.addWidget(self.undo_button); row.addWidget(self.redo_button)
        self.edit_button=QPushButton('在预览中编辑'); self.edit_button.clicked.connect(self.activateRequested)
        row.addWidget(self.edit_button)
        layout.addLayout(row)
        self.properties=QGroupBox('叠加层属性')
        self.form=QFormLayout(self.properties)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addWidget(self.properties)
        self.widgets={}
        self.kinds={}
        self._line('name','名称')
        self._check('visible','显示')
        self._check('locked','锁定')
        self._combo('layout_mode','布局',[('自动布局','auto'),('手动位置','manual')])
        self._combo('align_horizontal','水平对齐', [('左','left'),('居中','center'),('右','right')])
        self._combo('align_vertical','垂直对齐', [('上','top'),('居中','center'),('下','bottom')])
        for key,label in [('x_offset_pct','X 偏移 %'),('y_offset_pct','Y 偏移 %')]:
            self._spin(key,label,-100,100)
        for key,label in [('x','中心 X %'),('y','中心 Y %')]:
            self._spin(key,label,-10000,10000,factor=100)
        self._spin('scale','缩放 %',.1,10000,factor=100)
        self._spin('width','宽度 %',.1,1000,factor=100,kinds=('image','background'))
        self._spin('height','高度 %',.1,1000,factor=100,kinds=('background',))
        self._spin('rotation','旋转 °',-360,360)
        self._spin('opacity','不透明度 %',0,100)
        reset=QPushButton('恢复自动布局'); reset.clicked.connect(lambda:self.edit('layout_mode','auto'))
        self.form.addRow(reset)
        self.reset_layout_button=reset
        self._combo('text_mode','文字来源',[('自定义文本','literal'),('元数据字段','metadata')],kinds=('text',))
        self.text=QPlainTextEdit(); self.text.setMaximumHeight(100)
        self.text.textChanged.connect(lambda:self._text_timer.start() if not self._updating else None)
        self._row('text','文本',self.text,('text',))
        self.metadata=QComboBox(); self.metadata.setEditable(True)
        for source,key,label in get_template_context_field_options():
            self.metadata.addItem(f'{label} ({key})',(source,key))
        self.metadata.activated.connect(self._metadata_selected)
        self.metadata.lineEdit().editingFinished.connect(self._metadata_typed)
        self._row('text_source','字段/占位符',self.metadata,('text',))
        self._spin('font_size','基础字号',8,300,kinds=('text',))
        self.font=QComboBox(); self.font.addItem('自动（系统默认）','auto')
        self.font.activated.connect(lambda i:self.edit('font_type',self.font.itemData(i)))
        self._row('font_type','字体',self.font,('text',))
        choose=QPushButton('加载字体列表'); choose.clicked.connect(self._load_fonts)
        self.form.addRow(choose); self.font_button=choose
        self._combo('style','样式',[('常规','normal'),('粗体','bold'),('斜体','italic'),('粗斜体','bold_italic')],kinds=('text',))
        self._color('color','文字颜色',('text',))
        for key,value in DEFAULT_TEXT_EFFECTS.items():
            labels={'stroke_enabled':'描边','stroke_color':'描边颜色','stroke_width':'描边宽度',
                    'shadow_enabled':'阴影','shadow_color':'阴影颜色','shadow_opacity':'阴影不透明度 %',
                    'shadow_offset_x':'阴影 X','shadow_offset_y':'阴影 Y','shadow_blur':'阴影柔化'}
            if isinstance(value,bool): self._check(key,labels[key],('text',))
            elif isinstance(value,str): self._color(key,labels[key],('text',))
            else: self._spin(key,labels[key],*TEXT_EFFECT_RANGES[key],kinds=('text',))
        self._combo('banner_background_style','背景样式',[('纯色','solid'),('渐变','gradient_bottom')],kinds=('background',))
        self._color('banner_color','背景颜色',('background',))
        self.replace_image_button=QPushButton('替换图像…')
        self.replace_image_button.clicked.connect(lambda:self.import_file(replace_id=self.selected_id))
        self.form.addRow(self.replace_image_button)
        for key,label in [('banner_gradient_top_color','渐变顶部'),('banner_gradient_bottom_color','渐变底部')]:
            self._color(key,label,('background',))
        for key,label in [('banner_gradient_top_opacity_pct','顶部不透明度 %'),
                          ('banner_gradient_bottom_opacity_pct','底部不透明度 %'),('banner_gradient_height_pct','自动背景高度 %')]:
            self._spin(key,label,0,100,kinds=('background',))
        hint=QLabel('拖动移动 · 角手柄缩放 · 顶部手柄旋转\nShift 旋转吸附 · Alt 关闭吸附 · 空格平移 · Esc 取消')
        hint.setWordWrap(True); layout.addWidget(hint)
        self._organize_properties()
        self._refresh()

    def _organize_properties(self):
        self.tabs=QTabWidget()
        self.forms={}
        forms={}
        for name in ('内容','位置','效果'):
            page=QWidget(); forms[name]=QFormLayout(page)
            forms[name].setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            self.tabs.addTab(page,name)
        geometry={'layout_mode','align_horizontal','align_vertical','x_offset_pct','y_offset_pct','x','y','scale','width','height','rotation','opacity'}
        for key,widget in self.widgets.items():
            if key in ('name','visible','locked'): continue
            row=self.form.takeRow(widget)
            group='位置' if key in geometry else '效果' if key.startswith(('stroke_','shadow_')) else '内容'
            forms[group].addRow(row.labelItem.widget(),widget)
            self.forms[key]=forms[group]
        for widget,group in ((self.reset_layout_button,'位置'),(self.font_button,'内容'),(self.replace_image_button,'内容')):
            self.form.takeRow(widget); forms[group].addRow(widget)
        self.form.addRow(self.tabs)
        self.setMinimumWidth(0)

    def _row(self,key,label,widget,kinds=()):
        self.widgets[key]=widget; self.kinds[key]=kinds
        widget.setMinimumWidth(0)
        if isinstance(widget,QComboBox):
            widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            widget.setMinimumContentsLength(8)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding,widget.sizePolicy().verticalPolicy())
        self.form.addRow(label,widget)

    def _line(self,key,label):
        widget=QLineEdit(); widget.editingFinished.connect(lambda:self.edit(key,widget.text()))
        self._row(key,label,widget)

    def _check(self,key,label,kinds=()):
        widget=QCheckBox(); widget.toggled.connect(lambda value:self.edit(key,value))
        self._row(key,label,widget,kinds)

    def _spin(self,key,label,low,high,*,factor=1,kinds=()):
        widget=QDoubleSpinBox(); widget.setRange(low,high); widget.setDecimals(2)
        widget.setKeyboardTracking(False); widget.setProperty('factor',factor)
        widget.valueChanged.connect(lambda value:self.edit(key,value/factor))
        self._row(key,label,widget,kinds)

    def _combo(self,key,label,choices,*,kinds=()):
        widget=QComboBox()
        for caption,value in choices: widget.addItem(caption,value)
        widget.activated.connect(lambda index:self.edit(key,widget.itemData(index)))
        self._row(key,label,widget,kinds)

    def _color(self,key,label,kinds):
        widget=ColorEditor('#FFFFFF'); widget.colorChanged.connect(lambda value:self.edit(key,value))
        self._row(key,label,widget,kinds)

    def set_document(self,payload,context,*,following=False):
        doc=document(payload)
        if self.context==str(context) and self.doc==doc and self.following==following:
            return
        if self.context != str(context):
            self._context_generation += 1
        elif self.doc != doc:
            self._history.pop(self.context, None)
        self._text_timer.stop()
        self.context=str(context); self.doc=doc; self.following=following
        self._refresh()

    def flush_text(self):
        if self._text_timer.isActive():
            self._text_timer.stop()
            self.edit("text", self.text.toPlainText())

    def selected(self):
        return next((i for i in self.doc['overlays'] if i['id']==self.selected_id),None)

    def select(self,item_id):
        self.flush_text()
        self.selected_id=item_id
        self._refresh()
        self.selectionChanged.emit(item_id)

    def _selected(self,current,previous):
        if self._updating: return
        # 提交待保存文字会重建列表，先取出 id，避免访问已被 Qt 删除的行。
        item_id=current.data(Qt.ItemDataRole.UserRole) if current else ''
        self.flush_text()
        self.selected_id=item_id
        self._refresh(); self.selectionChanged.emit(self.selected_id)

    def _refresh(self):
        self._updating=True
        self.list.clear()
        for item in reversed(self.doc['overlays']):
            marks=('🔒 ' if item['locked'] else '')+('隐藏 · ' if not item['visible'] else '')
            row=QListWidgetItem(marks+item['name']); row.setData(Qt.ItemDataRole.UserRole,item['id'])
            row.setCheckState(Qt.CheckState.Checked if item['visible'] else Qt.CheckState.Unchecked)
            if item['locked']: row.setFlags(row.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            self.list.addItem(row)
            if item['id']==self.selected_id: self.list.setCurrentItem(row)
        self.scope.setText('跟随模板' if self.following else '叠加层 · 当前照片独立配置' if self.context.startswith('photo:') else '模板叠加层')
        undo,redo=self._history.get(self.context,([],[]))
        self.undo_button.setEnabled(bool(undo)); self.redo_button.setEnabled(bool(redo))
        self._updating=False; self._properties()

    def _properties(self):
        self._updating=True
        item=self.selected()
        self.properties.setEnabled(item is not None)
        self.delete_button.setEnabled(item is not None and not item["locked"])
        self.duplicate_button.setEnabled(item is not None)
        if item:
            for key,widget in self.widgets.items():
                show=not self.kinds[key] or item['type'] in self.kinds[key]
                if key in ('align_horizontal','align_vertical','x_offset_pct','y_offset_pct'): show &= item['layout_mode']=='auto' and item['type']!='background'
                if key in ('x','y'): show &= item['layout_mode']=='manual'
                if key=='text': show &= item.get('text_mode')=='literal'
                if key=='text_source': show &= item.get('text_mode')=='metadata'
                self.forms.get(key,self.form).setRowVisible(widget,show)
                widget.setEnabled(not item['locked'] or key in ('locked','visible','name'))
                value=item.get(key)
                if isinstance(widget,QCheckBox): widget.setChecked(bool(value))
                elif isinstance(widget,QDoubleSpinBox): widget.setValue(float(value or 0)*widget.property('factor'))
                elif isinstance(widget,QLineEdit): widget.setText(str(value or ''))
                elif isinstance(widget,QPlainTextEdit):
                    if widget.toPlainText()!=str(value or ''): widget.setPlainText(str(value or ''))
                elif isinstance(widget,ColorEditor): widget.set_value(str(value or '#000000'))
                elif key=='text_source':
                    value=value or {}; idx=widget.findData((value.get('type'),value.get('key')))
                    if idx>=0: widget.setCurrentIndex(idx)
                    else: widget.setEditText(str(value.get('key','')))
                else:
                    idx=widget.findData(value)
                    if idx<0 and key=='font_type': widget.addItem(str(value),value); idx=widget.count()-1
                    widget.setCurrentIndex(max(0,idx))
            self.font_button.setVisible(item['type']=='text')
            self.replace_image_button.setVisible(item['type']=='image')
            self.replace_image_button.setEnabled(not item['locked'])
            self.tabs.setTabVisible(2,item['type']=='text')
            self.reset_layout_button.setEnabled(not item['locked'])
        self._updating=False

    def commit(self,doc,*,following=False):
        doc=document(doc)
        if self.doc==doc and self.following==following: return
        undo,redo=self._history.setdefault(self.context,([],[]))
        undo.append((self.doc,self.following,self.selected_id)); redo.clear()
        self._history.move_to_end(self.context)
        while sum(len(a)+len(b) for a,b in self._history.values())>100:
            key=next(iter(self._history)); a,b=self._history[key]
            if a: a.pop(0)
            elif b: b.pop(0)
            if not a and not b: del self._history[key]
        self.doc=doc; self.following=following
        self._refresh(); self.changed.emit(deepcopy(doc))

    def _travel(self,back):
        self.flush_text()
        undo,redo=self._history.get(self.context,([],[]))
        source,target=(undo,redo) if back else (redo,undo)
        if not source: return
        target.append((self.doc,self.following,self.selected_id))
        self.doc,self.following,self.selected_id=source.pop()
        self._refresh(); self.changed.emit(deepcopy(self.doc)); self.selectionChanged.emit(self.selected_id)

    def undo(self): self._travel(True)
    def redo(self): self._travel(False)

    def edit(self,key,value):
        if not self._updating and key!='text': self.flush_text()
        if self._updating or self.selected() is None: return
        if self.selected()['locked'] and key not in ('locked','visible','name'): return
        if key=='layout_mode' and value=='manual':
            self.activateRequested.emit()
        doc=deepcopy(self.doc)
        item=next(i for i in doc['overlays'] if i['id']==self.selected_id)
        if key=='layout_mode' and value=='manual' and hasattr(self,'manual_geometry'):
            resolved=self.manual_geometry()
            if resolved: item.update(resolved)
        item[key]=value
        self.commit(doc)

    def replace_item(self,item):
        doc=deepcopy(self.doc)
        for index,old in enumerate(doc['overlays']):
            if old['id']==item['id']: doc['overlays'][index]=item; self.commit(doc); return

    def add(self,kind,*,metadata=False,asset=None):
        self.flush_text()
        item=new_item(kind,metadata=metadata)
        if asset:
            key,raw=asset; item.update(asset_id=key,name=raw['name'])
            # 高而窄的图像也完整落在成片内。
            item['width']=min(item['width'],item['width']*raw['width']/max(1,raw['height']))
        if kind=='background': item.update(banner_color='#000000',banner_background_style='solid')
        doc=deepcopy(self.doc)
        if asset: doc['overlay_assets'][asset[0]]=asset[1]
        doc['overlays'].append(item); self.selected_id=item['id']
        self.commit(doc); self.tabs.setCurrentIndex(0); self.activateRequested.emit(); self.selectionChanged.emit(self.selected_id)

    def delete(self):
        self.flush_text()
        item=self.selected()
        if not item or item['locked']: return
        doc=deepcopy(self.doc); doc['overlays']=[i for i in doc['overlays'] if i['id']!=item['id']]
        self.commit(doc); self.select('')

    def duplicate(self):
        self.flush_text()
        item=self.selected()
        if not item: return
        doc=deepcopy(self.doc); item=deepcopy(item); item.update(id=uuid.uuid4().hex,name=item['name']+' 副本',locked=False)
        if item['layout_mode']=='manual': item['x']+=.02; item['y']+=.02
        doc['overlays'].append(item); self.selected_id=item['id']; self.commit(doc)

    def _visibility_changed(self, row):
        if self._updating: return
        item_id=row.data(Qt.ItemDataRole.UserRole)
        visible=row.checkState()==Qt.CheckState.Checked
        self.flush_text()
        doc=deepcopy(self.doc)
        item=next(i for i in doc['overlays'] if i['id']==item_id)
        item['visible']=visible
        self.commit(doc)

    def _reorder(self,*args):
        if self._updating: return
        ids=[self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        self.flush_text()
        doc=deepcopy(self.doc); by_id={i['id']:i for i in doc['overlays']}
        doc['overlays']=[by_id[i] for i in reversed(ids)]; self.commit(doc)

    def _metadata_selected(self,index):
        source,key=self.metadata.itemData(index); self.edit('text_source',dict(type=source,key=key))

    def _metadata_typed(self):
        idx=self.metadata.currentIndex()
        if idx>=0 and self.metadata.currentText()==self.metadata.itemText(idx): return
        self.edit('text_source',dict(type='auto',key=self.metadata.currentText()))

    def _load_fonts(self):
        value=(self.selected() or {}).get('font_type','auto')
        self.font.clear()
        for label,key in template_font_choices(chinese_only=True,prefer_chinese_label=True): self.font.addItem(label,key)
        self.font.setCurrentIndex(max(0,self.font.findData(value)))
        self.font_button.hide()

    def focus_content(self):
        item=self.selected()
        if not item or item['type']!='text' or item['locked']: return
        self.tabs.setCurrentIndex(0)
        widget=self.text if item['text_mode']=='literal' else self.metadata
        parent=self.parentWidget()
        while parent is not None:
            if isinstance(parent,QScrollArea):
                parent.ensureWidgetVisible(widget,0,10)
                break
            parent=parent.parentWidget()
        widget.setFocus()

    def import_file(self, *, replace_id=None):
        from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
        pattern=' '.join('*'+ext for ext in sorted(SUPPORTED_IMAGE_EXTENSIONS))
        path,_=QFileDialog.getOpenFileName(self,'添加图像叠加层','',f'图像 ({pattern});;所有文件 (*)')
        if not path: return
        worker=_Import(f'{self.context}|{self._context_generation}',path,replace_id); self._jobs.append(worker)
        worker.signals.done.connect(self._asset_ready)
        self.scope.setText('正在导入图片…')
        QThreadPool.globalInstance().start(worker)

    @pyqtSlot(str,object,object)
    def _asset_ready(self,context,result,error):
        sender=self.sender()
        worker=next((w for w in self._jobs if w.signals is sender),None)
        self._jobs=[w for w in self._jobs if w.signals is not sender]
        if context!=f'{self.context}|{self._context_generation}': return
        if error: QMessageBox.warning(self,'图像素材导入失败',error); self._refresh()
        elif worker is not None and worker.replace_id:
            doc=deepcopy(self.doc)
            item=next((i for i in doc['overlays'] if i['id']==worker.replace_id),None)
            if item is None or item['locked']: return
            key,asset=result
            item.update(asset_id=key,name=asset['name'])
            doc['overlay_assets'][key]=asset
            self.commit(doc)
        else: self.add('image',asset=result)
