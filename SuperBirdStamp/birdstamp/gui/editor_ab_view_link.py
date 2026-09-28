"""联动视野变化，保留两侧已有的缩放比例和相对位置。"""
from PyQt6.QtCore import QObject


class ABViewLink(QObject):
    def __init__(self, ab):
        super().__init__(ab)
        self.ab = ab
        self.states = {}
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
        self.states.clear()
        if checked and self.enabled():
            # 焦点锁定会禁止平移；联动与独立焦点锁定互斥，避免另一侧悄悄跳回焦点。
            self.ab.center.setChecked(False)
            self.ab.editor.auto_focus_center_check.setChecked(False)
            # 当前两侧各自为基准；开启联动本身不改变任何视野。
            self.states = {canvas: canvas.viewport_state() for canvas in self.canvases}

    def focus_changed(self, checked):
        if checked and self.enabled():
            self.ab.linked.setChecked(False)

    def move_from(self, source):
        if not self.enabled() or self.applying:
            return
        previous = self.states.get(source)
        current = source.viewport_state()
        self.states[source] = current
        if previous is None or current is None or previous[0] <= 0:
            return
        zoom_ratio = current[0] / previous[0]
        center_delta = (current[1][0] - previous[1][0], current[1][1] - previous[1][1])
        self.applying = True
        try:
            for canvas in self.canvases:
                if canvas is source:
                    continue
                target = self.states.get(canvas)
                if target is None:
                    continue
                canvas.apply_viewport_state((target[0] * zoom_ratio,
                                             (target[1][0] + center_delta[0],
                                              target[1][1] + center_delta[1])))
                # 边界可能限制目标视野；后续变化从实际位置继续计算。
                self.states[canvas] = canvas.viewport_state()
        finally:
            self.applying = False

    def apply_to(self, canvas):
        if not self.enabled() or self.applying:
            return
        state = self.states.get(canvas)
        if state is None:
            self.states[canvas] = canvas.viewport_state()
            return
        self.applying = True
        try:
            canvas.apply_viewport_state(state)
            self.states[canvas] = canvas.viewport_state() or state
        finally:
            self.applying = False
