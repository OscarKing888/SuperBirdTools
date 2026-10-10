"""Draft-only form for the video processing settings page."""
from pathlib import Path

from app_common.superviewer_user_options import valid_video_frame_suffix
from .qt_compat import (
    QComboBox, QFileDialog, QGridLayout, QLabel, QLineEdit, QPushButton, QWidget,
)


class VideoFrameOptionsForm(QWidget):
    def __init__(self, options, parent=None):
        super().__init__(parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setColumnStretch(1, 1)
        self.mode = QComboBox(self)
        for label, value in (("视频所在目录", "source_subdir"),
                             ("指定固定根目录", "fixed"), ("每次弹出选择框", "ask")):
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(self.mode.findData(options["video_frame_output_mode"]))
        self.directory = QLineEdit(options["video_frame_output_directory"], self)
        self.browse = QPushButton("浏览…", self)
        self.browse.clicked.connect(self._browse)
        self.suffix = QLineEdit(options["video_frame_suffix"], self)
        self.suffix.setMaxLength(64)
        self.suffix.setToolTip("追加在不含扩展名的视频文件名后；可留空，不可包含路径分隔符。")
        for row, label, field in ((0, "输出目录类型", self.mode),
                                  (1, "固定根目录", self.directory),
                                  (2, "目录名后缀", self.suffix)):
            caption = QLabel(label, self)
            caption.setBuddy(field)
            layout.addWidget(caption, row, 0)
            layout.addWidget(field, row, 1, 1, 1 if row == 1 else 2)
        layout.addWidget(self.browse, 1, 2)
        self.example = QLabel(self)
        self.example.setWordWrap(True)
        layout.addWidget(self.example, 3, 0, 1, 3)
        note = QLabel("每个视频单独创建文件夹，重名自动编号。\n"
                      "新的设置用于下一次提取；当前任务不受影响。", self)
        note.setWordWrap(True)
        layout.addWidget(note, 4, 0, 1, 3)
        self.mode.currentIndexChanged.connect(self._update)
        self.suffix.textChanged.connect(self._update)
        self._update()

    def _update(self, *_args):
        fixed = self.mode.currentData() == "fixed"
        self.directory.setEnabled(fixed)
        self.browse.setEnabled(fixed)
        # Plain filename example; no path creation or writes while editing.
        self.example.setText("目录名示例：视频001" + self.suffix.text())

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择视频帧输出根目录", self.directory.text())
        if path:
            self.directory.setText(path)

    def validation_error(self):
        if not valid_video_frame_suffix(self.suffix.text()):
            return "目录名后缀不能包含路径分隔符、保留字符或控制字符，也不能以点或空格结尾。"
        if self.mode.currentData() == "fixed":
            path = self.directory.text().strip()
            if not path or "\x00" in path or not Path(path).expanduser().is_absolute():
                return "请选择固定输出根目录，或输入完整的绝对路径。"
        return ""

    def selected_options(self):
        return {
            "video_frame_output_mode": self.mode.currentData(),
            "video_frame_output_directory": self.directory.text().strip(),
            "video_frame_suffix": self.suffix.text(),
        }
