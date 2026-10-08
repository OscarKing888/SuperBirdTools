"""Viewer 照片菜单的分组与图标；动作仍由原有浏览器和控制器创建。"""
from __future__ import annotations

import sys

from app_common.file_utils import reveal_in_file_manager
from app_common.log import get_logger

try:
    from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt
    from PyQt6.QtGui import QIcon, QIconEngine, QPainter, QPalette, QPen, QPixmap, QPolygonF
    from PyQt6.QtWidgets import QApplication, QMenu
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QLineF, QPointF, QRectF, Qt
    from PyQt5.QtGui import QIcon, QIconEngine, QPainter, QPalette, QPen, QPixmap, QPolygonF
    from PyQt5.QtWidgets import QApplication, QMenu

_log = get_logger("superviewer.file_context_menu")
_GROUPS = {"bird": ("鸟种信息", "bird"), "capture": ("拍摄信息", "camera"),
           "process": ("分析与处理", "process")}


def file_menu_group(name, *, order=100):
    """声明扩展动作的分组，不包装回调或改变控制器的菜单/任务行为。"""
    if name not in _GROUPS:
        raise ValueError(f"Unknown file menu group: {name}")

    def decorate(callback):
        callback.file_menu_group = name
        callback.file_menu_order = order
        return callback
    return decorate


class _MenuIcon(QIconEngine):
    """无字体/外部资源依赖，按系统主题、悬停和禁用状态绘制线条。"""
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def clone(self):
        return _MenuIcon(self.kind)

    def paint(self, painter, rect, mode, state):
        palette = QApplication.palette()
        group = QPalette.ColorGroup.Disabled if mode == QIcon.Mode.Disabled else QPalette.ColorGroup.Active
        role = QPalette.ColorRole.HighlightedText if mode == QIcon.Mode.Selected else QPalette.ColorRole.WindowText
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(rect.x(), rect.y())
        painter.scale(rect.width() / 24, rect.height() / 24)
        pen = QPen(palette.color(group, role))
        pen.setWidthF(1.65)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        def line(x1, y1, x2, y2):
            painter.drawLine(QLineF(x1, y1, x2, y2))

        def box(x, y, w, h):
            painter.drawRoundedRect(QRectF(x, y, w, h), 1, 1)

        def ellipse(x, y, w, h):
            painter.drawEllipse(QRectF(x, y, w, h))

        def polygon(points):
            painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))

        kind = self.kind
        if kind == "copy":
            box(8, 8, 12, 13); line(4, 16, 4, 3); line(4, 3, 15, 3)
        elif kind == "cut":
            ellipse(3, 15, 6, 6); ellipse(15, 15, 6, 6)
            line(7, 3, 17, 16); line(17, 3, 7, 16)
        elif kind == "paste":
            box(5, 5, 14, 16); box(9, 2, 6, 5)
            line(9, 12, 15, 12); line(9, 16, 15, 16)
        elif kind == "star":
            polygon([(12, 2), (15, 8), (22, 9), (17, 14), (18, 21),
                     (12, 18), (6, 21), (7, 14), (2, 9), (9, 8)])
        elif kind == "tag":
            polygon([(3, 3), (12, 3), (22, 13), (13, 22), (3, 12)])
            ellipse(6, 6, 3, 3)
        elif kind == "bird":
            ellipse(6, 9, 11, 9); ellipse(14, 4, 6, 6)
            line(20, 6, 23, 8); line(6, 12, 2, 9); line(2, 9, 6, 17)
            line(10, 18, 9, 21); line(14, 18, 14, 21)
        elif kind == "camera":
            box(2, 7, 20, 14); ellipse(8, 10, 8, 8)
            line(7, 7, 9, 3); line(9, 3, 15, 3); line(15, 3, 17, 7)
        elif kind == "process":
            ellipse(3, 3, 13, 13); line(15, 15, 22, 22)
            line(6, 12, 9, 8); line(9, 8, 12, 11); line(12, 11, 14, 6)
        elif kind == "denoise":
            polygon([(9, 2), (11, 8), (17, 10), (11, 12), (9, 18), (7, 12), (1, 10), (7, 8)])
            line(19, 14, 19, 22); line(15, 18, 23, 18)
        elif kind == "shield":
            polygon([(12, 2), (21, 6), (19, 16), (12, 22), (5, 16), (3, 6)])
            line(7, 11, 11, 15); line(11, 15, 17, 8)
        elif kind == "send":
            polygon([(2, 3), (22, 12), (2, 21), (6, 12)])
            line(6, 12, 22, 12)
        elif kind == "folder":
            polygon([(2, 5), (9, 5), (12, 8), (22, 8), (22, 20), (2, 20)])
        elif kind == "delete":
            line(3, 6, 21, 6); box(9, 2, 6, 4)
            line(5, 6, 6, 21); line(6, 21, 18, 21); line(18, 21, 19, 6)
            line(10, 10, 10, 17); line(14, 10, 14, 17)
        painter.restore()

    def pixmap(self, size, mode, state):
        if size.isEmpty():
            return QPixmap()
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        return pixmap


