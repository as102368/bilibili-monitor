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
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QStackedWidget,
    QProgressDialog,
    QCheckBox,
    QSplitter,
    QTextEdit,
)
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QPixmap, QIcon

from ..logger import get_logger
from ..wbi import WBI

logger = get_logger(__name__)


def _ts_to_str(ts: int) -> str:
    """秒级时间戳转本地时间字符串"""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


class UserCenterDialog(QWidget):
    """用户中心页面（嵌入主窗口使用）"""

    def __init__(self, web_client, download_callback: Callable[[List[Dict]], None], parent=None):
        super().__init__(parent)
        self.setMinimumSize(1000, 700)
        self.web = web_client
        self.download_callback = download_callback
        self.user_info: Dict = {}
        self.current_videos: List[Dict] = []

        self._build_ui()
        # 延迟加载数据，避免在创建/切换页面时阻塞主线程
        QTimer.singleShot(0, self._load_user_info)
        QTimer.singleShot(0, self._load_followings)

    def _build_ui(self):
        main_layout = QHBoxLayout(self)

        # 左侧导航
        left_widget = QWidget()
        left_widget.setMaximumWidth(180)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(8, 8, 8, 8)

        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(64, 64)
        self.avatar_label.setAlignment(Qt.AlignCenter)
        self.avatar_label.setStyleSheet("border-radius: 32px; border: 1px solid #ddd;")
        left_layout.addWidget(self.avatar_label, alignment=Qt.AlignCenter)

        self.uname_label = QLabel("未登录")
        self.uname_label.setAlignment(Qt.AlignCenter)
        self.uname_label.setStyleSheet("font-weight: bold; margin: 8px 0;")
        left_layout.addWidget(self.uname_label)

        self.nav_list = QListWidget()
        self.nav_list.addItem("我的关注")
        self.nav_list.addItem("关注分组")
        self.nav_list.addItem("收藏列表")
        self.nav_list.addItem("稍后再看")
        self.nav_list.addItem("历史记录")
        self.nav_list.addItem("我的订阅")
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        left_layout.addWidget(self.nav_list)

        main_layout.addWidget(left_widget)

        # 右侧内容
        self.stack = QStackedWidget()

        # 1. 我的关注
        self.follow_page = self._build_follow_page()
        self.stack.addWidget(self.follow_page)

        # 2. 关注分组
        self.group_page = self._build_group_page()
        self.stack.addWidget(self.group_page)

        # 3. 收藏列表
        self.fav_page = self._build_fav_page()
        self.stack.addWidget(self.fav_page)

        # 4. 稍后再看
        self.watchlater_page = self._build_video_list_page("稍后再看")
        self.stack.addWidget(self.watchlater_page)

        # 5. 历史记录
        self.history_page = self._build_video_list_page("历史记录")
        self.stack.addWidget(self.history_page)

        # 6. 我的订阅
        self.subscription_page = self._build_subscription_page()
        self.stack.addWidget(self.subscription_page)

        main_layout.addWidget(self.stack, 1)

    def _build_follow_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        top.addWidget(QLabel("我的关注"))
        top.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._load_followings)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        self.follow_table = QTableWidget()
        self.follow_table.setColumnCount(5)
        self.follow_table.setHorizontalHeaderLabels(["", "UID", "昵称", "签名", "操作"])
        self.follow_table.setColumnWidth(0, 30)
        self.follow_table.setColumnWidth(1, 100)
        self.follow_table.setColumnWidth(2, 160)
        self.follow_table.setColumnWidth(3, 300)
        self.follow_table.setColumnWidth(4, 80)
        self.follow_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.follow_table)

        btn_layout = QHBoxLayout()
        self.follow_select_all = QCheckBox("全选")
        self.follow_select_all.clicked.connect(self._on_follow_select_all)
        btn_layout.addWidget(self.follow_select_all)
        btn_layout.addStretch()
        download_btn = QPushButton("批量下载选中UP主的全部视频")
        download_btn.clicked.connect(self._on_download_selected_follows)
        btn_layout.addWidget(download_btn)
        layout.addLayout(btn_layout)

        return page

    def _build_group_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        top.addWidget(QLabel("关注分组"))
        top.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._load_follow_groups)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        self.group_list = QListWidget()
        self.group_list.itemClicked.connect(self._on_group_selected)
        layout.addWidget(self.group_list, 1)

        self.group_member_table = QTableWidget()
        self.group_member_table.setColumnCount(5)
        self.group_member_table.setHorizontalHeaderLabels(["", "UID", "昵称", "签名", "操作"])
        self.group_member_table.setColumnWidth(0, 30)
        self.group_member_table.setColumnWidth(1, 100)
        self.group_member_table.setColumnWidth(2, 160)
        self.group_member_table.setColumnWidth(3, 300)
        self.group_member_table.setColumnWidth(4, 80)
        self.group_member_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.group_member_table, 2)

        btn_layout = QHBoxLayout()
        self.group_select_all = QCheckBox("全选")
        self.group_select_all.clicked.connect(self._on_group_select_all)
        btn_layout.addWidget(self.group_select_all)
        btn_layout.addStretch()
        download_btn = QPushButton("批量下载选中UP主的全部视频")
        download_btn.clicked.connect(self._on_download_selected_group_members)
        btn_layout.addWidget(download_btn)
        layout.addLayout(btn_layout)

        return page

    def _build_fav_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        top.addWidget(QLabel("收藏列表"))
        top.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._load_fav_folders)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        self.fav_list = QListWidget()
        self.fav_list.itemClicked.connect(self._on_fav_folder_selected)
        layout.addWidget(self.fav_list, 1)

        self.fav_video_table = QTableWidget()
        self.fav_video_table.setColumnCount(6)
        self.fav_video_table.setHorizontalHeaderLabels(["", "BV号", "标题", "UP主", "收藏时间", "操作"])
        self.fav_video_table.setColumnWidth(0, 30)
        self.fav_video_table.setColumnWidth(1, 100)
        self.fav_video_table.setColumnWidth(2, 300)
        self.fav_video_table.setColumnWidth(3, 120)
        self.fav_video_table.setColumnWidth(4, 150)
        self.fav_video_table.setColumnWidth(5, 80)
        self.fav_video_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.fav_video_table, 2)

        btn_layout = QHBoxLayout()
        self.fav_select_all = QCheckBox("全选")
        self.fav_select_all.clicked.connect(self._on_fav_select_all)
        btn_layout.addWidget(self.fav_select_all)
        btn_layout.addStretch()
        download_btn = QPushButton("批量下载选中视频")
        download_btn.clicked.connect(self._on_download_selected_fav_videos)
        btn_layout.addWidget(download_btn)
        layout.addLayout(btn_layout)

        return page

    def _build_video_list_page(self, title: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        top.addWidget(QLabel(title))
        top.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setProperty("page_type", title)
        refresh_btn.clicked.connect(self._on_video_page_refresh)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["", "BV号", "标题", "UP主", "时间", "操作"])
        table.setColumnWidth(0, 30)
        table.setColumnWidth(1, 100)
        table.setColumnWidth(2, 320)
        table.setColumnWidth(3, 120)
        table.setColumnWidth(4, 150)
        table.setColumnWidth(5, 80)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)

        btn_layout = QHBoxLayout()
        select_all = QCheckBox("全选")
        select_all.clicked.connect(lambda checked, t=table: self._on_table_select_all(t, checked))
        btn_layout.addWidget(select_all)
        btn_layout.addStretch()
        download_btn = QPushButton("批量下载选中视频")
        download_btn.clicked.connect(lambda: self._on_download_table_videos(table))
        btn_layout.addWidget(download_btn)
        layout.addLayout(btn_layout)

        return page

    def _build_subscription_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        top.addWidget(QLabel("我的订阅"))
        top.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._load_subscriptions)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        self.sub_table = QTableWidget()
        self.sub_table.setColumnCount(5)
        self.sub_table.setHorizontalHeaderLabels(["", "ID", "名称", "类型", "操作"])
        self.sub_table.setColumnWidth(0, 30)
        self.sub_table.setColumnWidth(1, 100)
        self.sub_table.setColumnWidth(2, 300)
        self.sub_table.setColumnWidth(3, 100)
        self.sub_table.setColumnWidth(4, 80)
        self.sub_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.sub_table)

        btn_layout = QHBoxLayout()
        self.sub_select_all = QCheckBox("全选")
        self.sub_select_all.clicked.connect(self._on_sub_select_all)
        btn_layout.addWidget(self.sub_select_all)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        return page

    # ---------- 通用表格辅助 ----------

    def _set_checkable_item(self, table: QTableWidget, row: int, col: int, checked: bool = False):
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        table.setItem(row, col, item)

    def _on_table_select_all(self, table: QTableWidget, checked: bool):
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def _get_table_selected_rows(self, table: QTableWidget) -> List[int]:
        result = []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                result.append(row)
        return result

    def _open_up_videos(self, mid: int, uname: str):
        """打开 UP 主视频列表弹窗"""
        dialog = UpVideoDialog(self.web, mid, uname, self.download_callback, self)
        dialog.exec()

    def _open_fav_videos(self, media_id: int, title: str):
        """打开收藏夹视频列表弹窗"""
        dialog = FavVideoDialog(self.web, media_id, title, self.download_callback, self)
        dialog.exec()

    # ---------- 数据加载 ----------

    def _load_user_info(self):
        try:
            data = self.web.request(
                "https://api.bilibili.com/x/web-interface/nav",
                referer="https://www.bilibili.com",
            )
            if data.get("code") == 0:
                info = data["data"]
                self.user_info = info
                self.uname_label.setText(info.get("uname", "未登录"))
                face_url = info.get("face", "")
                if face_url:
                    resp = self.web.session.get(face_url, timeout=15)
                    resp.raise_for_status()
                    pixmap = QPixmap()
                    pixmap.loadFromData(resp.content)
                    if not pixmap.isNull():
                        scaled = pixmap.scaled(
                            64, 64, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
                        )
                        self.avatar_label.setPixmap(scaled)
        except Exception as e:
            logger.warning(f"[UserCenter] 获取用户信息失败: {e}")

    def _load_followings(self):
        self.follow_table.setRowCount(0)
        try:
            followings = fetch_followings(self.web)
            self.follow_table.setRowCount(len(followings))
            for i, f in enumerate(followings):
                self._set_checkable_item(self.follow_table, i, 0)
                self.follow_table.setItem(i, 1, QTableWidgetItem(str(f.get("mid", ""))))
                self.follow_table.setItem(i, 2, QTableWidgetItem(f.get("uname", "")))
                self.follow_table.setItem(i, 3, QTableWidgetItem(f.get("sign", "")))
                btn = QPushButton("查看")
                mid = f.get("mid", 0)
                uname = f.get("uname", "")
                btn.clicked.connect(lambda _, m=mid, u=uname: self._open_up_videos(m, u))
                self.follow_table.setCellWidget(i, 4, btn)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取关注列表失败: {e}")

    def _load_follow_groups(self):
        self.group_list.clear()
        try:
            groups = fetch_follow_groups(self.web)
            for g in groups:
                item = QListWidgetItem(f"{g.get('name', '')} ({g.get('count', 0)})")
                item.setData(Qt.UserRole, g.get("tagid", 0))
                self.group_list.addItem(item)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取关注分组失败: {e}")

    def _on_group_selected(self, item: QListWidgetItem):
        tagid = item.data(Qt.UserRole)
        self.group_member_table.setRowCount(0)
        try:
            members = fetch_group_members(self.web, tagid)
            self.group_member_table.setRowCount(len(members))
            for i, f in enumerate(members):
                self._set_checkable_item(self.group_member_table, i, 0)
                self.group_member_table.setItem(i, 1, QTableWidgetItem(str(f.get("mid", ""))))
                self.group_member_table.setItem(i, 2, QTableWidgetItem(f.get("uname", "")))
                self.group_member_table.setItem(i, 3, QTableWidgetItem(f.get("sign", "")))
                btn = QPushButton("查看")
                mid = f.get("mid", 0)
                uname = f.get("uname", "")
                btn.clicked.connect(lambda _, m=mid, u=uname: self._open_up_videos(m, u))
                self.group_member_table.setCellWidget(i, 4, btn)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取分组成员失败: {e}")

    def _load_fav_folders(self):
        self.fav_list.clear()
        try:
            folders = fetch_fav_folders(self.web)
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

    def _on_video_page_refresh(self):
        sender = self.sender()
        page_type = sender.property("page_type") if sender else ""
        try:
            if page_type == "稍后再看":
                videos = fetch_watchlater(self.web)
                self._fill_video_table(self.watchlater_page.findChild(QTableWidget), videos)
            elif page_type == "历史记录":
                videos = fetch_history(self.web)
                self._fill_video_table(self.history_page.findChild(QTableWidget), videos)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取{page_type}失败: {e}")

    def _fill_video_table(self, table: QTableWidget, videos: List[Dict]):
        table.setRowCount(0)
        table.setRowCount(len(videos))
        for i, v in enumerate(videos):
            self._set_checkable_item(table, i, 0)
            table.setItem(i, 1, QTableWidgetItem(v.get("bvid", "")))
            table.setItem(i, 2, QTableWidgetItem(v.get("title", "")))
            table.setItem(i, 3, QTableWidgetItem(v.get("uname", "")))
            table.setItem(i, 4, QTableWidgetItem(_ts_to_str(v.get("ctime", 0))))
            btn = QPushButton("下载")
            bvid = v.get("bvid", "")
            title = v.get("title", "")
            uname = v.get("uname", "")
            btn.clicked.connect(lambda _, b=bvid, t=title, u=uname: self._download_one(b, t, u))
            table.setCellWidget(i, 5, btn)

    def _load_subscriptions(self):
        self.sub_table.setRowCount(0)
        try:
            subs = fetch_subscriptions(self.web)
            self.sub_table.setRowCount(len(subs))
            for i, s in enumerate(subs):
                self._set_checkable_item(self.sub_table, i, 0)
                self.sub_table.setItem(i, 1, QTableWidgetItem(str(s.get("id", ""))))
                self.sub_table.setItem(i, 2, QTableWidgetItem(s.get("title", "")))
                self.sub_table.setItem(i, 3, QTableWidgetItem(s.get("type", "")))
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
                self._on_video_page_refresh()
        elif index == 4:
            table = self.history_page.findChild(QTableWidget)
            if table and table.rowCount() == 0:
                self._on_video_page_refresh()
        elif index == 5 and self.sub_table.rowCount() == 0:
            self._load_subscriptions()

    def _on_follow_select_all(self, checked: bool):
        self._on_table_select_all(self.follow_table, checked)

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
            mid = int(self.follow_table.item(row, 1).text() or 0)
            uname = self.follow_table.item(row, 2).text() or ""
            if mid:
                up_list.append({"mid": mid, "uname": uname})
        if not up_list:
            return
        dialog = MultiUpDownloadDialog(self.web, up_list, self.download_callback, self)
        dialog.exec()

    def _on_download_selected_group_members(self):
        rows = self._get_table_selected_rows(self.group_member_table)
        if not rows:
            QMessageBox.information(self, "提示", "请先勾选UP主")
            return
        up_list = []
        for row in rows:
            mid = int(self.group_member_table.item(row, 1).text() or 0)
            uname = self.group_member_table.item(row, 2).text() or ""
            if mid:
                up_list.append({"mid": mid, "uname": uname})
        if not up_list:
            return
        dialog = MultiUpDownloadDialog(self.web, up_list, self.download_callback, self)
        dialog.exec()

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

    def __init__(self, web_client, mid: int, uname: str, download_callback: Callable, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{uname} 的视频列表")
        self.setMinimumSize(900, 600)
        self.web = web_client
        self.mid = mid
        self.uname = uname
        self.download_callback = download_callback
        self.videos: List[Dict] = []
        self.wbi = WBI(web_client.sessdata)

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
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["", "BV号", "标题", "发布时间", "时长", "操作"])
        self.table.setColumnWidth(0, 30)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 380)
        self.table.setColumnWidth(3, 150)
        self.table.setColumnWidth(4, 80)
        self.table.setColumnWidth(5, 80)
        self.table.horizontalHeader().setStretchLastSection(True)
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
                self.table.setItem(i, 1, QTableWidgetItem(v.get("bvid", "")))
                self.table.setItem(i, 2, QTableWidgetItem(v.get("title", "")))
                self.table.setItem(i, 3, QTableWidgetItem(_ts_to_str(v.get("created", 0))))
                self.table.setItem(i, 4, QTableWidgetItem(self._format_duration(v.get("length", 0))))
                btn = QPushButton("下载")
                bvid = v.get("bvid", "")
                title = v.get("title", "")
                btn.clicked.connect(lambda _, b=bvid, t=title: self._download_one(b, t))
                self.table.setCellWidget(i, 5, btn)
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
        videos = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                bvid = self.table.item(row, 1).text() or ""
                title = self.table.item(row, 2).text() or ""
                if bvid:
                    videos.append({"bvid": bvid, "title": title, "uname": self.uname})
        if not videos:
            QMessageBox.information(self, "提示", "请先勾选视频")
            return
        self.download_callback(videos)


