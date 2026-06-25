"""
运行状态页：展示当前下载/上传任务、进度条与当前文件。
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QPushButton,
)
from PySide6.QtCore import Qt


class _ProgressRow:
    __slots__ = ("key", "label", "progress", "table", "row_idx")

    def __init__(self, key: str, label: str, progress: QProgressBar, table: QTableWidget, row_idx: int):
        self.key = key
        self.label = label
        self.progress = progress
        self.table = table
        self.row_idx = row_idx


class StatusTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.download_rows: dict[str, _ProgressRow] = {}
        self.upload_rows: dict[str, _ProgressRow] = {}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 顶部概览
        self.summary_label = QLabel("下载中: 0 | 上传中: 0")
        self.summary_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(self.summary_label)

        # 下载区域
        download_group = QGroupBox("下载队列")
        download_layout = QVBoxLayout(download_group)
        self.download_table = QTableWidget()
        self.download_table.setColumnCount(4)
        self.download_table.setHorizontalHeaderLabels(["BV号", "标题", "UP主", "进度"])
        self.download_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.download_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.download_table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.download_table.horizontalHeader()
        header.setStretchLastSection(True)
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.download_table.setColumnWidth(0, 100)
        self.download_table.setColumnWidth(1, 280)
        self.download_table.setColumnWidth(2, 120)
        download_layout.addWidget(self.download_table)
        layout.addWidget(download_group)

        # 上传区域
        upload_group = QGroupBox("上传队列")
        upload_layout = QVBoxLayout(upload_group)
        upload_btn_layout = QHBoxLayout()
        self.upload_btn = QPushButton("开始上传")
        self.upload_btn.setStyleSheet("font-weight: bold; padding: 6px 16px;")
        upload_btn_layout.addWidget(self.upload_btn)
        upload_btn_layout.addStretch()
        upload_layout.addLayout(upload_btn_layout)

        self.upload_table = QTableWidget()
        self.upload_table.setColumnCount(2)
        self.upload_table.setHorizontalHeaderLabels(["文件名", "进度"])
        self.upload_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.upload_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.upload_table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.upload_table.horizontalHeader()
        header.setStretchLastSection(True)
        for col in range(2):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.upload_table.setColumnWidth(0, 500)
        upload_layout.addWidget(self.upload_table)
        layout.addWidget(upload_group)

    def _update_summary(self):
        self.summary_label.setText(
            f"下载中: {len(self.download_rows)} | 上传中: {len(self.upload_rows)}"
        )

    def on_download_started(self, bvid: str, title: str, uploader: str):
        if bvid in self.download_rows:
            return
        row_idx = self.download_table.rowCount()
        self.download_table.insertRow(row_idx)
        self.download_table.setItem(row_idx, 0, QTableWidgetItem(bvid))
        self.download_table.setItem(row_idx, 1, QTableWidgetItem(title))
        self.download_table.setItem(row_idx, 2, QTableWidgetItem(uploader))
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        progress.setTextVisible(True)
        self.download_table.setCellWidget(row_idx, 3, progress)
        self.download_rows[bvid] = _ProgressRow(bvid, title, progress, self.download_table, row_idx)
        self._update_summary()

    def on_download_progress(self, bvid: str, percent: int):
        row = self.download_rows.get(bvid)
        if row:
            row.progress.setValue(percent)

    def on_download_finished(self, bvid: str, success: bool, message: str):
        row = self.download_rows.pop(bvid, None)
        if row is None:
            return
        self.download_table.removeRow(row.row_idx)
        # 调整剩余行的索引
        for r in self.download_rows.values():
            if r.row_idx > row.row_idx:
                r.row_idx -= 1
        self._update_summary()

    def on_upload_started(self, file_name: str):
        if file_name in self.upload_rows:
            return
        row_idx = self.upload_table.rowCount()
        self.upload_table.insertRow(row_idx)
        self.upload_table.setItem(row_idx, 0, QTableWidgetItem(file_name))
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        progress.setTextVisible(True)
        self.upload_table.setCellWidget(row_idx, 1, progress)
        self.upload_rows[file_name] = _ProgressRow(file_name, file_name, progress, self.upload_table, row_idx)
        self._update_summary()

    def on_upload_progress(self, file_name: str, percent: int):
        row = self.upload_rows.get(file_name)
        if row:
            row.progress.setValue(percent)

    def on_upload_finished(self, file_name: str, success: bool, message: str):
        row = self.upload_rows.pop(file_name, None)
        if row is None:
            return
        self.upload_table.removeRow(row.row_idx)
        for r in self.upload_rows.values():
            if r.row_idx > row.row_idx:
                r.row_idx -= 1
        self._update_summary()
