"""
用户中心二级页面：展示关注、关注分组、收藏、稍后再看、历史记录、订阅，
并支持查看 UP 主/收藏夹视频列表与批量下载。
"""
import asyncio
from datetime import datetime
from typing import List, Dict, Optional, Callable

import requests
from PySide6.QtWidgets import (
    QDialog,
    QBoxLayout,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QSizePolicy,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QStackedWidget,
    QCheckBox,
    QSplitter,
    QTextEdit,
)
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QPixmap, QIcon, QPainter, QPainterPath

from ..logger import get_logger
from ..wbi import WBI
from ..database import DownloadDB

logger = get_logger(__name__)


def _ts_to_str(ts: int) -> str:
    """秒级时间戳转本地时间字符串"""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _get_video_status(db: DownloadDB, bvid: str) -> str:
    """返回视频在本地数据库中的状态：已存档 / 已存档（试看） / 失败原因 / 空"""
    if not bvid:
        return ""
    if db.is_downloaded(bvid):
        downloaded = db.get_downloaded_by_bvid(bvid)
        if downloaded and downloaded.get("is_preview"):
            return "已存档（试看）"
        return "已存档"
    failure = db.get_failure_by_bvid(bvid)
    if failure:
        reason = failure.get("reason", "")
        if "充电专属" in reason:
            return "充电专属"
        if "付费" in reason:
            return "付费视频"
        if "不存在" in reason or "已删除" in reason:
            return "已失效"
        return reason or "下载失败"
    return ""


