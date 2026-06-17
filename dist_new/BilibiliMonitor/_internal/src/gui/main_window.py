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
)
from PySide6.QtCore import Qt, QTimer

from ..config_loader import load_config, save_config
from ..monitor import BilibiliMonitor
from ..logger import setup_logging
from .log_handler import LogEmitter, GuiLogHandler


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

        # 下载配置
        download_group = QGroupBox("下载配置")
        download_form = QFormLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_btn = QPushButton("浏览...")
        self.output_dir_btn.clicked.connect(self._choose_dir)
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(self.output_dir_edit)
        dir_layout.addWidget(self.output_dir_btn)
        download_form.addRow("下载目录:", dir_layout)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["best", "4K", "1080P60", "1080P+", "1080P", "720P", "480P", "360P"])
        download_form.addRow("画质:", self.quality_combo)

        self.filename_template_edit = QLineEdit()
        download_form.addRow("文件名模板:", self.filename_template_edit)
        download_group.setLayout(download_form)
        layout.addWidget(download_group)

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

    def _choose_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择下载目录")
        if path:
            self.output_dir_edit.setText(path)

    def load_config(self, config: dict):
        cookie = config.get("cookie", {})
        self.sessdata_edit.setText(cookie.get("sessdata", ""))
        self.bili_jct_edit.setText(cookie.get("bili_jct", ""))
        self.buvid3_edit.setText(cookie.get("buvid3", ""))
        self.dedeuserid_edit.setText(cookie.get("dedeuserid", ""))

        monitor = config.get("monitor", {})
        self.interval_spin.setValue(monitor.get("interval", 60))
        self.page_size_spin.setValue(monitor.get("page_size", 5))

        download = config.get("download", {})
        self.output_dir_edit.setText(download.get("output_dir", "./downloads"))
        self.quality_combo.setCurrentText(download.get("quality", "best"))
        self.filename_template_edit.setText(
            download.get("filename_template", "%(uploader)s - %(title)s [%(id)s].%(ext)s")
        )

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
            "download": {
                "output_dir": self.output_dir_edit.text().strip(),
                "quality": self.quality_combo.currentText(),
                "filename_template": self.filename_template_edit.text().strip(),
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
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["BV号", "标题", "UP主", "UP主ID", "画质", "下载时间"])
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setStretchLastSection(False)
        for col in range(6):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 100)
        self.table.setColumnWidth(1, 240)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 80)
        self.table.setColumnWidth(5, 160)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        self.refresh_btn = QPushButton("刷新")
        layout.addWidget(self.refresh_btn)

    def set_data(self, rows: list):
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(row.get("bvid", "")))
            self.table.setItem(i, 1, QTableWidgetItem(row.get("title", "")))
            self.table.setItem(i, 2, QTableWidgetItem(row.get("uploader", "")))
            self.table.setItem(i, 3, QTableWidgetItem(str(row.get("uploader_id", ""))))
            self.table.setItem(i, 4, QTableWidgetItem(row.get("quality", "")))
            self.table.setItem(i, 5, QTableWidgetItem(row.get("downloaded_at", "")))


class UploadTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["ID", "BV号", "标题", "UP主", "文件名", "大小", "状态", "时间", "消息"]
        )
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setStretchLastSection(False)
        for col in range(9):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 180)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 160)
        self.table.setColumnWidth(5, 80)
        self.table.setColumnWidth(6, 80)
        self.table.setColumnWidth(7, 160)
        self.table.setColumnWidth(8, 200)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        self.refresh_btn = QPushButton("刷新")
        layout.addWidget(self.refresh_btn)

    def set_data(self, rows: list):
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(str(row.get("id", ""))))
            self.table.setItem(i, 1, QTableWidgetItem(row.get("bvid", "")))
            self.table.setItem(i, 2, QTableWidgetItem(row.get("title", "")))
            self.table.setItem(i, 3, QTableWidgetItem(row.get("uploader", "")))
            self.table.setItem(i, 4, QTableWidgetItem(row.get("file_name", "")))
            size = row.get("file_size", 0)
            size_str = f"{size / 1024 / 1024:.1f} MB" if size else ""
            self.table.setItem(i, 5, QTableWidgetItem(size_str))
            self.table.setItem(i, 6, QTableWidgetItem(row.get("status", "")))
            self.table.setItem(i, 7, QTableWidgetItem(row.get("uploaded_at", "")))
            self.table.setItem(i, 8, QTableWidgetItem(row.get("message", "")))


class FailureTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["ID", "BV号", "标题", "UP主", "失败原因", "时间", "状态"])
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setStretchLastSection(False)
        for col in range(7):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 200)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 180)
        self.table.setColumnWidth(5, 160)
        self.table.setColumnWidth(6, 80)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新")
        self.retry_btn = QPushButton("重试选中项")
        self.retry_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 6px 16px;")
        self.delete_btn = QPushButton("删除选中项")
        self.delete_btn.setStyleSheet("background-color: #f44336; color: white; padding: 6px 12px;")
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addWidget(self.retry_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def set_data(self, rows: list):
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(str(row.get("id", ""))))
            self.table.setItem(i, 1, QTableWidgetItem(row.get("bvid", "")))
            self.table.setItem(i, 2, QTableWidgetItem(row.get("title", "")))
            self.table.setItem(i, 3, QTableWidgetItem(row.get("uploader", "")))
            self.table.setItem(i, 4, QTableWidgetItem(row.get("reason", "")))
            self.table.setItem(i, 5, QTableWidgetItem(row.get("created_at", "")))
            self.table.setItem(i, 6, QTableWidgetItem(row.get("status", "")))

    def selected_row(self) -> dict | None:
        selected = self.table.selectedItems()
        if not selected:
            return None
        row_idx = selected[0].row()
        return {
            "id": self.table.item(row_idx, 0).text(),
            "bvid": self.table.item(row_idx, 1).text(),
            "title": self.table.item(row_idx, 2).text(),
            "uploader": self.table.item(row_idx, 3).text(),
            "reason": self.table.item(row_idx, 4).text(),
        }


class MainWindow(QMainWindow):
    def __init__(self, instance_running=False):
        super().__init__()
        self.setWindowTitle("Bilibili 动态监控")
        self.setMinimumSize(900, 600)

        self.monitor: BilibiliMonitor | None = None
        self.config = load_config("config.yaml") or {}
        self.instance_running = instance_running

        self._build_ui()
        self._setup_logging()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

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
        self.tabs.addTab(self.history_tab, "下载历史")

        # 上传记录页
        self.upload_tab = UploadTab()
        self.upload_tab.refresh_btn.clicked.connect(self._refresh_uploads)
        self.tabs.addTab(self.upload_tab, "上传记录")

        # 失败记录页
        self.failure_tab = FailureTab()
        self.failure_tab.refresh_btn.clicked.connect(self._refresh_failures)
        self.failure_tab.retry_btn.clicked.connect(self._on_retry)
        self.failure_tab.delete_btn.clicked.connect(self._on_delete_failure)
        self.tabs.addTab(self.failure_tab, "失败记录")

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
        save_config(cfg, "config.yaml")
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
        self.config = self.config_tab.get_config()
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

        self.failure_tab.retry_btn.setEnabled(False)
        try:
            downloader = await self._get_downloader()
            if not downloader:
                QMessageBox.warning(self, "无法重试", "请先配置有效的 Cookie 并确保能登录")
                return

            result = await asyncio.to_thread(downloader.download, bvid, title, uname)

            from ..database import DownloadDB
            db_path = self.config.get("database", {}).get("path", "./data/downloaded.db")
            db = DownloadDB(db_path)

            if result.get("success"):
                db.delete_failure(failure_id)
                QMessageBox.information(self, "重试成功", f"{bvid} 下载成功")
            else:
                QMessageBox.warning(self, "重试失败", f"{bvid} 仍然下载失败，已记录新的失败原因")

            self._refresh_failures()
        finally:
            self.failure_tab.retry_btn.setEnabled(True)

    async def _get_downloader(self):
        if self.monitor and self.monitor.downloader:
            return self.monitor.downloader

        cfg = self.config_tab.get_config()
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

    def closeEvent(self, event):
        if self.monitor:
            self.monitor.stop()
        event.accept()
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.quit()
