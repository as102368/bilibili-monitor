import sys
import os
import asyncio
import logging
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QTextEdit,
    QPushButton,
    QLabel,
    QLineEdit,
    QSpinBox,
    QComboBox,
    QCheckBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QFileDialog,
    QGroupBox,
    QFormLayout,
    QSplitter,
    QDialog,
    QDateTimeEdit,
)
from PySide6.QtCore import Qt, QTimer, QDateTime

from ..config_loader import load_config, save_config
from ..monitor import BilibiliMonitor
from ..logger import setup_logging
from .log_handler import LogEmitter, GuiLogHandler
from .filename_template_builder import FilenameTemplateBuilder


class ConfigTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Cookie 配置
        cookie_group = QGroupBox("Cookie 配置")
        cookie_form = QFormLayout()
        self.sessdata_edit = QLineEdit()
        self.bili_jct_edit = QLineEdit()
        self.buvid3_edit = QLineEdit()
        self.dedeuserid_edit = QLineEdit()
        cookie_form.addRow("SESSDATA:", self.sessdata_edit)
        cookie_form.addRow("bili_jct:", self.bili_jct_edit)
        cookie_form.addRow("buvid3:", self.buvid3_edit)
        cookie_form.addRow("dedeuserid:", self.dedeuserid_edit)
        cookie_group.setLayout(cookie_form)
        layout.addWidget(cookie_group)

        # 监控配置
        monitor_group = QGroupBox("监控配置")
        monitor_form = QFormLayout()
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(10, 3600)
        self.interval_spin.setSuffix(" 秒")
        self.page_size_spin = QSpinBox()
        self.page_size_spin.setRange(1, 50)
        monitor_form.addRow("扫描间隔:", self.interval_spin)
        monitor_form.addRow("每次扫描数量:", self.page_size_spin)
        monitor_group.setLayout(monitor_form)
        layout.addWidget(monitor_group)

        # 城通网盘配置
        ctfile_group = QGroupBox("城通网盘配置")
        ctfile_form = QFormLayout()
        self.ctfile_session_edit = QLineEdit()
        self.ctfile_folder_edit = QLineEdit()
        self.ctfile_upload_check = QCheckBox("下载后自动上传")
        ctfile_form.addRow("Session Token:", self.ctfile_session_edit)
        ctfile_form.addRow("Folder ID:", self.ctfile_folder_edit)
        ctfile_form.addRow(self.ctfile_upload_check)
        ctfile_group.setLayout(ctfile_form)
        layout.addWidget(ctfile_group)

        # 保存按钮
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存配置")
        self.save_btn.setStyleSheet("font-weight: bold; padding: 6px 16px;")
        btn_layout.addStretch()
        btn_layout.addWidget(self.save_btn)
        layout.addLayout(btn_layout)

        layout.addStretch()

    def load_config(self, config: dict):
        cookie = config.get("cookie", {})
        self.sessdata_edit.setText(cookie.get("sessdata", ""))
        self.bili_jct_edit.setText(cookie.get("bili_jct", ""))
        self.buvid3_edit.setText(cookie.get("buvid3", ""))
        self.dedeuserid_edit.setText(cookie.get("dedeuserid", ""))

        monitor = config.get("monitor", {})
        self.interval_spin.setValue(monitor.get("interval", 60))
        self.page_size_spin.setValue(monitor.get("page_size", 5))

        ctfile = config.get("ctfile", {})
        self.ctfile_session_edit.setText(ctfile.get("session", ""))
        self.ctfile_folder_edit.setText(ctfile.get("folder_id", "0"))
        self.ctfile_upload_check.setChecked(ctfile.get("upload_after_download", False))

    def get_config(self) -> dict:
        return {
            "cookie": {
                "sessdata": self.sessdata_edit.text().strip(),
                "bili_jct": self.bili_jct_edit.text().strip(),
                "buvid3": self.buvid3_edit.text().strip(),
                "dedeuserid": self.dedeuserid_edit.text().strip(),
            },
            "monitor": {
                "interval": self.interval_spin.value(),
                "page_size": self.page_size_spin.value(),
            },
            "database": {
                "path": "./data/downloaded.db",
            },
            "ctfile": {
                "session": self.ctfile_session_edit.text().strip(),
                "folder_id": self.ctfile_folder_edit.text().strip() or "0",
                "upload_after_download": self.ctfile_upload_check.isChecked(),
            },
        }


class HistoryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.all_rows = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索 BV号/标题/UP主...")
        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self._on_search)
        self.search_edit.returnPressed.connect(self._on_search)
        self.clear_search_btn = QPushButton("清空")
        self.clear_search_btn.clicked.connect(self._on_clear_search)
        search_layout.addWidget(QLabel("搜索:"))
        search_layout.addWidget(self.search_edit, 1)
        search_layout.addWidget(self.search_btn)
        search_layout.addWidget(self.clear_search_btn)
        layout.addLayout(search_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["", "BV号", "标题", "UP主", "UP主ID", "画质", "下载时间"])
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setStretchLastSection(True)
        for col in range(7):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 30)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 240)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 80)
        self.table.setColumnWidth(5, 80)
        self.table.setColumnWidth(6, 160)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.select_all_checkbox = QCheckBox("全选")
        self.select_all_checkbox.clicked.connect(self._on_select_all_clicked)
        self.redownload_btn = QPushButton("重新下载选中")
        self.redownload_btn.setStyleSheet("background-color: #2196F3; color: white; padding: 6px 12px;")
        self.batch_delete_btn = QPushButton("批量删除选中")
        self.batch_delete_btn.setStyleSheet("background-color: #f44336; color: white; padding: 6px 12px;")
        self.refresh_btn = QPushButton("刷新")
        btn_layout.addWidget(self.select_all_checkbox)
        btn_layout.addWidget(self.redownload_btn)
        btn_layout.addWidget(self.batch_delete_btn)
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _on_search(self):
        keyword = self.search_edit.text().strip().lower()
        if not keyword:
            self._render_rows(self.all_rows)
            return
        filtered = [
            r for r in self.all_rows
            if keyword in str(r.get("bvid", "")).lower()
            or keyword in str(r.get("title", "")).lower()
            or keyword in str(r.get("uploader", "")).lower()
        ]
        self._render_rows(filtered)

    def _on_clear_search(self):
        self.search_edit.clear()
        self._render_rows(self.all_rows)

    def _on_select_all_clicked(self, checked: bool):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def set_data(self, rows: list):
        self.all_rows = rows
        self._render_rows(rows)

    def _render_rows(self, rows: list):
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked)
            self.table.setItem(i, 0, chk)
            self.table.setItem(i, 1, QTableWidgetItem(row.get("bvid", "")))
            self.table.setItem(i, 2, QTableWidgetItem(row.get("title", "")))
            self.table.setItem(i, 3, QTableWidgetItem(row.get("uploader", "")))
            self.table.setItem(i, 4, QTableWidgetItem(str(row.get("uploader_id", ""))))
            self.table.setItem(i, 5, QTableWidgetItem(row.get("quality", "")))
            self.table.setItem(i, 6, QTableWidgetItem(row.get("downloaded_at", "")))

    def get_selected_rows(self) -> list:
        result = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                result.append({
                    "bvid": self.table.item(row, 1).text() if self.table.item(row, 1) else "",
                    "title": self.table.item(row, 2).text() if self.table.item(row, 2) else "",
                    "uploader": self.table.item(row, 3).text() if self.table.item(row, 3) else "",
                })
        return result


class UploadTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.all_rows = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索 BV号/标题/文件名...")
        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self._on_search)
        self.search_edit.returnPressed.connect(self._on_search)
        self.clear_search_btn = QPushButton("清空")
        self.clear_search_btn.clicked.connect(self._on_clear_search)
        search_layout.addWidget(QLabel("搜索:"))
        search_layout.addWidget(self.search_edit, 1)
        search_layout.addWidget(self.search_btn)
        search_layout.addWidget(self.clear_search_btn)
        layout.addLayout(search_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels(
            ["", "ID", "BV号", "标题", "UP主", "文件名", "大小", "状态", "时间", "消息"]
        )
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setStretchLastSection(True)
        for col in range(10):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 30)
        self.table.setColumnWidth(1, 50)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 180)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 160)
        self.table.setColumnWidth(6, 80)
        self.table.setColumnWidth(7, 80)
        self.table.setColumnWidth(8, 160)
        self.table.setColumnWidth(9, 200)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.select_all_checkbox = QCheckBox("全选")
        self.select_all_checkbox.clicked.connect(self._on_select_all_clicked)
        self.batch_delete_btn = QPushButton("批量删除选中")
        self.batch_delete_btn.setStyleSheet("background-color: #f44336; color: white; padding: 6px 12px;")
        self.refresh_btn = QPushButton("刷新")
        btn_layout.addWidget(self.select_all_checkbox)
        btn_layout.addWidget(self.batch_delete_btn)
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _on_search(self):
        keyword = self.search_edit.text().strip().lower()
        if not keyword:
            self._render_rows(self.all_rows)
            return
        filtered = [
            r for r in self.all_rows
            if keyword in str(r.get("bvid", "")).lower()
            or keyword in str(r.get("title", "")).lower()
            or keyword in str(r.get("file_name", "")).lower()
        ]
        self._render_rows(filtered)

    def _on_clear_search(self):
        self.search_edit.clear()
        self._render_rows(self.all_rows)

    def _on_select_all_clicked(self, checked: bool):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def set_data(self, rows: list):
        self.all_rows = rows
        self._render_rows(rows)

    def _render_rows(self, rows: list):
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked)
            self.table.setItem(i, 0, chk)
            self.table.setItem(i, 1, QTableWidgetItem(str(row.get("id", ""))))
            self.table.setItem(i, 2, QTableWidgetItem(row.get("bvid", "")))
            self.table.setItem(i, 3, QTableWidgetItem(row.get("title", "")))
            self.table.setItem(i, 4, QTableWidgetItem(row.get("uploader", "")))
            self.table.setItem(i, 5, QTableWidgetItem(row.get("file_name", "")))
            size = row.get("file_size", 0)
            size_str = f"{size / 1024 / 1024:.1f} MB" if size else ""
            self.table.setItem(i, 6, QTableWidgetItem(size_str))
            status = row.get("status", "")
            status_display = {"success": "成功", "failed": "失败"}.get(status, status)
            self.table.setItem(i, 7, QTableWidgetItem(status_display))
            self.table.setItem(i, 8, QTableWidgetItem(row.get("uploaded_at", "")))
            self.table.setItem(i, 9, QTableWidgetItem(row.get("message", "")))

    def get_selected_rows(self) -> list:
        result = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                result.append({
                    "id": int(self.table.item(row, 1).text()) if self.table.item(row, 1) else 0,
                })
        return result


class FailureTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.all_rows = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索 BV号/标题/失败原因...")
        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self._on_search)
        self.search_edit.returnPressed.connect(self._on_search)
        self.clear_search_btn = QPushButton("清空")
        self.clear_search_btn.clicked.connect(self._on_clear_search)
        search_layout.addWidget(QLabel("搜索:"))
        search_layout.addWidget(self.search_edit, 1)
        search_layout.addWidget(self.search_btn)
        search_layout.addWidget(self.clear_search_btn)
        layout.addLayout(search_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(["", "ID", "BV号", "标题", "UP主", "失败原因", "时间", "状态"])
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setStretchLastSection(True)
        for col in range(8):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 30)
        self.table.setColumnWidth(1, 50)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 200)
        self.table.setColumnWidth(4, 100)
        self.table.setColumnWidth(5, 180)
        self.table.setColumnWidth(6, 160)
        self.table.setColumnWidth(7, 80)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.select_all_checkbox = QCheckBox("全选")
        self.select_all_checkbox.clicked.connect(self._on_select_all_clicked)
        self.refresh_btn = QPushButton("刷新")
        self.retry_btn = QPushButton("重试选中项")
        self.retry_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 6px 16px;")
        self.delete_btn = QPushButton("删除选中项")
        self.delete_btn.setStyleSheet("background-color: #f44336; color: white; padding: 6px 12px;")
        self.batch_delete_btn = QPushButton("批量删除选中")
        self.batch_delete_btn.setStyleSheet("background-color: #f44336; color: white; padding: 6px 12px;")
        btn_layout.addWidget(self.select_all_checkbox)
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addWidget(self.retry_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.batch_delete_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _on_search(self):
        keyword = self.search_edit.text().strip().lower()
        if not keyword:
            self._render_rows(self.all_rows)
            return
        filtered = [
            r for r in self.all_rows
            if keyword in str(r.get("bvid", "")).lower()
            or keyword in str(r.get("title", "")).lower()
            or keyword in str(r.get("reason", "")).lower()
        ]
        self._render_rows(filtered)

    def _on_clear_search(self):
        self.search_edit.clear()
        self._render_rows(self.all_rows)

    def _on_select_all_clicked(self, checked: bool):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def set_data(self, rows: list):
        self.all_rows = rows
        self._render_rows(rows)

    def _render_rows(self, rows: list):
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked)
            self.table.setItem(i, 0, chk)
            self.table.setItem(i, 1, QTableWidgetItem(str(row.get("id", ""))))
            self.table.setItem(i, 2, QTableWidgetItem(row.get("bvid", "")))
            self.table.setItem(i, 3, QTableWidgetItem(row.get("title", "")))
            self.table.setItem(i, 4, QTableWidgetItem(row.get("uploader", "")))
            self.table.setItem(i, 5, QTableWidgetItem(row.get("reason", "")))
            self.table.setItem(i, 6, QTableWidgetItem(row.get("created_at", "")))
            status = row.get("status", "")
            status_display = {
                "pending": "待重试",
                "retried": "已重试",
                "skipped": "跳过",
                "success": "成功",
                "failed": "失败",
            }.get(status, status)
            self.table.setItem(i, 7, QTableWidgetItem(status_display))

    def selected_row(self) -> dict | None:
        selected = self.table.selectedItems()
        if not selected:
            return None
        row_idx = selected[0].row()
        return {
            "id": self.table.item(row_idx, 1).text() if self.table.item(row_idx, 1) else "",
            "bvid": self.table.item(row_idx, 2).text() if self.table.item(row_idx, 2) else "",
            "title": self.table.item(row_idx, 3).text() if self.table.item(row_idx, 3) else "",
            "uploader": self.table.item(row_idx, 4).text() if self.table.item(row_idx, 4) else "",
            "reason": self.table.item(row_idx, 5).text() if self.table.item(row_idx, 5) else "",
        }

    def get_selected_rows(self) -> list:
        result = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                result.append({
                    "id": int(self.table.item(row, 1).text()) if self.table.item(row, 1) else 0,
                })
        return result


class CtfileDeduplicateTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.duplicate_groups = []
        self.all_rows = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel(
            "说明: 城通网盘 API 未提供 MD5，当前按「文件名 + 大小」判断重复，"
            "每组保留第一个文件，其余将被移至回收站。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.status_label = QLabel("状态: 等待扫描")
        self.status_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.status_label)

        search_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索 文件名/文件ID...")
        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self._on_search)
        self.search_edit.returnPressed.connect(self._on_search)
        self.clear_search_btn = QPushButton("清空")
        self.clear_search_btn.clicked.connect(self._on_clear_search)
        search_layout.addWidget(QLabel("搜索:"))
        search_layout.addWidget(self.search_edit, 1)
        search_layout.addWidget(self.search_btn)
        search_layout.addWidget(self.clear_search_btn)
        layout.addLayout(search_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["组号", "文件ID", "文件名", "大小", "日期", "状态"])
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setStretchLastSection(True)
        for col in range(6):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 240)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 140)
        self.table.setColumnWidth(5, 80)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        # 本地同步
        sync_group = QGroupBox("本地同步")
        sync_layout = QHBoxLayout(sync_group)
        self.sync_dir_edit = QLineEdit(r"D:\BI\bilibili-monitor\downloads")
        self.sync_browse_btn = QPushButton("浏览...")
        self.sync_browse_btn.clicked.connect(self._choose_sync_dir)
        self.sync_btn = QPushButton("同步本地文件")
        self.sync_btn.setStyleSheet("padding: 6px 16px;")
        sync_layout.addWidget(QLabel("本地目录:"))
        sync_layout.addWidget(self.sync_dir_edit, 1)
        sync_layout.addWidget(self.sync_browse_btn)
        sync_layout.addWidget(self.sync_btn)
        layout.addWidget(sync_group)

        btn_layout = QHBoxLayout()
        self.scan_btn = QPushButton("扫描重复文件")
        self.scan_btn.setStyleSheet("padding: 6px 16px;")
        self.delete_btn = QPushButton("删除重复文件")
        self.delete_btn.setEnabled(False)
        self.delete_btn.setStyleSheet(
            "background-color: #f44336; color: white; font-weight: bold; padding: 6px 16px;"
        )
        btn_layout.addWidget(self.scan_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _choose_sync_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择本地目录")
        if path:
            self.sync_dir_edit.setText(path)

    def _on_search(self):
        keyword = self.search_edit.text().strip().lower()
        if not keyword:
            self._render_rows(self.all_rows)
            return
        filtered = [
            r for r in self.all_rows
            if keyword in str(r.get("name", "")).lower()
            or keyword in str(r.get("key", "")).lower()
        ]
        self._render_rows(filtered)

    def _on_clear_search(self):
        self.search_edit.clear()
        self._render_rows(self.all_rows)

    def set_duplicates(self, groups: list):
        self.duplicate_groups = groups
        rows = []
        for idx, group in enumerate(groups, 1):
            for i, f in enumerate(group):
                status = "保留" if i == 0 else "将删除"
                rows.append(
                    {
                        "group": idx,
                        "key": f.get("key", ""),
                        "name": f.get("name", ""),
                        "size": f.get("size", 0),
                        "date": f.get("date", ""),
                        "status": status,
                    }
                )
        self.all_rows = rows
        self._render_rows(rows)

    def _render_rows(self, rows: list):
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(str(row["group"])))
            self.table.setItem(i, 1, QTableWidgetItem(row["key"]))
            self.table.setItem(i, 2, QTableWidgetItem(row["name"]))
            size = row["size"]
            size_str = f"{size / 1024 / 1024:.1f} MB" if size else ""
            self.table.setItem(i, 3, QTableWidgetItem(size_str))
            self.table.setItem(i, 4, QTableWidgetItem(str(row["date"])))
            self.table.setItem(i, 5, QTableWidgetItem(row["status"]))


class SettingsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 下载设置
        download_group = QGroupBox("下载设置")
        download_layout = QVBoxLayout(download_group)
        download_layout.setSpacing(10)

        download_form = QFormLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_btn = QPushButton("浏览...")
        self.output_dir_btn.clicked.connect(self._choose_dir)
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(self.output_dir_edit)
        dir_layout.addWidget(self.output_dir_btn)
        download_form.addRow("下载目录:", dir_layout)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["best", "8K", "4K", "1080P60", "1080P+", "1080P", "720P60", "720P", "480P", "360P"])
        download_form.addRow("画质:", self.quality_combo)

        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 5)
        self.concurrent_spin.setSuffix(" 个")
        download_form.addRow("同时下载数:", self.concurrent_spin)

        download_layout.addLayout(download_form)

        # 文件命名格式单独放置，避免 QFormLayout 压缩复杂组件
        self.filename_template_builder = FilenameTemplateBuilder()
        download_layout.addWidget(self.filename_template_builder)

        layout.addWidget(download_group)

        # 保存按钮
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存配置")
        self.save_btn.setStyleSheet("font-weight: bold; padding: 6px 16px;")
        btn_layout.addStretch()
        btn_layout.addWidget(self.save_btn)
        layout.addLayout(btn_layout)

        layout.addStretch()

    def _choose_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择下载目录")
        if path:
            self.output_dir_edit.setText(path)

    def load_config(self, config: dict):
        download = config.get("download", {})
        self.output_dir_edit.setText(download.get("output_dir", "./downloads"))
        self.quality_combo.setCurrentText(download.get("quality", "best"))
        self.filename_template_builder.set_template(
            download.get("filename_template", "%(uploader)s - %(title)s [%(id)s].%(ext)s")
        )
        self.filename_template_builder.set_time_format(
            download.get("time_format", "yyyy-MM-dd HH-mm-ss")
        )
        self.filename_template_builder.set_index_format(
            download.get("index_format", "自然数")
        )
        self.concurrent_spin.setValue(download.get("concurrent_downloads", 2))

    def get_config(self) -> dict:
        return {
            "download": {
                "output_dir": self.output_dir_edit.text().strip(),
                "quality": self.quality_combo.currentText(),
                "filename_template": self.filename_template_builder.get_template(),
                "time_format": self.filename_template_builder.get_time_format(),
                "index_format": self.filename_template_builder.get_index_format(),
                "concurrent_downloads": self.concurrent_spin.value(),
            }
        }


class BatchDownloadTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scanned_videos = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 扫描设置
        settings_group = QGroupBox("扫描设置")
        settings_layout = QFormLayout()

        self.start_time_edit = QDateTimeEdit()
        self.start_time_edit.setCalendarPopup(True)
        self.start_time_edit.setDateTime(QDateTime.currentDateTime().addDays(-7))
        self.start_time_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")

        self.end_time_edit = QDateTimeEdit()
        self.end_time_edit.setCalendarPopup(True)
        self.end_time_edit.setDateTime(QDateTime.currentDateTime())
        self.end_time_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")

        self.scan_type_combo = QComboBox()
        self.scan_type_combo.addItems(["动态", "UP主投稿", "两者"])
        self.scan_type_combo.currentTextChanged.connect(self._on_scan_type_changed)

        self.mid_edit = QLineEdit()
        self.mid_edit.setPlaceholderText("UP主MID（仅UP主投稿时需要）")
        self.mid_edit.setEnabled(False)

        settings_layout.addRow("开始时间:", self.start_time_edit)
        settings_layout.addRow("结束时间:", self.end_time_edit)
        settings_layout.addRow("扫描类型:", self.scan_type_combo)
        settings_layout.addRow("UP主MID:", self.mid_edit)
        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)

        # 按钮
        btn_layout = QHBoxLayout()
        self.scan_btn = QPushButton("扫描视频")
        self.scan_btn.setStyleSheet(
            "background-color: #2196F3; color: white; font-weight: bold; padding: 6px 16px;"
        )
        self.download_btn = QPushButton("下载选中")
        self.download_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 6px 16px;"
        )
        self.download_btn.setEnabled(False)
        self.select_all_checkbox = QCheckBox("全选")
        self.select_all_checkbox.setChecked(True)
        self.select_all_checkbox.clicked.connect(self._on_select_all_clicked)
        btn_layout.addWidget(self.scan_btn)
        btn_layout.addWidget(self.download_btn)
        btn_layout.addWidget(self.select_all_checkbox)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 结果表格
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["", "BV号", "标题", "UP主", "发布时间", "类型"])
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setStretchLastSection(True)
        for col in range(6):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 30)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 240)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 140)
        self.table.setColumnWidth(5, 60)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

    def _on_scan_type_changed(self, text: str):
        self.mid_edit.setEnabled(text != "动态")

    def _on_select_all_clicked(self, checked: bool):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def set_videos(self, videos: list):
        self.scanned_videos = videos
        self._render_videos()
        self.download_btn.setEnabled(bool(videos))

    def _render_videos(self):
        self.table.setRowCount(len(self.scanned_videos))
        for i, v in enumerate(self.scanned_videos):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked)
            self.table.setItem(i, 0, chk)
            self.table.setItem(i, 1, QTableWidgetItem(v.get("bvid", "")))
            self.table.setItem(i, 2, QTableWidgetItem(v.get("title", "")))
            self.table.setItem(i, 3, QTableWidgetItem(v.get("uname", "")))
            from datetime import datetime
            ts = v.get("pub_ts", 0)
            time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
            self.table.setItem(i, 4, QTableWidgetItem(time_str))
            self.table.setItem(i, 5, QTableWidgetItem(v.get("type", "")))
        self.table.resizeColumnsToContents()

    def get_selected_videos(self) -> list:
        result = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                result.append(self.scanned_videos[row])
        return result