class UserCenterDialog(QWidget):
    """用户中心页面（嵌入主窗口使用）"""

    def __init__(
        self,
        web_client,
        db_path: str,
        download_callback: Callable[[List[Dict]], None],
        parent=None,
        download_all_callback: Optional[Callable[[List[Dict]], None]] = None,
    ):
        super().__init__(parent)
        self.setMinimumSize(800, 650)
        self.resize(1400, 850)
        self.web = web_client
        self.wbi = WBI(web_client.sessdata)
        self.db_path = db_path
        self.db = DownloadDB(db_path)
        self.download_callback = download_callback
        self.download_all_callback = download_all_callback
        self.user_info: Dict = {}
        self.current_videos: List[Dict] = []
        self.user_mid: int = 0

        self._build_ui()
        # 延迟加载数据，避免在创建/切换页面时阻塞主线程
        QTimer.singleShot(0, self._load_user_info)

    def _build_ui(self):
        self._left_sidebar = self._build_sidebar()
        self._right_stack = self._build_stack()

        self._main_layout = QHBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)
        self._main_layout.addWidget(self._left_sidebar)
        self._main_layout.addWidget(self._right_stack, 1)

    def _build_sidebar(self) -> QWidget:
        """左侧导航栏：固定宽度，仅保留功能入口列表。"""
        left_widget = QWidget()
        left_widget.setFixedWidth(180)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(12, 16, 12, 16)
        left_layout.setSpacing(12)

        self.nav_list = QListWidget()
        self.nav_list.addItem("我的关注")
        self.nav_list.addItem("关注分组")
        self.nav_list.addItem("收藏列表")
        self.nav_list.addItem("稍后再看")
        self.nav_list.addItem("历史记录")
        self.nav_list.addItem("我的订阅")
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        self.nav_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.nav_list.setStyleSheet(
            "QListWidget { border: none; border-radius: 6px; background-color: #2a2a2a; color: #eeeeee; outline: none; }"
            "QListWidget::item { padding: 12px 14px; border-radius: 6px; }"
            "QListWidget::item:hover { background-color: #3a3a3a; }"
            "QListWidget::item:selected { background-color: #1E88E5; }"
        )
        left_layout.addWidget(self.nav_list)
        left_layout.addStretch()

        return left_widget

    def _build_stack(self) -> QStackedWidget:
        """右侧主内容区：堆叠多个二级页面。"""
        self.stack = QStackedWidget()

        self.follow_page = self._build_follow_page()
        self.stack.addWidget(self.follow_page)

        self.group_page = self._build_group_page()
        self.stack.addWidget(self.group_page)

        self.fav_page = self._build_fav_page()
        self.stack.addWidget(self.fav_page)

        self.watchlater_page = self._build_video_list_page("稍后再看")
        self.stack.addWidget(self.watchlater_page)

        self.history_page = self._build_video_list_page("历史记录")
        self.stack.addWidget(self.history_page)

        self.subscription_page = self._build_subscription_page()
        self.stack.addWidget(self.subscription_page)

        return self.stack

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_group_area_height()

    def _update_group_area_height(self):
        """分组列表区域最大高度随窗口高度动态调整（约占右侧区域的 40%）。"""
        if not hasattr(self, "_group_area"):
            return
        available = max(self._right_stack.height() - 120, 200)
        self._group_area.setMaximumHeight(int(available * 0.45))

    def _build_follow_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 顶部标题栏
        top = QHBoxLayout()
        title = QLabel("我的关注")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        top.addWidget(title)
        top.addStretch()
        self.follow_count_label = QLabel("共 0 位 UP主")
        self.follow_count_label.setStyleSheet("color: #aaaaaa; font-size: 13px;")
        top.addWidget(self.follow_count_label)
        refresh_btn = QPushButton("刷新")
        refresh_btn.setMinimumHeight(32)
        refresh_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffffff; border: 1px solid #555; border-radius: 4px; padding: 0 14px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        refresh_btn.clicked.connect(self._load_followings)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        # 关注表格：占满剩余高度，内容超出时滚动
        self.follow_table = QTableWidget()
        self.follow_table.setColumnCount(3)
        self.follow_table.setHorizontalHeaderLabels(["", "昵称", "操作"])
        self.follow_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.follow_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.follow_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.follow_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.follow_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.follow_table.setStyleSheet(
            "QTableWidget { border: 1px solid #3a3a3a; border-radius: 6px; background-color: #252525; gridline-color: #333; }"
            "QHeaderView::section { background-color: #2a2a2a; color: #ffffff; padding: 6px; border: none; border-bottom: 1px solid #3a3a3a; }"
            "QTableWidget::item { padding: 6px; color: #eeeeee; }"
            "QTableWidget::item:selected { background-color: #1E88E5; }"
        )
        follow_header = self.follow_table.horizontalHeader()
        follow_header.setSectionResizeMode(0, QHeaderView.Fixed)
        follow_header.setSectionResizeMode(1, QHeaderView.Stretch)
        follow_header.setSectionResizeMode(2, QHeaderView.Fixed)
        follow_header.setDefaultAlignment(Qt.AlignLeft)
        self.follow_table.setColumnWidth(0, 50)
        self.follow_table.setColumnWidth(2, 90)
        self.follow_table.verticalHeader().setVisible(False)
        layout.addWidget(self.follow_table, 1)

        # 底部操作栏
        bottom = QWidget()
        bottom.setMinimumHeight(52)
        bottom.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        bottom.setStyleSheet(
            "QWidget { background-color: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 8px; }"
            "QCheckBox { color: #eeeeee; font-size: 13px; spacing: 6px; }"
            "QCheckBox::indicator { width: 16px; height: 16px; }"
        )
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(14, 10, 14, 10)
        self.follow_select_all = QCheckBox("全选")
        self.follow_select_all.clicked.connect(self._on_follow_select_all)
        bottom_layout.addWidget(self.follow_select_all)
        bottom_layout.addStretch()
        self.follow_selected_count = QLabel("已选 0 位")
        self.follow_selected_count.setStyleSheet("color: #aaaaaa; font-size: 13px;")
        bottom_layout.addWidget(self.follow_selected_count)
        bottom_layout.addSpacing(12)
        download_btn = QPushButton("下载选中UP主的全部视频")
        download_btn.setMinimumHeight(38)
        download_btn.setMinimumWidth(220)
        download_btn.setCursor(Qt.PointingHandCursor)
        download_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; border-radius: 6px; padding: 0 20px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #1976D2; }"
            "QPushButton:pressed { background-color: #1565C0; }"
            "QPushButton:disabled { background-color: #555; color: #aaa; }"
        )
        download_btn.clicked.connect(self._on_download_selected_follows)
        bottom_layout.addWidget(download_btn)
        layout.addWidget(bottom)

        # 选中变化时更新计数（行拖拽 + 复选框点击）
        self.follow_table.itemSelectionChanged.connect(self._update_follow_selection_count)
        self.follow_table.itemChanged.connect(self._update_follow_selection_count)

        return page

    def _build_group_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 顶部标题栏
        top = QHBoxLayout()
        title = QLabel("关注分组")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        top.addWidget(title)
        top.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setMinimumHeight(32)
        refresh_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffffff; border: 1px solid #555; border-radius: 4px; padding: 0 14px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        refresh_btn.clicked.connect(self._load_follow_groups)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        # 上方分组列表区域：高度自适应并自带滚动条，防止挤压下方表格
        self._group_area = QWidget()
        self._group_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        group_area_layout = QVBoxLayout(self._group_area)
        group_area_layout.setContentsMargins(0, 0, 0, 0)
        group_area_layout.setSpacing(8)

        self.group_list = QListWidget()
        self.group_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.group_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.group_list.setStyleSheet(
            "QListWidget { border: 1px solid #3a3a3a; border-radius: 6px; background-color: #252525; color: #eeeeee; outline: none; }"
            "QListWidget::item { padding: 8px; border-bottom: 1px solid #333; }"
            "QListWidget::item:hover { background-color: #333333; }"
            "QListWidget::item:selected { background-color: #1E88E5; }"
        )
        self.group_list.itemClicked.connect(self._on_group_selected)
        group_area_layout.addWidget(self.group_list, 1)
        layout.addWidget(self._group_area)

        # 下方分组成员区域：占满剩余高度，列表超出时滚动
        member_area = QWidget()
        member_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        member_area_layout = QVBoxLayout(member_area)
        member_area_layout.setContentsMargins(0, 0, 0, 0)
        member_area_layout.setSpacing(8)

        self.group_member_table = QTableWidget()
        self.group_member_table.setColumnCount(3)
        self.group_member_table.setHorizontalHeaderLabels(["", "昵称", "操作"])
        self.group_member_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.group_member_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.group_member_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.group_member_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.group_member_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.group_member_table.setStyleSheet(
            "QTableWidget { border: 1px solid #3a3a3a; border-radius: 6px; background-color: #252525; gridline-color: #333; }"
            "QHeaderView::section { background-color: #2a2a2a; color: #ffffff; padding: 6px; border: none; border-bottom: 1px solid #3a3a3a; }"
            "QTableWidget::item { padding: 6px; color: #eeeeee; }"
            "QTableWidget::item:selected { background-color: #1E88E5; }"
        )
        group_header = self.group_member_table.horizontalHeader()
        group_header.setSectionResizeMode(0, QHeaderView.Fixed)
        group_header.setSectionResizeMode(1, QHeaderView.Stretch)
        group_header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.group_member_table.setColumnWidth(0, 50)
        self.group_member_table.setColumnWidth(2, 90)
        self.group_member_table.verticalHeader().setVisible(False)
        member_area_layout.addWidget(self.group_member_table, 1)

        bottom = QWidget()
        bottom.setMinimumHeight(52)
        bottom.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        bottom.setStyleSheet(
            "QWidget { background-color: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 8px; }"
            "QCheckBox { color: #eeeeee; font-size: 13px; spacing: 6px; }"
            "QCheckBox::indicator { width: 16px; height: 16px; }"
        )
        btn_layout = QHBoxLayout(bottom)
        btn_layout.setContentsMargins(14, 10, 14, 10)
        self.group_select_all = QCheckBox("全选")
        self.group_select_all.clicked.connect(self._on_group_select_all)
        btn_layout.addWidget(self.group_select_all)
        btn_layout.addStretch()
        download_btn = QPushButton("批量下载选中UP主的全部视频")
        download_btn.setMinimumHeight(38)
        download_btn.setMinimumWidth(240)
        download_btn.setCursor(Qt.PointingHandCursor)
        download_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; border-radius: 6px; padding: 0 20px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #1976D2; }"
            "QPushButton:pressed { background-color: #1565C0; }"
        )
        download_btn.clicked.connect(self._on_download_selected_group_members)
        btn_layout.addWidget(download_btn)
        member_area_layout.addWidget(bottom)

        layout.addWidget(member_area, 1)

        return page

    def _build_fav_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 顶部标题栏
        top = QHBoxLayout()
        title = QLabel("收藏列表")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        top.addWidget(title)
        top.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setMinimumHeight(32)
        refresh_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffffff; border: 1px solid #555; border-radius: 4px; padding: 0 14px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        refresh_btn.clicked.connect(self._load_fav_folders)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        # 上方收藏夹列表：高度自适应并滚动
        fav_area = QWidget()
        fav_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        fav_area_layout = QVBoxLayout(fav_area)
        fav_area_layout.setContentsMargins(0, 0, 0, 0)
        fav_area_layout.setSpacing(8)

        self.fav_list = QListWidget()
        self.fav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.fav_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.fav_list.setStyleSheet(
            "QListWidget { border: 1px solid #3a3a3a; border-radius: 6px; background-color: #252525; color: #eeeeee; outline: none; }"
            "QListWidget::item { padding: 8px; border-bottom: 1px solid #333; }"
            "QListWidget::item:hover { background-color: #333333; }"
            "QListWidget::item:selected { background-color: #1E88E5; }"
        )
        self.fav_list.itemClicked.connect(self._on_fav_folder_selected)
        fav_area_layout.addWidget(self.fav_list, 1)
        layout.addWidget(fav_area)

        # 下方收藏视频区域：占满剩余高度
        video_area = QWidget()
        video_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        video_area_layout = QVBoxLayout(video_area)
        video_area_layout.setContentsMargins(0, 0, 0, 0)
        video_area_layout.setSpacing(8)

        self.fav_video_table = QTableWidget()
        self.fav_video_table.setColumnCount(7)
        self.fav_video_table.setHorizontalHeaderLabels(["", "BV号", "标题", "UP主", "收藏时间", "状态", "操作"])
        self.fav_video_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.fav_video_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.fav_video_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.fav_video_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.fav_video_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.fav_video_table.setStyleSheet(
            "QTableWidget { border: 1px solid #3a3a3a; border-radius: 6px; background-color: #252525; gridline-color: #333; }"
            "QHeaderView::section { background-color: #2a2a2a; color: #ffffff; padding: 6px; border: none; border-bottom: 1px solid #3a3a3a; }"
            "QTableWidget::item { padding: 6px; color: #eeeeee; }"
            "QTableWidget::item:selected { background-color: #1E88E5; }"
        )
        fav_header = self.fav_video_table.horizontalHeader()
        fav_header.setSectionResizeMode(0, QHeaderView.Fixed)
        fav_header.setSectionResizeMode(1, QHeaderView.Interactive)
        fav_header.setSectionResizeMode(2, QHeaderView.Stretch)
        fav_header.setSectionResizeMode(3, QHeaderView.Interactive)
        fav_header.setSectionResizeMode(4, QHeaderView.Interactive)
        fav_header.setSectionResizeMode(5, QHeaderView.Interactive)
        fav_header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.fav_video_table.setColumnWidth(0, 30)
        self.fav_video_table.setColumnWidth(1, 100)
        self.fav_video_table.setColumnWidth(3, 120)
        self.fav_video_table.setColumnWidth(4, 150)
        self.fav_video_table.setColumnWidth(5, 100)
        self.fav_video_table.setColumnWidth(6, 80)
        video_area_layout.addWidget(self.fav_video_table, 1)

        bottom = QWidget()
        bottom.setMinimumHeight(52)
        bottom.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        bottom.setStyleSheet(
            "QWidget { background-color: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 8px; }"
            "QCheckBox { color: #eeeeee; font-size: 13px; spacing: 6px; }"
            "QCheckBox::indicator { width: 16px; height: 16px; }"
        )
        btn_layout = QHBoxLayout(bottom)
        btn_layout.setContentsMargins(14, 10, 14, 10)
        self.fav_select_all = QCheckBox("全选")
        self.fav_select_all.clicked.connect(self._on_fav_select_all)
        btn_layout.addWidget(self.fav_select_all)
        btn_layout.addStretch()
        download_btn = QPushButton("批量下载选中视频")
        download_btn.setMinimumHeight(38)
        download_btn.setMinimumWidth(180)
        download_btn.setCursor(Qt.PointingHandCursor)
        download_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; border-radius: 6px; padding: 0 20px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #1976D2; }"
            "QPushButton:pressed { background-color: #1565C0; }"
        )
        download_btn.clicked.connect(self._on_download_selected_fav_videos)
        btn_layout.addWidget(download_btn)
        video_area_layout.addWidget(bottom)

        layout.addWidget(video_area, 1)

        return page

    def _build_video_list_page(self, title: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 顶部标题栏
        top = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        top.addWidget(title_label)
        top.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setMinimumHeight(32)
        refresh_btn.setProperty("page_type", title)
        refresh_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffffff; border: 1px solid #555; border-radius: 4px; padding: 0 14px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        refresh_btn.clicked.connect(self._on_video_page_refresh)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        # 视频列表区域：占满剩余高度，超出滚动
        table_area = QWidget()
        table_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        table_area_layout = QVBoxLayout(table_area)
        table_area_layout.setContentsMargins(0, 0, 0, 0)
        table_area_layout.setSpacing(8)

        table = QTableWidget()
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels(["", "BV号", "标题", "UP主", "时间", "状态", "操作"])
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.ExtendedSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.setStyleSheet(
            "QTableWidget { border: 1px solid #3a3a3a; border-radius: 6px; background-color: #252525; gridline-color: #333; }"
            "QHeaderView::section { background-color: #2a2a2a; color: #ffffff; padding: 6px; border: none; border-bottom: 1px solid #3a3a3a; }"
            "QTableWidget::item { padding: 6px; color: #eeeeee; }"
            "QTableWidget::item:selected { background-color: #1E88E5; }"
        )
        v_header = table.horizontalHeader()
        v_header.setSectionResizeMode(0, QHeaderView.Fixed)
        v_header.setSectionResizeMode(1, QHeaderView.Interactive)
        v_header.setSectionResizeMode(2, QHeaderView.Stretch)
        v_header.setSectionResizeMode(3, QHeaderView.Interactive)
        v_header.setSectionResizeMode(4, QHeaderView.Interactive)
        v_header.setSectionResizeMode(5, QHeaderView.Interactive)
        v_header.setSectionResizeMode(6, QHeaderView.Fixed)
        table.setColumnWidth(0, 30)
        table.setColumnWidth(1, 100)
        table.setColumnWidth(3, 120)
        table.setColumnWidth(4, 150)
        table.setColumnWidth(5, 100)
        table.setColumnWidth(6, 80)
        table_area_layout.addWidget(table, 1)

        bottom = QWidget()
        bottom.setMinimumHeight(52)
        bottom.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        bottom.setStyleSheet(
            "QWidget { background-color: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 8px; }"
            "QCheckBox { color: #eeeeee; font-size: 13px; spacing: 6px; }"
            "QCheckBox::indicator { width: 16px; height: 16px; }"
        )
        btn_layout = QHBoxLayout(bottom)
        btn_layout.setContentsMargins(14, 10, 14, 10)
        select_all = QCheckBox("全选")
        select_all.clicked.connect(lambda checked, t=table: self._on_table_select_all(t, checked))
        btn_layout.addWidget(select_all)
        btn_layout.addStretch()
        download_btn = QPushButton("批量下载选中视频")
        download_btn.setMinimumHeight(38)
        download_btn.setMinimumWidth(180)
        download_btn.setCursor(Qt.PointingHandCursor)
        download_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; border-radius: 6px; padding: 0 20px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #1976D2; }"
            "QPushButton:pressed { background-color: #1565C0; }"
        )
        download_btn.clicked.connect(lambda: self._on_download_table_videos(table))
        btn_layout.addWidget(download_btn)
        table_area_layout.addWidget(bottom)

        layout.addWidget(table_area, 1)

        return page

    def _build_subscription_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 顶部标题栏
        top = QHBoxLayout()
        title = QLabel("我的订阅")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        top.addWidget(title)
        top.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setMinimumHeight(32)
        refresh_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffffff; border: 1px solid #555; border-radius: 4px; padding: 0 14px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        refresh_btn.clicked.connect(self._load_subscriptions)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        # 订阅列表区域：占满剩余高度，超出滚动
        table_area = QWidget()
        table_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        table_area_layout = QVBoxLayout(table_area)
        table_area_layout.setContentsMargins(0, 0, 0, 0)
        table_area_layout.setSpacing(8)

        self.sub_table = QTableWidget()
        self.sub_table.setColumnCount(5)
        self.sub_table.setHorizontalHeaderLabels(["", "ID", "名称", "类型", "操作"])
        self.sub_table.setColumnWidth(0, 30)
        self.sub_table.setColumnWidth(1, 100)
        self.sub_table.setColumnWidth(2, 300)
        self.sub_table.setColumnWidth(3, 100)
        self.sub_table.setColumnWidth(4, 80)
        self.sub_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.sub_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.sub_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.sub_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.sub_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.sub_table.setStyleSheet(
            "QTableWidget { border: 1px solid #3a3a3a; border-radius: 6px; background-color: #252525; gridline-color: #333; }"
            "QHeaderView::section { background-color: #2a2a2a; color: #ffffff; padding: 6px; border: none; border-bottom: 1px solid #3a3a3a; }"
            "QTableWidget::item { padding: 6px; color: #eeeeee; }"
            "QTableWidget::item:selected { background-color: #1E88E5; }"
        )
        self.sub_table.horizontalHeader().setStretchLastSection(True)
        table_area_layout.addWidget(self.sub_table, 1)

        bottom = QWidget()
        bottom.setMinimumHeight(52)
        bottom.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        bottom.setStyleSheet(
            "QWidget { background-color: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 8px; }"
            "QCheckBox { color: #eeeeee; font-size: 13px; spacing: 6px; }"
            "QCheckBox::indicator { width: 16px; height: 16px; }"
        )
        btn_layout = QHBoxLayout(bottom)
        btn_layout.setContentsMargins(14, 10, 14, 10)
        self.sub_select_all = QCheckBox("全选")
        self.sub_select_all.clicked.connect(self._on_sub_select_all)
        btn_layout.addWidget(self.sub_select_all)
        btn_layout.addStretch()
        download_btn = QPushButton("批量下载选中订阅")
        download_btn.setMinimumHeight(38)
        download_btn.setMinimumWidth(180)
        download_btn.setCursor(Qt.PointingHandCursor)
        download_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; border-radius: 6px; padding: 0 20px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #1976D2; }"
            "QPushButton:pressed { background-color: #1565C0; }"
        )
        download_btn.clicked.connect(self._on_download_selected_subscriptions)
        btn_layout.addWidget(download_btn)
        table_area_layout.addWidget(bottom)

        layout.addWidget(table_area, 1)

        return page

    # ---------- 通用表格辅助 ----------

    def _set_checkable_item(self, table: QTableWidget, row: int, col: int, checked: bool = False):
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        table.setItem(row, col, item)

    @staticmethod
    def _make_round_avatar(pixmap: QPixmap, size: int) -> QPixmap:
        """将方形头像裁剪为圆形"""
        if pixmap.isNull():
            return pixmap
        scaled = pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        rounded = QPixmap(size, size)
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)
        # 居中绘制
        x = (size - scaled.width()) // 2
        y = (size - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        return rounded

    def _update_follow_selection_count(self):
        """更新关注页选中计数"""
        rows = self._get_table_selected_rows(self.follow_table)
        count = len(rows)
        self.follow_selected_count.setText(f"已选 {count} 位")

    def _on_table_select_all(self, table: QTableWidget, checked: bool):
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def _get_table_selected_rows(self, table: QTableWidget) -> List[int]:
        selected_rows = set()
        # 复选框选中
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                selected_rows.add(row)
        # 行拖拽框选
        for item in table.selectedItems():
            selected_rows.add(item.row())
        return sorted(selected_rows)

    def _open_up_videos(self, mid: int, uname: str):
        """打开 UP 主视频列表弹窗"""
        dialog = UpVideoDialog(self.web, mid, uname, self.db_path, self.download_callback, self, wbi=self.wbi)
        dialog.exec()

    def _open_fav_videos(self, media_id: int, title: str):
        """打开收藏夹视频列表弹窗"""
        dialog = FavVideoDialog(self.web, media_id, title, self.db_path, self.download_callback, self)
        dialog.exec()

    # ---------- 数据加载 ----------

    def _load_user_info(self):
        asyncio.create_task(self._load_user_info_async())

    async def _load_user_info_async(self):
        try:
            data = await asyncio.to_thread(
                self.web.request,
                "https://api.bilibili.com/x/web-interface/nav",
                referer="https://www.bilibili.com",
            )
            if data.get("code") == 0:
                info = data["data"]
                self.user_info = info
                self.user_mid = info.get("mid", 0)
                # 获取到用户信息后再加载关注列表，确保 mid 已就绪
                self._load_followings()
        except Exception as e:
            logger.warning(f"[UserCenter] 获取用户信息失败: {e}")

    def _load_followings(self):
        asyncio.create_task(self._load_followings_async())

    async def _load_followings_async(self):
        self.follow_table.setRowCount(0)
        try:
            vmid = self.user_mid or int(self.web.dedeuserid or 0)
            followings = await asyncio.to_thread(fetch_followings, self.web, vmid=vmid)
            self.follow_table.setRowCount(len(followings))
            for i, f in enumerate(followings):
                self._set_checkable_item(self.follow_table, i, 0)
                mid = f.get("mid", 0)
                uname = f.get("uname", "")
                self.follow_table.setItem(i, 1, QTableWidgetItem(uname))
                # 将 mid 存入行数据，避免展示 UID
                self.follow_table.item(i, 1).setData(Qt.UserRole, mid)
                btn = QPushButton("查看")
                btn.setStyleSheet(
                    "QPushButton { background-color: #424242; color: white; border-radius: 4px; padding: 2px 10px; }"
                    "QPushButton:hover { background-color: #555555; }"
                )
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _, m=mid, u=uname: self._open_up_videos(m, u))
                self.follow_table.setCellWidget(i, 2, btn)
            self.follow_count_label.setText(f"共 {len(followings)} 位 UP主")
            self._update_follow_selection_count()
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取关注列表失败: {e}")

    def _load_follow_groups(self):
        asyncio.create_task(self._load_follow_groups_async())

    async def _load_follow_groups_async(self):
        self.group_list.clear()
        try:
            groups = await asyncio.to_thread(fetch_follow_groups, self.web)
            for g in groups:
                item = QListWidgetItem(f"{g.get('name', '')} ({g.get('count', 0)})")
                item.setData(Qt.UserRole, g.get("tagid", 0))
                self.group_list.addItem(item)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取关注分组失败: {e}")

    def _on_group_selected(self, item: QListWidgetItem):
        asyncio.create_task(self._on_group_selected_async(item))

    async def _on_group_selected_async(self, item: QListWidgetItem):
        tagid = item.data(Qt.UserRole)
        self.group_member_table.setRowCount(0)
        try:
            members = await asyncio.to_thread(fetch_group_members, self.web, tagid)
            self.group_member_table.setRowCount(len(members))
            for i, f in enumerate(members):
                self._set_checkable_item(self.group_member_table, i, 0)
                mid = f.get("mid", 0)
                uname = f.get("uname", "")
                self.group_member_table.setItem(i, 1, QTableWidgetItem(uname))
                self.group_member_table.item(i, 1).setData(Qt.UserRole, mid)
                btn = QPushButton("查看")
                btn.setStyleSheet(
                    "QPushButton { background-color: #424242; color: white; border-radius: 4px; padding: 2px 10px; }"
                    "QPushButton:hover { background-color: #555555; }"
                )
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _, m=mid, u=uname: self._open_up_videos(m, u))
                self.group_member_table.setCellWidget(i, 2, btn)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取分组成员失败: {e}")

    def _load_fav_folders(self):
        asyncio.create_task(self._load_fav_folders_async())

    async def _load_fav_folders_async(self):
        self.fav_list.clear()
        try:
            up_mid = self.user_mid or int(self.web.dedeuserid or 0)
            folders = await asyncio.to_thread(fetch_fav_folders, self.web, up_mid=up_mid)
            for f in folders:
                item = QListWidgetItem(f"{f.get('title', '')} ({f.get('media_count', 0)})")
                item.setData(Qt.UserRole, f.get("id", 0))
                item.setData(Qt.UserRole + 1, f.get("title", ""))
                self.fav_list.addItem(item)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取收藏夹失败: {e}")

    def _on_fav_folder_selected(self, item: QListWidgetItem):
        media_id = item.data(Qt.UserRole)
        title = item.data(Qt.UserRole + 1)
        self._open_fav_videos(media_id, title)

    def _on_video_page_refresh(self, page_type: str = ""):
        asyncio.create_task(self._on_video_page_refresh_async(page_type))

    async def _on_video_page_refresh_async(self, page_type: str = ""):
        if not page_type:
            sender = self.sender()
            page_type = sender.property("page_type") if sender else ""
        try:
            if page_type == "稍后再看":
                videos = await asyncio.to_thread(fetch_watchlater, self.web)
                self._fill_video_table(self.watchlater_page.findChild(QTableWidget), videos)
            elif page_type == "历史记录":
                videos = await asyncio.to_thread(fetch_history, self.web)
                self._fill_video_table(self.history_page.findChild(QTableWidget), videos)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取{page_type}失败: {e}")

    def _fill_video_table(self, table: QTableWidget, videos: List[Dict]):
        table.setRowCount(0)
        table.setRowCount(len(videos))
        for i, v in enumerate(videos):
            self._set_checkable_item(table, i, 0)
            bvid = v.get("bvid", "")
            table.setItem(i, 1, QTableWidgetItem(bvid))
            table.setItem(i, 2, QTableWidgetItem(v.get("title", "")))
            table.setItem(i, 3, QTableWidgetItem(v.get("uname", "")))
            table.setItem(i, 4, QTableWidgetItem(_ts_to_str(v.get("ctime", 0))))
            table.setItem(i, 5, QTableWidgetItem(_get_video_status(self.db, bvid)))
            btn = QPushButton("下载")
            title = v.get("title", "")
            uname = v.get("uname", "")
            btn.clicked.connect(lambda _, b=bvid, t=title, u=uname: self._download_one(b, t, u))
            table.setCellWidget(i, 6, btn)

    def _load_subscriptions(self):
        asyncio.create_task(self._load_subscriptions_async())

    async def _load_subscriptions_async(self):
        self.sub_table.setRowCount(0)
        try:
            subs = await asyncio.to_thread(fetch_subscriptions, self.web)
            self.sub_table.setRowCount(len(subs))
            for i, s in enumerate(subs):
                self._set_checkable_item(self.sub_table, i, 0)
                sid = s.get("id", "")
                title = s.get("title", "")
                self.sub_table.setItem(i, 1, QTableWidgetItem(str(sid)))
                self.sub_table.setItem(i, 2, QTableWidgetItem(title))
                self.sub_table.setItem(i, 3, QTableWidgetItem(s.get("type", "")))
                btn = QPushButton("下载")
                btn.setStyleSheet(
                    "QPushButton { background-color: #424242; color: white; border-radius: 4px; padding: 2px 10px; }"
                    "QPushButton:hover { background-color: #555555; }"
                )
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _, s=sid, t=title: self._on_download_subscription(s, t))
                self.sub_table.setCellWidget(i, 4, btn)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取订阅失败: {e}")

    # ---------- 导航与事件 ----------

    def _on_nav_changed(self, index: int):
        self.stack.setCurrentIndex(index)
        if index == 0 and self.follow_table.rowCount() == 0:
            self._load_followings()
        elif index == 1 and self.group_list.count() == 0:
            self._load_follow_groups()
        elif index == 2 and self.fav_list.count() == 0:
            self._load_fav_folders()
        elif index == 3:
            table = self.watchlater_page.findChild(QTableWidget)
            if table and table.rowCount() == 0:
                self._on_video_page_refresh("稍后再看")
        elif index == 4:
            table = self.history_page.findChild(QTableWidget)
            if table and table.rowCount() == 0:
                self._on_video_page_refresh("历史记录")
        elif index == 5 and self.sub_table.rowCount() == 0:
            self._load_subscriptions()

    def _on_follow_select_all(self, checked: bool):
        self._on_table_select_all(self.follow_table, checked)
        self._update_follow_selection_count()

    def _on_group_select_all(self, checked: bool):
        self._on_table_select_all(self.group_member_table, checked)

    def _on_fav_select_all(self, checked: bool):
        self._on_table_select_all(self.fav_video_table, checked)

    def _on_sub_select_all(self, checked: bool):
        self._on_table_select_all(self.sub_table, checked)

    def _download_one(self, bvid: str, title: str, uname: str):
        self.download_callback([{"bvid": bvid, "title": title, "uname": uname}])

    def _on_download_selected_follows(self):
        rows = self._get_table_selected_rows(self.follow_table)
        if not rows:
            QMessageBox.information(self, "提示", "请先勾选UP主")
            return
        up_list = []
        for row in rows:
            mid = int(self.follow_table.item(row, 1).data(Qt.UserRole) or 0)
            uname = self.follow_table.item(row, 1).text() or ""
            if mid:
                up_list.append({"mid": mid, "uname": uname})
        if not up_list:
            return
        if self.download_all_callback:
            self.download_all_callback(up_list)

    def _on_download_selected_group_members(self):
        rows = self._get_table_selected_rows(self.group_member_table)
        if not rows:
            QMessageBox.information(self, "提示", "请先勾选UP主")
            return
        up_list = []
        for row in rows:
            mid = int(self.group_member_table.item(row, 1).data(Qt.UserRole) or 0)
            uname = self.group_member_table.item(row, 1).text() or ""
            if mid:
                up_list.append({"mid": mid, "uname": uname})
        if not up_list:
            return
        if self.download_all_callback:
            self.download_all_callback(up_list)

    def _on_download_subscription(self, season_id, title: str):
        logger.info(f"[UserCenter] 下载订阅: {title} (season_id={season_id})")
        asyncio.create_task(self._download_subscription_async(season_id, title))

    async def _download_subscription_async(self, season_id, title: str):
        try:
            videos = await asyncio.to_thread(fetch_season_videos, self.web, season_id, wbi=self.wbi)
            if not videos:
                QMessageBox.information(self, "提示", f"《{title}》暂无可用分集")
                return
            for v in videos:
                v["uname"] = title
            self._safe_download_callback(videos)
            QMessageBox.information(self, "提示", f"已将《{title}》的 {len(videos)} 个分集加入下载队列")
        except Exception as e:
            logger.exception(f"[UserCenter] 获取订阅剧集失败 {season_id}")
            QMessageBox.critical(self, "下载失败", f"获取《{title}》剧集失败: {e}")

    def _on_download_selected_subscriptions(self):
        rows = self._get_table_selected_rows(self.sub_table)
        if not rows:
            QMessageBox.information(self, "提示", "请先勾选订阅")
            return
        selected = []
        for row in rows:
            sid = self.sub_table.item(row, 1).text() or ""
            title = self.sub_table.item(row, 2).text() or ""
            if sid:
                selected.append((sid, title))
        if not selected:
            return
        logger.info(f"[UserCenter] 批量下载选中订阅: {len(selected)} 个")
        asyncio.create_task(self._download_selected_subscriptions_async(selected))

    async def _download_selected_subscriptions_async(self, selected: list):
        all_videos = []
        failed = []
        for sid, title in selected:
            try:
                videos = await asyncio.to_thread(fetch_season_videos, self.web, sid, wbi=self.wbi)
                for v in videos:
                    v["uname"] = title
                all_videos.extend(videos)
            except Exception as e:
                logger.warning(f"[UserCenter] 获取订阅 {title} 剧集失败: {e}")
                failed.append(title)
        if failed:
            QMessageBox.warning(self, "部分失败", "以下订阅获取失败:\n" + "\n".join(failed))
        if all_videos:
            self._safe_download_callback(all_videos)
            QMessageBox.information(self, "提示", f"已将 {len(all_videos)} 个分集加入下载队列")
        elif not failed:
            QMessageBox.information(self, "提示", "选中的订阅暂无可用分集")

    def _on_download_selected_fav_videos(self):
        logger.info("[UserCenter] 点击批量下载收藏视频")
        rows = self._get_table_selected_rows(self.fav_video_table)
        videos = []
        for row in rows:
            bvid = self.fav_video_table.item(row, 1).text() or ""
            title = self.fav_video_table.item(row, 2).text() or ""
            uname = self.fav_video_table.item(row, 3).text() or ""
            if bvid:
                videos.append({"bvid": bvid, "title": title, "uname": uname})
        if not videos:
            QMessageBox.information(self, "提示", "请先勾选视频")
            return
        self._safe_download_callback(videos)

    def _on_download_table_videos(self, table: QTableWidget):
        logger.info("[UserCenter] 点击批量下载表格视频")
        rows = self._get_table_selected_rows(table)
        videos = []
        for row in rows:
            bvid = table.item(row, 1).text() or ""
            title = table.item(row, 2).text() or ""
            uname = table.item(row, 3).text() or ""
            if bvid:
                videos.append({"bvid": bvid, "title": title, "uname": uname})
        if not videos:
            QMessageBox.information(self, "提示", "请先勾选视频")
            return
        self._safe_download_callback(videos)

    def _safe_download_callback(self, videos: list):
        try:
            logger.info(f"[UserCenter] 触发下载回调，数量: {len(videos)}")
            self.download_callback(videos)
            logger.info("[UserCenter] 下载回调执行成功")
        except Exception:
            logger.exception("[UserCenter] 下载回调执行失败")
            QMessageBox.critical(self, "错误", "启动下载失败，请查看日志")


