"""Relative A/B navigation, adapted from main's ViewerABViewLink."""
try:
    from PyQt6.QtCore import QObject
except ImportError:
    from PyQt5.QtCore import QObject


class ABViewLink(QObject):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.states = {}
        self.applying = False
        self.canvases = tuple(panel.preview_for_side(side).canvas for side in ("A", "B"))
        for canvas in self.canvases:
            canvas.viewport_interacted.connect(lambda canvas=canvas: self.move_from(canvas))
            canvas.viewport_content_changed.connect(lambda canvas=canvas: self.apply_to(canvas))
        panel.link_button.toggled.connect(self.toggle)

    def enabled(self):
        return self.panel.is_enabled() and self.panel.link_button.isChecked()

    def toggle(self, checked):
        self.states.clear()
        if checked and self.enabled():
            self.states = {canvas: state for canvas in self.canvases
                           if (state := canvas.viewport_state()) is not None}

    def move_from(self, source):
        if not self.enabled() or self.applying:
            return
        previous = self.states.get(source)
        current = source.viewport_state()
        self.states[source] = current
        if previous is None or current is None or previous[0] <= 0:
            return
        zoom_ratio = current[0] / previous[0]
        delta = (current[1][0] - previous[1][0], current[1][1] - previous[1][1])
        self.applying = True
        try:
            for canvas in self.canvases:
                if canvas is source:
                    continue
                target = self.states.get(canvas)
                if target is not None:
                    state = (target[0] * zoom_ratio,
                             (target[1][0] + delta[0], target[1][1] + delta[1]))
                    canvas.apply_viewport_state(state)
                    self.states[canvas] = canvas.viewport_state() or state
        finally:
            self.applying = False

    def apply_to(self, canvas):
        if not self.enabled() or self.applying or canvas.viewport_state() is None:
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