def menu_icon(kind):
    """菜单和设置导航共用的主题自适应图标。"""
    return QIcon(_MenuIcon(kind))


def _icon(action, kind):
    action.setIcon(menu_icon(kind))
    # macOS 默认可隐藏菜单图标；显式启用这些用于辨认功能的图标。
    action.setIconVisibleInMenu(True)


def _submenu(menu, title, kind):
    sub = menu.addMenu(title)
    _icon(sub.menuAction(), kind)
    return sub


def _add_with_icons(menu, callback, args, kinds):
    before = set(menu.actions())
    callback(menu, *args)
    added = [a for a in menu.actions() if a not in before and not a.isSeparator()]
    for action, kind in zip(added, kinds):
        _icon(action, kind)
    return added


def _prepend_clipboard_actions(menu, panel, paths, primary):
    """扩展完成后置顶常用操作，避免扩展的 insertAction 抢占顶部。"""
    anchor = menu.actions()[0] if menu.actions() else None
    copy, cut, paste = _add_with_icons(
        menu, panel._add_file_clipboard_menu_actions, (paths,), ("copy", "cut", "paste"),
    )
    header = [copy, paste, cut, menu.addSeparator()]
    header.extend(_add_with_icons(
        menu, panel._add_species_menu_actions, (primary, paths), ("copy", "paste"),
    ))
    header.append(menu.addSeparator())
    for action in header:
        menu.removeAction(action)
        menu.insertAction(anchor, action)


def build_file_context_menu(panel, paths, primary, *, log_prefix):
    """仅组装视图，复用共享动作的路径解析、快捷键、权限与信号绑定。"""
    menu = QMenu(panel)
    # 仅调整此菜单树的留白，颜色与选中/禁用状态继续跟随系统主题。
    menu.setStyleSheet(
        "QMenu { padding: 4px 6px; }"
        "QMenu::item { padding: 6px 20px 6px 8px; }"
        "QMenu::separator { margin: 4px 10px; }"
    )
    _add_with_icons(menu, panel._add_rating_menu_actions, (paths,), ("star",))
    _add_with_icons(menu, panel._add_photo_tag_menu_actions, (paths,), ("tag",))
    menu.addSeparator()
    groups = {key: _submenu(menu, title, kind) for key, (title, kind) in _GROUPS.items()}
    extenders = sorted(getattr(panel, "_file_context_menu_extenders", ()),
                       key=lambda callback: getattr(callback, "file_menu_order", 100))
    for extender in extenders:
        target = groups.get(getattr(extender, "file_menu_group", None), menu)
        # 每个控制器的操作形成一个小组，运行中进度/停止动作仍在原分组内。
        separator = target.addSeparator() if target is not menu and target.actions() else None
        before = set(target.actions())
        try:
            extender(target, list(paths))
        except Exception:
            _log.exception("[%s] file context menu extender failed", log_prefix)
        if separator is not None and set(target.actions()) == before:
            target.removeAction(separator)
            separator.deleteLater()

    for sub in groups.values():
        sub.menuAction().setVisible(bool(sub.actions()))

    menu.addSeparator()
    send_menu = _submenu(menu, "发送到", "send")
    panel._add_send_to_external_app_actions(send_menu, paths)
    locate_menu = _submenu(menu, "定位文件", "folder")
    label = "在Finder中显示" if sys.platform == "darwin" else "在资源管理器中显示"
    reveal_path = panel._resolve_reveal_path(primary)
    if reveal_path:
        _log.info("[%s] reveal_path=%r paths=%s", log_prefix, reveal_path, len(paths))
        locate_menu.addAction(label, lambda checked=False, p=reveal_path: reveal_in_file_manager(p))
    locate_menu.addAction("复制文件全路径", lambda checked=False, p=list(paths): panel._copy_filenames_to_clipboard(p))
    locate_menu.addSeparator()
    panel._add_browse_preview_menu_action(locate_menu, primary)
    menu.addSeparator()
    _add_with_icons(menu, panel._add_delete_menu_action, (paths,), ("delete",))
    _prepend_clipboard_actions(menu, panel, paths, primary)
    return menu