class UpVideoDialog(QDialog):
    """UP 主视频列表弹窗"""

    def __init__(self, web_client, mid: int, uname: str, db_path: str, download_callback: Callable, parent=None, wbi: Optional[WBI] = None):
        super().__init__(parent)
        self.setWindowTitle(f"{uname} 的视频列表")
        self.setMinimumSize(1100, 700)
        self.resize(1200, 750)
        self.web = web_client
        self.mid = mid
        self.uname = uname
        self.db_path = db_path
        self.db = DownloadDB(db_path)
        self.download_callback = download_callback
        self.videos: List[Dict] = []
        self.wbi = wbi or WBI(web_client.sessdata)

        self._build_ui()
        self._load_videos()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        top.addWidget(QLabel(f"UP主: {self.uname}"))
        top.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._load_videos)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["", "BV号", "标题", "发布时间", "时长", "状态", "操作"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        up_header = self.table.horizontalHeader()
        up_header.setSectionResizeMode(0, QHeaderView.Fixed)
        up_header.setSectionResizeMode(1, QHeaderView.Interactive)
        up_header.setSectionResizeMode(2, QHeaderView.Stretch)
        up_header.setSectionResizeMode(3, QHeaderView.Interactive)
        up_header.setSectionResizeMode(4, QHeaderView.Interactive)
        up_header.setSectionResizeMode(5, QHeaderView.Interactive)
        up_header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 30)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(3, 150)
        self.table.setColumnWidth(4, 80)
        self.table.setColumnWidth(5, 100)
        self.table.setColumnWidth(6, 80)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.select_all = QCheckBox("全选")
        self.select_all.clicked.connect(lambda checked: self._on_select_all(checked))
        btn_layout.addWidget(self.select_all)
        btn_layout.addStretch()
        download_btn = QPushButton("批量下载选中视频")
        download_btn.clicked.connect(self._on_download_selected)
        btn_layout.addWidget(download_btn)
        layout.addLayout(btn_layout)

    def _on_select_all(self, checked: bool):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def _load_videos(self):
        self.table.setRowCount(0)
        try:
            self.videos = fetch_up_videos(self.web, self.mid, wbi=self.wbi)
            self.table.setRowCount(len(self.videos))
            for i, v in enumerate(self.videos):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                item.setCheckState(Qt.Unchecked)
                self.table.setItem(i, 0, item)
                bvid = v.get("bvid", "")
                self.table.setItem(i, 1, QTableWidgetItem(bvid))
                self.table.setItem(i, 2, QTableWidgetItem(v.get("title", "")))
                self.table.setItem(i, 3, QTableWidgetItem(_ts_to_str(v.get("created", 0))))
                self.table.setItem(i, 4, QTableWidgetItem(self._format_duration(v.get("length", 0))))
                self.table.setItem(i, 5, QTableWidgetItem(_get_video_status(self.db, bvid)))
                btn = QPushButton("下载")
                title = v.get("title", "")
                btn.clicked.connect(lambda _, b=bvid, t=title: self._download_one(b, t))
                self.table.setCellWidget(i, 6, btn)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取视频列表失败: {e}")

    def _format_duration(self, seconds: int) -> str:
        try:
            seconds = int(seconds)
            m, s = divmod(seconds, 60)
            h, m = divmod(m, 60)
            if h:
                return f"{h}:{m:02d}:{s:02d}"
            return f"{m}:{s:02d}"
        except Exception:
            return ""

    def _download_one(self, bvid: str, title: str):
        self.download_callback([{"bvid": bvid, "title": title, "uname": self.uname}])

    def _on_download_selected(self):
        selected_rows = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                selected_rows.add(row)
        for item in self.table.selectedItems():
            selected_rows.add(item.row())

        videos = []
        for row in sorted(selected_rows):
            bvid = self.table.item(row, 1).text() or ""
            title = self.table.item(row, 2).text() or ""
            if not bvid:
                continue
            # 跳过已有状态的视频（已存档、充电专属、付费、不支持 DASH 等）
            if _get_video_status(self.db, bvid):
                continue
            videos.append({"bvid": bvid, "title": title, "uname": self.uname})
        if not videos:
            QMessageBox.information(self, "提示", "所选视频均已有状态或无需下载")
            return
        self.download_callback(videos)


