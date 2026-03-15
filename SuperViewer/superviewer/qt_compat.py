# -*- coding: utf-8 -*-
"""
PyQt5/PyQt6 兼容层：统一导入与枚举别名，供 SuperViewer 各模块使用。
不包含业务逻辑，不依赖其它 SuperViewer 模块。
"""

try:
    from PyQt6.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QListView,
        QMenu,
        QDialog,
        QPushButton,
        QToolButton,
        QDialogButtonBox,
        QTableWidget,
        QTableWidgetItem,
        QSplitter,
        QFrame,
        QFileDialog,
        QMessageBox,
        QHeaderView,
        QAbstractItemView,
        QScrollArea,
        QGroupBox,
        QGridLayout,
        QCheckBox,
        QComboBox,
        QSpinBox,
        QTabWidget,
        QTreeView,
        QTreeWidget,
        QTreeWidgetItem,
        QSizePolicy,
        QStyledItemDelegate,
        QStackedWidget,
        QSlider,
    )
    from PyQt6.QtCore import Qt, QMimeData, QSize, QDir, QThread, QTimer, pyqtSignal, QModelIndex, QRect
    from PyQt6.QtGui import (
        QPixmap,
        QImage,
        QTransform,
        QDragEnterEvent,
        QDropEvent,
        QFont,
        QPalette,
        QColor,
        QAction,
        QIcon,
        QFileSystemModel,
        QPainter,
        QBrush,
        QPen,
    )
except ImportError:
    from PyQt5.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QListView,
        QMenu,
        QDialog,
        QPushButton,
        QToolButton,
        QDialogButtonBox,
        QTableWidget,
        QTableWidgetItem,
        QSplitter,
        QFrame,
        QFileDialog,
        QMessageBox,
        QHeaderView,
        QAbstractItemView,
        QScrollArea,
        QGroupBox,
        QGridLayout,
        QCheckBox,
        QComboBox,
        QSpinBox,
        QTabWidget,
        QTreeView,
        QTreeWidget,
        QTreeWidgetItem,
        QFileSystemModel,
        QSizePolicy,
        QStyledItemDelegate,
        QStackedWidget,
        QSlider,
    )
    from PyQt5.QtCore import Qt, QMimeData, QSize, QDir, QThread, QTimer, pyqtSignal, QModelIndex, QRect
    from PyQt5.QtGui import (
        QPixmap,
        QImage,
        QTransform,
        QDragEnterEvent,
        QDropEvent,
        QFont,
        QPalette,
        QColor,
        QAction,
        QIcon,
        QPainter,
        QBrush,
        QPen,
    )

# PyQt5/6 枚举兼容
if hasattr(Qt, "AlignmentFlag"):
    _AlignCenter = Qt.AlignmentFlag.AlignCenter
    _LeftButton = Qt.MouseButton.LeftButton
    _KeepAspectRatio = Qt.AspectRatioMode.KeepAspectRatio
    _SmoothTransformation = Qt.TransformationMode.SmoothTransformation
else:
    _AlignCenter = Qt.AlignCenter
    _LeftButton = Qt.LeftButton
    _KeepAspectRatio = Qt.KeepAspectRatio
    _SmoothTransformation = Qt.SmoothTransformation
if hasattr(QFrame, "Shape"):
    _FrameBox = QFrame.Shape.Box
    _FrameSunken = QFrame.Shadow.Sunken
else:
    _FrameBox = QFrame.Box
    _FrameSunken = QFrame.Sunken
if hasattr(QHeaderView, "ResizeMode"):
    _ResizeStretch = QHeaderView.ResizeMode.Stretch
else:
    _ResizeStretch = QHeaderView.Stretch
if hasattr(QAbstractItemView, "SelectionBehavior"):
    _SelectRows = QAbstractItemView.SelectionBehavior.SelectRows
elif hasattr(QAbstractItemView, "SelectRows"):
    _SelectRows = QAbstractItemView.SelectRows
else:
    _SelectRows = QAbstractItemView.SelectRows
if hasattr(QAbstractItemView, "EditTrigger"):
    _NoEditTriggers = QAbstractItemView.EditTrigger.NoEditTriggers
    _DoubleClicked = QAbstractItemView.EditTrigger.DoubleClicked
else:
    _NoEditTriggers = QAbstractItemView.NoEditTriggers
    _DoubleClicked = QAbstractItemView.DoubleClicked
try:
    _ItemIsEditable = Qt.ItemFlag.ItemIsEditable
except AttributeError:
    _ItemIsEditable = Qt.ItemIsEditable
try:
    _UserRole = Qt.ItemDataRole.UserRole
except AttributeError:
    _UserRole = Qt.UserRole
_orient = getattr(Qt, "Orientation", None)
_Horizontal = getattr(_orient, "Horizontal", None) if _orient else None
if _Horizontal is None:
    _Horizontal = getattr(Qt, "Horizontal", 1)
if hasattr(QMessageBox, "Icon"):
    _MsgInfo = QMessageBox.Icon.Information
else:
    _MsgInfo = QMessageBox.Information
if hasattr(QMessageBox, "StandardButton"):
    _MsgOk = QMessageBox.StandardButton.Ok
else:
    _MsgOk = QMessageBox.Ok
