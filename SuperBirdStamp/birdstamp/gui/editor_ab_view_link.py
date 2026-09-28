"""仅联动视野：适应窗口倍率和图像归一化中心，不重新解码或修改裁切。"""
from PyQt6.QtCore import QObject


class ABViewLink(QObject):
    def __init__(self, ab):
        super().__init__(ab)
        self.ab = ab
        self.state = None
        self.applying = False
        self.canvases = (ab.preview.canvas, ab.editor.preview_label.canvas)
        for canvas in self.canvases:
            canvas.viewport_interacted.connect(lambda canvas=canvas: self.move_from(canvas))
            canvas.viewport_content_changed.connect(lambda canvas=canvas: self.apply_to(canvas))
        for center in (ab.center, ab.editor.auto_focus_center_check):
            center.toggled.connect(self.focus_changed)
        ab.linked.toggled.connect(self.toggle)

    def enabled(self):
        return self.ab.enabled.isChecked() and self.ab.linked.isChecked() and not self.ab.stopping

    def toggle(self, checked):
        self.state = None
        if checked and self.enabled():
            # 焦点锁定会禁止平移；联动与独立焦点锁定互斥，避免另一侧悄悄跳回焦点。
            self.ab.center.setChecked(False)
            self.ab.editor.auto_focus_center_check.setChecked(False)
            canvas = self.canvases[0 if self.ab.active_side == 'a' else 1]
            if canvas.viewport_state() is None:
                canvas = next((item for item in self.canvases if item.viewport_state() is not None), canvas)
            self.move_from(canvas)

    def focus_changed(self, checked):
        if checked and self.enabled():
            self.ab.linked.setChecked(False)

    def move_from(self, source):
        if not self.enabled() or self.applying:
            return
        self.state = source.viewport_state()
        for canvas in self.canvases:
            if canvas is not source:
                self.apply_to(canvas)

    def apply_to(self, canvas):
        if not self.enabled() or self.applying or self.state is None:
            return
        self.applying = True
        try:
            canvas.apply_viewport_state(self.state)
        finally:
            self.applying = False