class FavVideoDialog(QDialog):
    """收藏夹视频列表弹窗"""

    def __init__(self, web_client, media_id: int, title: str, db_path: str, download_callback: Callable, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"收藏夹: {title}")
        self.setMinimumSize(1100, 700)
        self.resize(1200, 750)
        self.web = web_client
        self.media_id = media_id
        self.db_path = db_path
        self.db = DownloadDB(db_path)
        self.download_callback = download_callback
        self.videos: List[Dict] = []

        self._build_ui()
        self._load_videos()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        top.addWidget(QLabel(f"收藏夹: {self.windowTitle()}"))
        top.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._load_videos)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["", "BV号", "标题", "UP主", "收藏时间", "状态", "操作"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        favd_header = self.table.horizontalHeader()
        favd_header.setSectionResizeMode(0, QHeaderView.Fixed)
        favd_header.setSectionResizeMode(1, QHeaderView.Interactive)
        favd_header.setSectionResizeMode(2, QHeaderView.Stretch)
        favd_header.setSectionResizeMode(3, QHeaderView.Interactive)
        favd_header.setSectionResizeMode(4, QHeaderView.Interactive)
        favd_header.setSectionResizeMode(5, QHeaderView.Interactive)
        favd_header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 30)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(3, 120)
        self.table.setColumnWidth(4, 150)
        self.table.setColumnWidth(5, 100)
        self.table.setColumnWidth(6, 80)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.select_all = QCheckBox("全选")
        self.select_all.clicked.connect(lambda checked: self._on_select_all(checked))
        btn_layout.addWidget(self.select_all)
        btn_layout.addStretch()
        download_btn = QPushButton("批量下载选中视频")
        download_btn.clicked.connect(self._on_download_selected)
        btn_layout.addWidget(download_btn)
        layout.addLayout(btn_layout)

    def _on_select_all(self, checked: bool):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def _load_videos(self):
        self.table.setRowCount(0)
        try:
            self.videos = fetch_fav_videos(self.web, self.media_id)
            self.table.setRowCount(len(self.videos))
            for i, v in enumerate(self.videos):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                item.setCheckState(Qt.Unchecked)
                self.table.setItem(i, 0, item)
                bvid = v.get("bvid", "")
                self.table.setItem(i, 1, QTableWidgetItem(bvid))
                self.table.setItem(i, 2, QTableWidgetItem(v.get("title", "")))
                self.table.setItem(i, 3, QTableWidgetItem(v.get("uname", "")))
                self.table.setItem(i, 4, QTableWidgetItem(_ts_to_str(v.get("fav_time", 0))))
                self.table.setItem(i, 5, QTableWidgetItem(_get_video_status(self.db, bvid)))
                btn = QPushButton("下载")
                title = v.get("title", "")
                uname = v.get("uname", "")
                btn.clicked.connect(lambda _, b=bvid, t=title, u=uname: self._download_one(b, t, u))
                self.table.setCellWidget(i, 6, btn)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取收藏夹视频失败: {e}")

    def _download_one(self, bvid: str, title: str, uname: str):
        self.download_callback([{"bvid": bvid, "title": title, "uname": uname}])

    def _on_download_selected(self):
        selected_rows = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                selected_rows.add(row)
        for item in self.table.selectedItems():
            selected_rows.add(item.row())

        videos = []
        for row in sorted(selected_rows):
            bvid = self.table.item(row, 1).text() or ""
            title = self.table.item(row, 2).text() or ""
            uname = self.table.item(row, 3).text() or ""
            if bvid:
                videos.append({"bvid": bvid, "title": title, "uname": uname})
        if not videos:
            QMessageBox.information(self, "提示", "请先勾选视频")
            return
        self.download_callback(videos)


