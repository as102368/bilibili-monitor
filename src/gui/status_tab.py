"""
运行状态页：展示当前下载/上传任务、进度条与当前文件。
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QProgressBar,
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
        layout.setSpacing(12)
        layout.setContentsMargins(14, 14, 14, 14)

        # 后台任务控制面板
        control_group = QGroupBox("后台任务控制")
        control_layout = QGridLayout(control_group)
        control_layout.setSpacing(10)
        control_layout.setContentsMargins(12, 10, 12, 10)

        # 动态监控
        control_layout.addWidget(QLabel("动态监控:"), 0, 0)
        self.monitor_status_label = QLabel("已停止")
        self.monitor_status_label.setStyleSheet("color: #f44336; font-weight: bold;")
        control_layout.addWidget(self.monitor_status_label, 0, 1)
        self.monitor_start_btn = QPushButton("启动监控")
        self.monitor_start_btn.setObjectName("success_btn")
        control_layout.addWidget(self.monitor_start_btn, 0, 2)
        self.monitor_stop_btn = QPushButton("停止监控")
        self.monitor_stop_btn.setEnabled(False)
        self.monitor_stop_btn.setObjectName("danger_btn")
        control_layout.addWidget(self.monitor_stop_btn, 0, 3)
        control_layout.setColumnStretch(4, 1)

        # 下载后自动上传
        control_layout.addWidget(QLabel("自动上传:"), 1, 0)
        self.auto_upload_status_label = QLabel("已关闭")
        self.auto_upload_status_label.setStyleSheet("color: #888888; font-weight: bold;")
        control_layout.addWidget(self.auto_upload_status_label, 1, 1)
        self.auto_upload_btn = QPushButton("启用自动上传")
        self.auto_upload_btn.setObjectName("primary_btn")
        control_layout.addWidget(self.auto_upload_btn, 1, 2)
        control_layout.setColumnStretch(4, 1)

        layout.addWidget(control_group)

        # 顶部概览
        self.summary_label = QLabel("下载中: 0 | 上传中: 0")
        self.summary_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(self.summary_label)

        self.last_check_label = QLabel("上次扫描: --")
        self.last_check_label.setStyleSheet("font-size: 12px; color: #888888;")
        layout.addWidget(self.last_check_label)

        # 下载区域
        download_group = QGroupBox("下载队列")
        download_layout = QVBoxLayout(download_group)
        download_layout.setSpacing(8)
        download_layout.setContentsMargins(10, 8, 10, 10)
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
        upload_layout.setSpacing(8)
        upload_layout.setContentsMargins(10, 8, 10, 10)
        upload_btn_layout = QHBoxLayout()
        self.upload_btn = QPushButton("开始上传")
        self.upload_btn.setObjectName("primary_btn")
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
        pending = getattr(self, "_pending_upload_count", 0)
        self.summary_label.setText(
            f"下载中: {len(self.download_rows)} | 上传中: {len(self.upload_rows)} | 待上传: {pending}"
        )

    def set_pending_upload_count(self, count: int):
        self._pending_upload_count = count
        self._update_summary()

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

    def set_monitor_status(self, running: bool):
        """更新动态监控状态显示与按钮可用性。"""
        if running:
            self.monitor_status_label.setText("运行中")
            self.monitor_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
            self.monitor_start_btn.setEnabled(False)
            self.monitor_stop_btn.setEnabled(True)
        else:
            self.monitor_status_label.setText("已停止")
            self.monitor_status_label.setStyleSheet("color: #f44336; font-weight: bold;")
            self.monitor_start_btn.setEnabled(True)
            self.monitor_stop_btn.setEnabled(False)

    def set_last_check_time(self, ts: str):
        """更新上次扫描时间显示。"""
        self.last_check_label.setText(f"上次扫描: {ts}" if ts else "上次扫描: --")

    def set_auto_upload_enabled(self, enabled: bool, ready: bool = True):
        """更新自动上传状态显示与按钮文案。"""
        if enabled:
            self.auto_upload_status_label.setText("已启用" if ready else "已启用（未配置）")
            self.auto_upload_status_label.setStyleSheet(
                "color: #4CAF50; font-weight: bold;" if ready else "color: #ff9800; font-weight: bold;"
            )
            self.auto_upload_btn.setText("关闭自动上传")
        else:
            self.auto_upload_status_label.setText("已关闭")
            self.auto_upload_status_label.setStyleSheet("color: #888888; font-weight: bold;")
            self.auto_upload_btn.setText("启用自动上传")