class MainWindow(QMainWindow):
    def __init__(self, instance_running=False):
        super().__init__()
        self.setWindowTitle("Bilibili 动态监控")
        self.setMinimumSize(900, 600)

        self.monitor: BilibiliMonitor | None = None
        self.config = load_config() or {}
        self.instance_running = instance_running
        self.redownload_queue = []
        self.redownload_running = False

        self._build_ui()
        self._setup_logging()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # 顶部标题栏 + 登录按钮
        header_layout = QHBoxLayout()
        title_label = QLabel("Bilibili 动态监控")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.login_btn = QPushButton("扫码登录")
        self.login_btn.setStyleSheet("padding: 4px 14px;")
        self.login_btn.clicked.connect(self._on_qr_login)
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.login_btn)
        layout.addLayout(header_layout)

        self.tabs = QTabWidget()

        # 控制台页
        self.console_tab = QWidget()
        console_layout = QVBoxLayout(self.console_tab)
        self.status_label = QLabel("状态: 未启动")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        console_layout.addWidget(self.status_label)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        console_layout.addWidget(self.log_edit)

        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("启动监控")
        self.start_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 6px 16px;")
        self.stop_btn = QPushButton("停止监控")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 6px 16px;")
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.addStretch()
        console_layout.addLayout(btn_layout)

        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)

        if self.instance_running:
            self.status_label.setText("状态: 已有实例在运行")
            self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: orange;")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)

        self.tabs.addTab(self.console_tab, "控制台")

        # 配置页
        self.config_tab = ConfigTab()
        self.config_tab.load_config(self.config)
        self.config_tab.save_btn.clicked.connect(self._on_save_config)
        self.tabs.addTab(self.config_tab, "配置")

        # 下载历史页
        self.history_tab = HistoryTab()
        self.history_tab.refresh_btn.clicked.connect(self._refresh_history)
        self.history_tab.redownload_btn.clicked.connect(self._on_redownload_history)
        self.history_tab.batch_delete_btn.clicked.connect(self._on_batch_delete_history)
        self.tabs.addTab(self.history_tab, "下载历史")

        # 上传记录页
        self.upload_tab = UploadTab()
        self.upload_tab.refresh_btn.clicked.connect(self._refresh_uploads)
        self.upload_tab.batch_delete_btn.clicked.connect(self._on_batch_delete_upload)
        self.tabs.addTab(self.upload_tab, "上传记录")

        # 城通网盘去重页
        self.dedup_tab = CtfileDeduplicateTab()
        self.dedup_tab.scan_btn.clicked.connect(self._on_scan_duplicates)
        self.dedup_tab.delete_btn.clicked.connect(self._on_delete_duplicates)
        self.dedup_tab.sync_btn.clicked.connect(self._on_sync_local)
        self.tabs.addTab(self.dedup_tab, "网盘去重")

        # 失败记录页
        self.failure_tab = FailureTab()
        self.failure_tab.refresh_btn.clicked.connect(self._refresh_failures)
        self.failure_tab.retry_btn.clicked.connect(self._on_retry)
        self.failure_tab.delete_btn.clicked.connect(self._on_delete_failure)
        self.failure_tab.batch_delete_btn.clicked.connect(self._on_batch_delete_failure)
        self.tabs.addTab(self.failure_tab, "失败记录")

        # 批量下载页
        self.batch_tab = BatchDownloadTab()
        self.batch_tab.scan_btn.clicked.connect(self._on_batch_scan)
        self.batch_tab.download_btn.clicked.connect(self._on_batch_download)
        self.tabs.addTab(self.batch_tab, "批量下载")

        # 设置页
        self.settings_tab = SettingsTab()
        self.settings_tab.load_config(self.config)
        self.settings_tab.save_btn.clicked.connect(self._on_save_config)
        self.tabs.addTab(self.settings_tab, "设置")

        layout.addWidget(self.tabs)

    def _setup_logging(self):
        self.log_emitter = LogEmitter()
        self.log_emitter.log_signal.connect(self._append_log)
        handler = GuiLogHandler(self.log_emitter)
        setup_logging(level=logging.INFO, handler=handler)

    def _append_log(self, msg: str):
        self.log_edit.append(msg)
        # 限制日志行数，防止 QTextEdit 无限增长导致卡顿
        doc = self.log_edit.document()
        if doc.blockCount() > 3000:
            cursor = self.log_edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()
        sb = self.log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_save_config(self):
        cfg = self.config_tab.get_config()
        cfg.update(self.settings_tab.get_config())
        save_config(cfg)
        self.config = cfg
        QMessageBox.information(self, "保存成功", "配置已保存到 config.yaml")

    async def _init_monitor(self):
        cfg = self.config
        if not cfg.get("cookie", {}).get("sessdata") or not cfg.get("cookie", {}).get("bili_jct"):
            QMessageBox.warning(self, "配置不完整", "请先填写 SESSDATA 和 bili_jct")
            return False

        try:
            self.monitor = BilibiliMonitor(cfg)
            await self.monitor.init()
            return True
        except Exception as e:
            QMessageBox.critical(self, "启动失败", str(e))
            return False

    def _on_start(self):
        cfg = self.config_tab.get_config()
        cfg.update(self.settings_tab.get_config())
        self.config = cfg
        task = asyncio.create_task(self._do_start())
        task.add_done_callback(self._on_start_done)

    def _on_start_done(self, task):
        try:
            task.result()
        except Exception as e:
            logging.exception("启动监控失败")
            QMessageBox.critical(self, "启动失败", f"启动时发生错误:\n{e}")
            self.start_btn.setEnabled(True)

    async def _do_start(self):
        ok = await self._init_monitor()
        if not ok:
            return
        self.monitor.start()
        self.status_label.setText("状态: 运行中")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: green;")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _on_stop(self):
        if self.monitor:
            self.monitor.stop()
            self.monitor = None
        self.status_label.setText("状态: 已停止")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: red;")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _refresh_history(self):
        from ..database import DownloadDB
        db_path = self.config.get("database", {}).get("path", "./data/downloaded.db")
        db = DownloadDB(db_path)
        rows = db.get_downloaded_list()
        self.history_tab.set_data(rows)

    def _refresh_uploads(self):
        from ..database import DownloadDB
        db_path = self.config.get("database", {}).get("path", "./data/downloaded.db")
        db = DownloadDB(db_path)
        rows = db.get_upload_list()
        self.upload_tab.set_data(rows)

    def _refresh_failures(self):
        from ..database import DownloadDB
        db_path = self.config.get("database", {}).get("path", "./data/downloaded.db")
        db = DownloadDB(db_path)
        rows = db.get_failures()
        self.failure_tab.set_data(rows)

    def _on_retry(self):
        row = self.failure_tab.selected_row()
        if not row:
            QMessageBox.information(self, "提示", "请先选中一行")
            return
        asyncio.create_task(self._do_retry(row))

    async def _do_retry(self, row: dict):
        bvid = row.get("bvid", "")
        title = row.get("title", "")
        uname = row.get("uploader", "")
        failure_id = int(row.get("id", 0))

        from ..database import DownloadDB
        db_path = self.config.get("database", {}).get("path", "./data/downloaded.db")
        db = DownloadDB(db_path)

        # 检查失败重试次数
        failure_info = db.get_pending_failure_info(bvid)
        fail_count = failure_info.get("fail_count", 0)
        reason = failure_info.get("reason", "")
        if "充电专属" in reason:
            QMessageBox.information(self, "提示", f"{bvid} 为充电专属视频，不支持重试下载")
            db.mark_failure_skipped(bvid)
            self._refresh_failures()
            return
        if fail_count >= 5:
            QMessageBox.information(
                self, "已达最大重试次数",
                f"{bvid} 已达到最大重试次数 ({fail_count}/5)\n"
                f"失败原因: {reason}\n已停止自动重试，保留本地文件。"
            )
            return

        self.failure_tab.retry_btn.setEnabled(False)
        try:
            downloader = await self._get_downloader()
            if not downloader:
                QMessageBox.warning(self, "无法重试", "请先配置有效的 Cookie 并确保能登录")
                return

            result = await asyncio.to_thread(downloader.download, bvid, title, uname)

            if result.get("success"):
                db.delete_failure(failure_id)
                QMessageBox.information(self, "重试成功", f"{bvid} 下载成功")
            else:
                new_reason = result.get("reason", "")
                QMessageBox.warning(
                    self, "重试失败",
                    f"{bvid} 仍然下载失败\n失败原因: {new_reason}"
                )

            self._refresh_failures()
        finally:
            self.failure_tab.retry_btn.setEnabled(True)

    async def _get_downloader(self):
        if self.monitor and self.monitor.downloader:
            return self.monitor.downloader

        cfg = self.config_tab.get_config()
        cfg.update(self.settings_tab.get_config())
        if not cfg.get("cookie", {}).get("sessdata") or not cfg.get("cookie", {}).get("bili_jct"):
            return None

        try:
            temp_monitor = BilibiliMonitor(cfg)
            await temp_monitor.init()
            return temp_monitor.downloader
        except Exception:
            return None

    def _on_delete_failure(self):
        row = self.failure_tab.selected_row()
        if not row:
            QMessageBox.information(self, "提示", "请先选中一行")
            return
        reply = QMessageBox.question(self, "确认删除", "确定删除这条失败记录吗？")
        if reply != QMessageBox.Yes:
            return
        failure_id = int(row.get("id", 0))
        from ..database import DownloadDB
        db_path = self.config.get("database", {}).get("path", "./data/downloaded.db")
        db = DownloadDB(db_path)
        db.delete_failure(failure_id)
        self._refresh_failures()

    def _on_redownload_history(self):
        rows = self.history_tab.get_selected_rows()
        if not rows:
            QMessageBox.information(self, "提示", "请先勾选要重新下载的记录")
            return
        reply = QMessageBox.question(
            self, "确认重新下载",
            f"确定重新下载选中的 {len(rows)} 条记录吗？"
        )
        if reply != QMessageBox.Yes:
            return
        for row in rows:
            bvid = row.get("bvid", "")
            title = row.get("title", "")
            uname = row.get("uploader", "")
            if not bvid:
                continue
            from ..database import DownloadDB
            db_path = self.config.get("database", {}).get("path", "./data/downloaded.db")
            db = DownloadDB(db_path)
            if db.is_downloaded(bvid):
                logger.info(f"[History] {bvid} 已下载或已跳过，跳过")
                continue
            self.redownload_queue.append((bvid, title, uname))
        self.history_tab.select_all_checkbox.setChecked(False)
        if not self.redownload_running:
            asyncio.create_task(self._redownload_worker())

    async def _redownload_worker(self):
        self.redownload_running = True
        try:
            while self.redownload_queue:
                bvid, title, uname = self.redownload_queue.pop(0)
                from ..database import DownloadDB
                db_path = self.config.get("database", {}).get("path", "./data/downloaded.db")
                db = DownloadDB(db_path)
                if db.is_downloaded(bvid):
                    logger.info(f"[GUI] {bvid} 已下载或已跳过，跳过")
                    continue
                try:
                    downloader = await self._get_downloader()
                    if not downloader:
                        continue
                    result = await asyncio.to_thread(downloader.download, bvid, title, uname)
                    if result.get("success"):
                        self._refresh_history()
                    else:
                        reason = result.get("reason", "")
                        if "城通网盘上传失败" in reason:
                            logger.warning(f"[GUI] {bvid} 城通网盘上传失败，10 秒后自动重新下载")
                            await asyncio.sleep(10)
                            self.redownload_queue.append((bvid, title, uname))
                        elif "充电专属" in reason:
                            logger.info(f"[GUI] {bvid} 为充电专属视频，跳过")
                except Exception as e:
                    logger.error(f"[GUI] 重新下载失败 {bvid}: {e}")
        finally:
            self.redownload_running = False

    def _on_batch_delete_history(self):
        rows = self.history_tab.get_selected_rows()
        if not rows:
            QMessageBox.information(self, "提示", "请先勾选要删除的记录")
            return
        reply = QMessageBox.question(self, "确认删除", f"确定删除选中的 {len(rows)} 条下载记录吗？")
        if reply != QMessageBox.Yes:
            return
        bvids = [r["bvid"] for r in rows if r.get("bvid")]
        from ..database import DownloadDB
        db_path = self.config.get("database", {}).get("path", "./data/downloaded.db")
        db = DownloadDB(db_path)
        db.delete_downloaded_records(bvids)
        self._refresh_history()
        self.history_tab.select_all_checkbox.setChecked(False)

    def _on_batch_delete_upload(self):
        rows = self.upload_tab.get_selected_rows()
        if not rows:
            QMessageBox.information(self, "提示", "请先勾选要删除的记录")
            return
        reply = QMessageBox.question(self, "确认删除", f"确定删除选中的 {len(rows)} 条上传记录吗？")
        if reply != QMessageBox.Yes:
            return
        ids = [r["id"] for r in rows if r.get("id")]
        from ..database import DownloadDB
        db_path = self.config.get("database", {}).get("path", "./data/downloaded.db")
        db = DownloadDB(db_path)
        db.delete_upload_records(ids)
        self._refresh_uploads()
        self.upload_tab.select_all_checkbox.setChecked(False)

    def _on_batch_delete_failure(self):
        rows = self.failure_tab.get_selected_rows()
        if not rows:
            QMessageBox.information(self, "提示", "请先勾选要删除的记录")
            return
        reply = QMessageBox.question(self, "确认删除", f"确定删除选中的 {len(rows)} 条失败记录吗？")
        if reply != QMessageBox.Yes:
            return
        ids = [r["id"] for r in rows if r.get("id")]
        from ..database import DownloadDB
        db_path = self.config.get("database", {}).get("path", "./data/downloaded.db")
        db = DownloadDB(db_path)
        db.delete_failures(ids)
        self._refresh_failures()
        self.failure_tab.select_all_checkbox.setChecked(False)

    def _on_scan_duplicates(self):
        cfg = self.config_tab.get_config()
        ctfile_cfg = cfg.get("ctfile", {})
        session = ctfile_cfg.get("session", "")
        folder_id = ctfile_cfg.get("folder_id", "0")
        if not session:
            QMessageBox.warning(self, "配置不完整", "请先填写城通网盘 Session Token")
            return
        task = asyncio.create_task(self._do_scan_duplicates(session, folder_id))
        task.add_done_callback(self._on_async_error)

    async def _do_scan_duplicates(self, session: str, folder_id: str):
        self.dedup_tab.scan_btn.setEnabled(False)
        self.dedup_tab.status_label.setText("状态: 正在扫描...")
        try:
            from ..ctfile_uploader import CtfileUploader
            uploader = CtfileUploader(session, folder_id)
            groups = await asyncio.to_thread(uploader.find_duplicates)
            self.dedup_tab.set_duplicates(groups)
            total_dup = sum(len(g) - 1 for g in groups)
            self.dedup_tab.status_label.setText(
                f"状态: 发现 {len(groups)} 组重复文件，可删除 {total_dup} 个"
            )
            self.dedup_tab.delete_btn.setEnabled(bool(groups))
        except Exception as e:
            self.dedup_tab.status_label.setText(f"状态: 扫描失败 - {e}")
            QMessageBox.critical(self, "扫描失败", str(e))
        finally:
            self.dedup_tab.scan_btn.setEnabled(True)

    def _on_delete_duplicates(self):
        reply = QMessageBox.question(
            self,
            "确认删除",
            "确定删除所有重复文件吗？每组将保留第一个文件，其余移至回收站。",
        )
        if reply != QMessageBox.Yes:
            return
        cfg = self.config_tab.get_config()
        ctfile_cfg = cfg.get("ctfile", {})
        session = ctfile_cfg.get("session", "")
        folder_id = ctfile_cfg.get("folder_id", "0")
        task = asyncio.create_task(self._do_delete_duplicates(session, folder_id))
        task.add_done_callback(self._on_async_error)

    async def _do_delete_duplicates(self, session: str, folder_id: str):
        self.dedup_tab.delete_btn.setEnabled(False)
        self.dedup_tab.scan_btn.setEnabled(False)
        self.dedup_tab.status_label.setText("状态: 正在删除...")
        try:
            from ..ctfile_uploader import CtfileUploader
            uploader = CtfileUploader(session, folder_id)
            groups = self.dedup_tab.duplicate_groups
            deleted = await asyncio.to_thread(uploader.remove_duplicates, groups)
            # 重新扫描
            groups = await asyncio.to_thread(uploader.find_duplicates)
            self.dedup_tab.set_duplicates(groups)
            total_dup = sum(len(g) - 1 for g in groups)
            if total_dup > 0:
                self.dedup_tab.status_label.setText(
                    f"状态: 已删除 {deleted} 个，仍剩余 {len(groups)} 组重复"
                )
                self.dedup_tab.delete_btn.setEnabled(True)
            else:
                self.dedup_tab.status_label.setText(
                    f"状态: 已删除 {deleted} 个，当前无重复文件"
                )
                self.dedup_tab.delete_btn.setEnabled(False)
        except Exception as e:
            self.dedup_tab.status_label.setText(f"状态: 删除失败 - {e}")
            QMessageBox.critical(self, "删除失败", str(e))
        finally:
            self.dedup_tab.scan_btn.setEnabled(True)

    def _on_sync_local(self):
        cfg = self.config_tab.get_config()
        ctfile_cfg = cfg.get("ctfile", {})
        session = ctfile_cfg.get("session", "")
        folder_id = ctfile_cfg.get("folder_id", "0")
        if not session:
            QMessageBox.warning(self, "配置不完整", "请先填写城通网盘 Session Token")
            return
        local_dir = self.dedup_tab.sync_dir_edit.text().strip()
        if not os.path.isdir(local_dir):
            QMessageBox.warning(self, "目录无效", f"本地目录不存在:\n{local_dir}")
            return
        task = asyncio.create_task(self._do_sync_local(session, folder_id, local_dir))
        task.add_done_callback(self._on_async_error)

    async def _do_sync_local(self, session: str, folder_id: str, local_dir: str):
        self.dedup_tab.sync_btn.setEnabled(False)
        self.dedup_tab.status_label.setText("状态: 正在同步本地文件...")
        try:
            from ..ctfile_uploader import CtfileUploader
            uploader = CtfileUploader(session, folder_id)
            stats = await asyncio.to_thread(uploader.sync_local_folder, local_dir)
            self.dedup_tab.status_label.setText(
                f"状态: 同步完成 — 上传 {stats['uploaded']} 个，"
                f"直接删除 {stats['deleted']} 个，失败 {stats['failed']} 个"
            )
        except Exception as e:
            self.dedup_tab.status_label.setText(f"状态: 同步失败 - {e}")
            QMessageBox.critical(self, "同步失败", str(e))
        finally:
            self.dedup_tab.sync_btn.setEnabled(True)

    def _on_async_error(self, task):
        try:
            task.result()
        except Exception:
            logging.exception("异步任务异常")

    def _on_batch_scan(self):
        cfg = self.config_tab.get_config()
        cfg.update(self.settings_tab.get_config())
        self.config = cfg
        if not cfg.get("cookie", {}).get("sessdata") or not cfg.get("cookie", {}).get("bili_jct"):
            QMessageBox.warning(self, "配置不完整", "请先填写 SESSDATA 和 bili_jct")
            return
        task = asyncio.create_task(self._do_batch_scan())
        task.add_done_callback(self._on_async_error)

    async def _do_batch_scan(self):
        self.batch_tab.scan_btn.setEnabled(False)
        self.batch_tab.download_btn.setEnabled(False)
        try:
            start_dt = self.batch_tab.start_time_edit.dateTime().toPython()
            end_dt = self.batch_tab.end_time_edit.dateTime().toPython()
            start_ts = int(start_dt.timestamp())
            end_ts = int(end_dt.timestamp())

            if start_ts >= end_ts:
                QMessageBox.warning(self, "时间错误", "开始时间必须早于结束时间")
                return

            scan_type = self.batch_tab.scan_type_combo.currentText()
            mid_text = self.batch_tab.mid_edit.text().strip()
            mid = int(mid_text) if mid_text else 0

            # 初始化临时 monitor 用于扫描
            temp_monitor = BilibiliMonitor(self.config)
            await temp_monitor.init()

            all_videos = []
            if scan_type in ("动态", "两者"):
                self.status_label.setText("状态: 正在扫描动态...")
                dynamic_videos = await temp_monitor.fetch_dynamics_in_range(start_ts, end_ts)
                all_videos.extend(dynamic_videos)

            if scan_type in ("UP主投稿", "两者"):
                if not mid:
                    QMessageBox.warning(self, "参数缺失", "扫描UP主投稿时需要填写UP主MID")
                    return
                self.status_label.setText("状态: 正在扫描UP主投稿...")
                user_videos = await temp_monitor.fetch_user_videos_in_range(mid, start_ts, end_ts)
                all_videos.extend(user_videos)

            # 去重
            seen = set()
            unique_videos = []
            for v in all_videos:
                bvid = v.get("bvid")
                if bvid and bvid not in seen:
                    seen.add(bvid)
                    unique_videos.append(v)

            self.batch_tab.set_videos(unique_videos)
            self.status_label.setText(f"状态: 扫描完成，发现 {len(unique_videos)} 个视频")
            QMessageBox.information(
                self, "扫描完成",
                f"在指定时间段内发现 {len(unique_videos)} 个视频"
            )
        except Exception as e:
            self.status_label.setText(f"状态: 扫描失败")
            QMessageBox.critical(self, "扫描失败", str(e))
        finally:
            self.batch_tab.scan_btn.setEnabled(True)

    def _on_batch_download(self):
        videos = self.batch_tab.get_selected_videos()
        if not videos:
            QMessageBox.information(self, "提示", "请先勾选要下载的视频")
            return
        reply = QMessageBox.question(
            self, "确认下载",
            f"确定下载选中的 {len(videos)} 个视频吗？"
        )
        if reply != QMessageBox.Yes:
            return
        for v in videos:
            bvid = v.get("bvid", "")
            title = v.get("title", "")
            uname = v.get("uname", "")
            if not bvid:
                continue
            from ..database import DownloadDB
            db_path = self.config.get("database", {}).get("path", "./data/downloaded.db")
            db = DownloadDB(db_path)
            if db.is_downloaded(bvid):
                logger.info(f"[Batch] {bvid} 已下载或已跳过，跳过")
                continue
            self.redownload_queue.append((bvid, title, uname))
        if not self.redownload_running:
            asyncio.create_task(self._redownload_worker())

    def _on_qr_login(self):
        from .qr_login_dialog import QrLoginDialog
        dlg = QrLoginDialog(self)
        if dlg.exec() == QDialog.Accepted:
            cookies = dlg.get_cookies()
            cookie_cfg = self.config.setdefault("cookie", {})
            cookie_cfg["sessdata"] = cookies.get("SESSDATA", "")
            cookie_cfg["bili_jct"] = cookies.get("bili_jct", "")
            cookie_cfg["dedeuserid"] = cookies.get("DedeUserID", "")
            cookie_cfg["buvid3"] = cookies.get("buvid3", "")
            save_config(self.config)
            self.config_tab.load_config(self.config)
            self.settings_tab.load_config(self.config)
            QMessageBox.information(self, "登录成功", "Cookie 已自动保存到配置")

    def closeEvent(self, event):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if self.monitor:
            try:
                self.monitor.stop()
            except Exception:
                pass
            self.monitor = None
        if app:
            app.quit()
        event.accept()