# ---------- B站 API 封装 ----------

def fetch_followings(web, vmid: int = 0) -> List[Dict]:
    """获取我的关注列表（全部）"""
    result = []
    pn = 1
    ps = 50
    vmid = vmid or int(web.dedeuserid or 0)
    if not vmid:
        raise RuntimeError("未获取到用户 mid，无法加载关注列表")
    while True:
        data = web.request(
            "https://api.bilibili.com/x/relation/followings",
            referer="https://space.bilibili.com",
            params={"vmid": vmid, "pn": pn, "ps": ps, "order_type": ""},
        )
        if data.get("code") != 0:
            raise RuntimeError(data.get("message", "获取关注列表失败"))
        items = data.get("data", {}).get("list", [])
        if not items:
            break
        for item in items:
            result.append({
                "mid": item.get("mid"),
                "uname": item.get("uname", ""),
                "sign": item.get("sign", ""),
                "face": item.get("face", ""),
            })
        if len(items) < ps:
            break
        pn += 1
    return result


def fetch_follow_groups(web) -> List[Dict]:
    """获取关注分组"""
    data = web.request(
        "https://api.bilibili.com/x/relation/tags",
        referer="https://space.bilibili.com",
    )
    if data.get("code") != 0:
        raise RuntimeError(data.get("message", "获取分组失败"))
    groups = []
    for g in data.get("data", []):
        groups.append({
            "tagid": g.get("tagid"),
            "name": g.get("name", ""),
            "count": g.get("count", 0),
        })
    return groups