class FavVideoDialog(QDialog):
    """收藏夹视频列表弹窗"""

    def __init__(self, web_client, media_id: int, title: str, download_callback: Callable, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"收藏夹: {title}")
        self.setMinimumSize(900, 600)
        self.web = web_client
        self.media_id = media_id
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
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["", "BV号", "标题", "UP主", "收藏时间", "操作"])
        self.table.setColumnWidth(0, 30)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 340)
        self.table.setColumnWidth(3, 120)
        self.table.setColumnWidth(4, 150)
        self.table.setColumnWidth(5, 80)
        self.table.horizontalHeader().setStretchLastSection(True)
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
                self.table.setItem(i, 1, QTableWidgetItem(v.get("bvid", "")))
                self.table.setItem(i, 2, QTableWidgetItem(v.get("title", "")))
                self.table.setItem(i, 3, QTableWidgetItem(v.get("uname", "")))
                self.table.setItem(i, 4, QTableWidgetItem(_ts_to_str(v.get("fav_time", 0))))
                btn = QPushButton("下载")
                bvid = v.get("bvid", "")
                title = v.get("title", "")
                uname = v.get("uname", "")
                btn.clicked.connect(lambda _, b=bvid, t=title, u=uname: self._download_one(b, t, u))
                self.table.setCellWidget(i, 5, btn)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"获取收藏夹视频失败: {e}")

    def _download_one(self, bvid: str, title: str, uname: str):
        self.download_callback([{"bvid": bvid, "title": title, "uname": uname}])

    def _on_download_selected(self):
        videos = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                bvid = self.table.item(row, 1).text() or ""
                title = self.table.item(row, 2).text() or ""
                uname = self.table.item(row, 3).text() or ""
                if bvid:
                    videos.append({"bvid": bvid, "title": title, "uname": uname})
        if not videos:
            QMessageBox.information(self, "提示", "请先勾选视频")
            return
        self.download_callback(videos)


