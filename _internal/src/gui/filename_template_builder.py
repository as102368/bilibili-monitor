from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QPushButton,
    QLabel,
    QComboBox,
)
from PySide6.QtCore import Qt


class FilenameTemplateBuilder(QWidget):
    """可视化的文件名模板构建器"""

    FIELDS = [
        ("序号", "%(index)s"),
        ("视频章节", "%(section)s"),
        ("视频标题", "%(title)s"),
        ("分P标题", "%(part_title)s"),
        ("视频分区", "%(category)s"),
        ("音质", "%(audio_quality)s"),
        ("画质", "%(quality)s"),
        ("视频编码", "%(video_codec)s"),
        ("视频发布时间", "%(upload_date)s"),
        ("avid", "%(avid)s"),
        ("bvid", "%(bvid)s"),
        ("cid", "%(cid)s"),
        ("UP主ID", "%(uploader_id)s"),
        ("UP主昵称", "%(uploader)s"),
        ("扩展名", "%(ext)s"),
    ]

    SEPARATORS = [
        ("/", "/"),
        ("_", "_"),
        ("-", "-"),
        ("+", "+"),
        (",", ","),
        (".", "."),
        ("&", "&"),
        ("#", "#"),
        ("(", "("),
        (")", ")"),
        ("[", "["),
        ("]", "]"),
        ("{", "{"),
        ("}", "}"),
        ("空格", " "),
    ]

    DEFAULT_TEMPLATE = "%(uploader)s - %(title)s [%(bvid)s].%(ext)s"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parts: list[dict] = []
        self.setMinimumHeight(240)
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        # 可选字段区域
        avail_group = QWidget()
        avail_layout = QVBoxLayout(avail_group)
        avail_layout.setContentsMargins(10, 10, 10, 10)
        avail_layout.setSpacing(8)

        avail_title = QLabel("可选字段:")
        avail_title.setStyleSheet("color: #cccccc; font-size: 13px;")
        avail_layout.addWidget(avail_title)

        # 字段按钮网格
        fields_widget = QWidget()
        fields_grid = QGridLayout(fields_widget)
        fields_grid.setSpacing(6)
        fields_grid.setContentsMargins(0, 0, 0, 0)
        col = 0
        row = 0
        for label, value in self.FIELDS:
            btn = QPushButton(label)
            btn.setProperty("template_value", value)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #4a4a4a;
                    color: #ffffff;
                    border: 1px solid #5a5a5a;
                    border-radius: 10px;
                    padding: 3px 10px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #2196F3;
                    border-color: #1976D2;
                }
                """
            )
            btn.clicked.connect(lambda _checked, l=label, v=value: self._add_part(l, v))
            fields_grid.addWidget(btn, row, col)
            col += 1
            if col >= 7:
                col = 0
                row += 1
        avail_layout.addWidget(fields_widget)

        # 分隔符按钮网格
        sep_widget = QWidget()
        sep_grid = QGridLayout(sep_widget)
        sep_grid.setSpacing(6)
        sep_grid.setContentsMargins(0, 0, 0, 0)
        col = 0
        row = 0
        for label, value in self.SEPARATORS:
            btn = QPushButton(label)
            btn.setProperty("template_value", value)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #4a4a4a;
                    color: #ffffff;
                    border: 1px solid #5a5a5a;
                    border-radius: 10px;
                    padding: 3px 10px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #2196F3;
                    border-color: #1976D2;
                }
                """
            )
            btn.clicked.connect(lambda _checked, l=label, v=value: self._add_part(l, v))
            sep_grid.addWidget(btn, row, col)
            col += 1
            if col >= 7:
                col = 0
                row += 1
        avail_layout.addWidget(sep_widget)

        avail_group.setStyleSheet(
            """
            QWidget {
                background-color: #3a3a3a;
                border: 1px solid #555555;
                border-radius: 8px;
            }
            QLabel {
                border: none;
                background: transparent;
            }
            """
        )
        main_layout.addWidget(avail_group)

        # 文件名区域
        filename_title = QLabel("文件名:")
        filename_title.setStyleSheet("color: #cccccc; font-size: 13px;")
        main_layout.addWidget(filename_title)

        self.filename_container = QWidget()
        self.filename_container.setStyleSheet(
            """
            QWidget {
                background-color: #3a3a3a;
                border: 1px solid #555555;
                border-radius: 8px;
            }
            """
        )
        self.filename_layout = QHBoxLayout(self.filename_container)
        self.filename_layout.setSpacing(6)
        self.filename_layout.setContentsMargins(10, 8, 10, 8)
        self.filename_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.filename_layout.addStretch()
        main_layout.addWidget(self.filename_container)

        # 底部选项
        options_layout = QHBoxLayout()
        options_layout.setSpacing(8)

        options_layout.addWidget(QLabel("时间格式:"))
        self.time_format_combo = QComboBox()
        self.time_format_combo.addItems(
            ["yyyy-MM-dd", "yyyy-MM-dd HH-mm-ss", "yyyyMMdd", "yyyy/MM/dd"]
        )
        options_layout.addWidget(self.time_format_combo)

        options_layout.addWidget(QLabel("序号格式:"))
        self.index_format_combo = QComboBox()
        self.index_format_combo.addItems(["自然数", "两位数字", "三位数字"])
        options_layout.addWidget(self.index_format_combo)

        options_layout.addStretch()

        self.reset_btn = QPushButton("恢复默认")
        self.reset_btn.clicked.connect(self._reset_default)
        options_layout.addWidget(self.reset_btn)

        main_layout.addLayout(options_layout)

        self._refresh_filename_ui()

    def _add_part(self, label: str, value: str):
        self.parts.append({"label": label, "value": value})
        self._refresh_filename_ui()

    def _remove_part(self, index: int):
        if 0 <= index < len(self.parts):
            del self.parts[index]
            self._refresh_filename_ui()

    def _refresh_filename_ui(self):
        # 清空现有部件，保留最后的 stretch
        while self.filename_layout.count() > 1:
            item = self.filename_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for idx, part in enumerate(self.parts):
            btn = QPushButton(part["label"])
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip("点击移除该字段")
            btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #555555;
                    color: #ffffff;
                    border: 1px solid #777777;
                    border-radius: 10px;
                    padding: 3px 10px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #f44336;
                    border-color: #d32f2f;
                }
                """
            )
            btn.clicked.connect(lambda _checked, i=idx: self._remove_part(i))
            self.filename_layout.insertWidget(idx, btn)

    def _reset_default(self):
        self.set_template(self.DEFAULT_TEMPLATE)

    def get_template(self) -> str:
        return "".join(p["value"] for p in self.parts)

    def set_template(self, template: str):
        self.parts = self._parse_template(template)
        self._refresh_filename_ui()

    @classmethod
    def _parse_template(cls, template: str) -> list[dict]:
        reverse_map = {v: k for k, v in cls.FIELDS}
        for label, val in cls.SEPARATORS:
            reverse_map[val] = label

        parts = []
        i = 0
        n = len(template)
        field_values = [v for _l, v in cls.FIELDS]

        while i < n:
            matched = False
            for fv in sorted(field_values, key=len, reverse=True):
                if template.startswith(fv, i):
                    parts.append({"label": reverse_map.get(fv, fv), "value": fv})
                    i += len(fv)
                    matched = True
                    break
            if matched:
                continue

            # 收集连续的分隔符字符
            j = i
            while j < n:
                matched2 = False
                for fv in field_values:
                    if template.startswith(fv, j):
                        matched2 = True
                        break
                if matched2:
                    break
                j += 1
            sep_str = template[i:j]
            parts.append({"label": reverse_map.get(sep_str, sep_str), "value": sep_str})
            i = j

        return parts

    def set_time_format(self, fmt: str):
        index = self.time_format_combo.findText(fmt)
        if index >= 0:
            self.time_format_combo.setCurrentIndex(index)

    def get_time_format(self) -> str:
        return self.time_format_combo.currentText()

    def set_index_format(self, fmt: str):
        index = self.index_format_combo.findText(fmt)
        if index >= 0:
            self.index_format_combo.setCurrentIndex(index)

    def get_index_format(self) -> str:
        return self.index_format_combo.currentText()