def fetch_group_members(web, tagid: int) -> List[Dict]:
    """获取分组成员"""
    result = []
    pn = 1
    ps = 50
    while True:
        data = web.request(
            "https://api.bilibili.com/x/relation/tag",
            referer="https://space.bilibili.com",
            params={"tagid": tagid, "pn": pn, "ps": ps},
        )
        if data.get("code") != 0:
            raise RuntimeError(data.get("message", "获取分组成员失败"))
        items = data.get("data", [])
        if not items:
            break
        for item in items:
            result.append({
                "mid": item.get("mid"),
                "uname": item.get("uname", ""),
                "sign": item.get("sign", ""),
                "face": item.get("face", ""),
            })
        if len(items) < ps:
            break
        pn += 1
    return result


def fetch_fav_folders(web, up_mid: int = 0) -> List[Dict]:
    """获取收藏夹列表"""
    up_mid = up_mid or int(web.dedeuserid or 0)
    data = web.request(
        "https://api.bilibili.com/x/v3/fav/folder/created/list-all",
        referer="https://space.bilibili.com",
        params={"up_mid": up_mid},
    )
    if data.get("code") != 0:
        raise RuntimeError(data.get("message", "获取收藏夹失败"))
    folders = []
    for f in data.get("data", {}).get("list", []):
        folders.append({
            "id": f.get("id"),
            "title": f.get("title", ""),
            "media_count": f.get("media_count", 0),
        })
    return folders