class MultiUpDownloadDialog(QDialog):
    """批量下载多个 UP 主全部视频的确认/进度弹窗"""

    def __init__(self, web_client, up_list: List[Dict], download_callback: Callable, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量下载UP主视频")
        self.setMinimumSize(600, 400)
        self.web = web_client
        self.up_list = up_list
        self.download_callback = download_callback
        self.videos: List[Dict] = []
        self.wbi = WBI(web_client.sessdata)

        self._build_ui()
        self._load_all_videos()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel(f"共选中 {len(self.up_list)} 个UP主，正在加载视频列表..."))
        self.info_label = QLabel("")
        layout.addWidget(self.info_label)

        self.progress = QProgressDialog("加载中...", "取消", 0, len(self.up_list), self)
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.canceled.connect(self.reject)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["", "BV号", "标题", "UP主", "发布时间"])
        self.table.setColumnWidth(0, 30)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 380)
        self.table.setColumnWidth(3, 120)
        self.table.setColumnWidth(4, 150)
        self.table.horizontalHeader().setStretchLastSection(True)
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

    def _load_all_videos(self):
        self.videos = []
        for i, up in enumerate(self.up_list):
            if self.progress.wasCanceled():
                return
            self.progress.setValue(i)
            self.info_label.setText(f"正在加载: {up.get('uname', '')}")
            try:
                vs = fetch_up_videos(self.web, up.get("mid", 0), wbi=self.wbi)
                for v in vs:
                    v["uname"] = up.get("uname", "")
                self.videos.extend(vs)
            except Exception as e:
                logger.warning(f"[UserCenter] 加载 {up.get('uname', '')} 视频失败: {e}")
            # 避免请求过快
            import time
            time.sleep(0.3)
        self.progress.setValue(len(self.up_list))
        self.info_label.setText(f"共加载 {len(self.videos)} 个视频")
        self._render_videos()

    def _render_videos(self):
        self.table.setRowCount(len(self.videos))
        for i, v in enumerate(self.videos):
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Unchecked)
            self.table.setItem(i, 0, item)
            self.table.setItem(i, 1, QTableWidgetItem(v.get("bvid", "")))
            self.table.setItem(i, 2, QTableWidgetItem(v.get("title", "")))
            self.table.setItem(i, 3, QTableWidgetItem(v.get("uname", "")))
            self.table.setItem(i, 4, QTableWidgetItem(_ts_to_str(v.get("created", 0))))

    def _on_download_selected(self):
        logger.info("[MultiUpDownload] 点击批量下载选中视频")
        videos = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                bvid = self.table.item(row, 1).text() or ""
                title = self.table.item(row, 2).text() or ""
                uname = self.table.item(row, 3).text() or ""
                if bvid:
                    videos.append({"bvid": bvid, "title": title, "uname": uname})
        logger.info(f"[MultiUpDownload] 勾选视频数量: {len(videos)}")
        if not videos:
            QMessageBox.information(self, "提示", "请先勾选视频")
            return
        try:
            self.download_callback(videos)
            logger.info("[MultiUpDownload] 回调执行成功")
        except Exception:
            logger.exception("[MultiUpDownload] 回调执行失败")
            QMessageBox.critical(self, "错误", "启动下载失败，请查看日志")
            return
        QMessageBox.information(self, "提示", f"已将 {len(videos)} 个视频加入下载队列")


# ---------- B站 API 封装 ----------

def fetch_followings(web) -> List[Dict]:
    """获取我的关注列表（全部）"""
    result = []
    pn = 1
    ps = 50
    while True:
        data = web.request(
            "https://api.bilibili.com/x/relation/followings",
            referer="https://space.bilibili.com",
            params={"vmid": web.dedeuserid or "0", "pn": pn, "ps": ps, "order": "desc"},
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


def fetch_fav_folders(web) -> List[Dict]:
    """获取收藏夹列表"""
    data = web.request(
        "https://api.bilibili.com/x/v3/fav/folder/created/list-all",
        referer="https://space.bilibili.com",
        params={"up_mid": int(web.dedeuserid or 0)},
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