def fetch_fav_videos(web, media_id: int) -> List[Dict]:
    """获取收藏夹视频"""
    result = []
    pn = 1
    ps = 20
    while True:
        data = web.request(
            "https://api.bilibili.com/x/v3/fav/resource/list",
            referer="https://www.bilibili.com",
            params={
                "media_id": media_id,
                "pn": pn,
                "ps": ps,
                "platform": "web",
                "type": 0,
            },
        )
        if data.get("code") != 0:
            raise RuntimeError(data.get("message", "获取收藏夹视频失败"))
        medias = data.get("data", {}).get("medias", []) or []
        for m in medias:
            if m.get("type") != 2:
                continue
            upper = m.get("upper", {})
            result.append({
                "bvid": m.get("bvid", ""),
                "title": m.get("title", ""),
                "uname": upper.get("name", ""),
                "fav_time": m.get("fav_time", 0),
            })
        if len(medias) < ps:
            break
        pn += 1
    return result


def fetch_watchlater(web) -> List[Dict]:
    """获取稍后再看列表"""
    data = web.request(
        "https://api.bilibili.com/x/v2/history/toview/web",
        referer="https://www.bilibili.com",
    )
    if data.get("code") != 0:
        raise RuntimeError(data.get("message", "获取稍后再看失败"))
    result = []
    for item in data.get("data", {}).get("list", []):
        owner = item.get("owner", {})
        result.append({
            "bvid": item.get("bvid", ""),
            "title": item.get("title", ""),
            "uname": owner.get("name", ""),
            "ctime": item.get("ctime", 0),
        })
    return result


def fetch_history(web) -> List[Dict]:
    """获取历史记录（按天分页，最多 10 页）"""
    result = []
    max_at = ""
    view_at = ""
    for _ in range(10):
        params = {"max": max_at, "view_at": view_at, "business": "", "type": "", "ps": 20}
        data = web.request(
            "https://api.bilibili.com/x/web-interface/history/cursor",
            referer="https://www.bilibili.com",
            params=params,
        )
        if data.get("code") != 0:
            raise RuntimeError(data.get("message", "获取历史记录失败"))
        list_data = data.get("data", {})
        items = list_data.get("list", [])
        if not items:
            break
        for item in items:
            if item.get("history", {}).get("business") != "archive":
                continue
            owner = item.get("owner", {})
            result.append({
                "bvid": item.get("bvid", ""),
                "title": item.get("title", ""),
                "uname": owner.get("name", ""),
                "ctime": item.get("view_at", 0),
            })
        cursor = list_data.get("cursor", {})
        max_at = cursor.get("max", "")
        view_at = cursor.get("view_at", "")
        if not max_at:
            break
    return result


def fetch_subscriptions(web) -> List[Dict]:
    """获取我的订阅（订阅番剧/剧集/课程等）"""
    result = []
    pn = 1
    ps = 30
    while True:
        data = web.request(
            "https://api.bilibili.com/x/space/bangumi/follow/list",
            referer="https://space.bilibili.com",
            params={"vmid": web.dedeuserid or "0", "pn": pn, "ps": ps, "type": 1},
        )
        if data.get("code") != 0:
            raise RuntimeError(data.get("message", "获取订阅失败"))
        items = data.get("data", {}).get("list", [])
        if not items:
            break
        for item in items:
            result.append({
                "id": item.get("season_id", ""),
                "title": item.get("title", ""),
                "type": "番剧",
            })
        if len(items) < ps:
            break
        pn += 1
    return result


def fetch_season_videos(web, season_id: int, wbi: Optional[WBI] = None) -> List[Dict]:
    """获取番剧/订阅剧集的分集视频列表（带 WBI 签名）。"""
    if wbi is None:
        wbi = WBI(web.sessdata)
    params = {"season_id": season_id}
    signed = wbi.sign(params)
    data = web.request(
        "https://api.bilibili.com/pgc/view/web/season",
        referer=f"https://www.bilibili.com/bangumi/play/ss{season_id}",
        params=signed,
    )
    if data.get("code") != 0:
        raise RuntimeError(data.get("message", "获取剧集信息失败"))
    episodes = data.get("data", {}).get("episodes", [])
    result = []
    for ep in episodes:
        bvid = ep.get("bvid", "")
        if not bvid:
            continue
        result.append({
            "bvid": bvid,
            "title": ep.get("long_title") or ep.get("title", ""),
            "uname": "",
        })
    return result


def fetch_up_videos(web, mid: int, limit_pages: int = 100, wbi: Optional[WBI] = None) -> List[Dict]:
    """获取UP主全部投稿视频（带 WBI 签名）"""
    if wbi is None:
        wbi = WBI(web.sessdata)
    result = []
    pn = 1
    ps = 30
    for _ in range(limit_pages):
        params = {"mid": mid, "pn": pn, "ps": ps, "order": "pubdate"}
        signed = wbi.sign(params)
        data = web.request(
            "https://api.bilibili.com/x/space/wbi/arc/search",
            referer=f"https://space.bilibili.com/{mid}",
            params=signed,
        )
        if data.get("code") != 0:
            raise RuntimeError(data.get("message", "获取UP主视频失败"))
        vlist = data.get("data", {}).get("list", {}).get("vlist", [])
        if not vlist:
            break
        for v in vlist:
            result.append({
                "bvid": v.get("bvid", ""),
                "title": v.get("title", ""),
                "created": v.get("created", 0),
                "length": v.get("length", 0),
            })
        if len(vlist) < ps:
            break
        pn += 1
    return result
