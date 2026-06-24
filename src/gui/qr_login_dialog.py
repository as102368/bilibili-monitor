import requests
import qrcode
from PIL.ImageQt import ImageQt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap


class QrLoginDialog(QDialog):
    """哔哩哔哩扫码登录对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("哔哩哔哩扫码登录")
        self.setFixedSize(340, 440)

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://passport.bilibili.com",
            }
        )

        self.qrcode_key = ""
        self.cookies = {}

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_status)

        self._build_ui()
        self._fetch_qr()

        self.timer.start(3000)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        tip = QLabel("请使用 哔哩哔哩 App 扫描下方二维码")
        tip.setAlignment(Qt.AlignCenter)
        tip.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 8px;")
        layout.addWidget(tip)

        self.qr_label = QLabel()
        self.qr_label.setFixedSize(220, 220)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setStyleSheet("border: 1px solid #ddd; background: #fff;")
        layout.addWidget(self.qr_label, alignment=Qt.AlignCenter)

        self.status_label = QLabel("正在获取二维码...")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #666; margin-top: 12px;")
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新二维码")
        self.refresh_btn.clicked.connect(self._fetch_qr)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

    def _fetch_qr(self):
        try:
            resp = self.session.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                timeout=10,
            )
            data = resp.json()
            if data.get("code") == 0:
                self.qrcode_key = data["data"]["qrcode_key"]
                url = data["data"]["url"]
                self._show_qr(url)
                self.status_label.setText("请使用哔哩哔哩App扫码登录")
                self.status_label.setStyleSheet("color: #666; margin-top: 12px;")
                if not self.timer.isActive():
                    self.timer.start(3000)
            else:
                self.status_label.setText(f"获取二维码失败: {data.get('message', '')}")
                self.status_label.setStyleSheet("color: red; margin-top: 12px;")
        except Exception as e:
            self.status_label.setText(f"获取二维码失败: {e}")
            self.status_label.setStyleSheet("color: red; margin-top: 12px;")

    def _show_qr(self, url: str):
        qr = qrcode.make(url)
        pil_image = qr.get_image()
        qt_image = ImageQt(pil_image)
        pixmap = QPixmap.fromImage(qt_image)
        scaled = pixmap.scaled(
            220, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.qr_label.setPixmap(scaled)

    def _poll_status(self):
        if not self.qrcode_key:
            return
        try:
            resp = self.session.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                params={"qrcode_key": self.qrcode_key},
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                return

            poll_data = data.get("data", {})
            poll_code = poll_data.get("code", -1)

            if poll_code == 86101:
                self.status_label.setText("请使用哔哩哔哩App扫码登录")
                self.status_label.setStyleSheet("color: #666; margin-top: 12px;")
            elif poll_code == 86090:
                self.status_label.setText("已扫描，请在手机上确认登录")
                self.status_label.setStyleSheet("color: orange; margin-top: 12px;")
            elif poll_code == 86038:
                self.status_label.setText("二维码已过期，请点击刷新")
                self.status_label.setStyleSheet("color: red; margin-top: 12px;")
                self.timer.stop()
            elif poll_code == 0:
                self.status_label.setText("登录成功，正在获取Cookie...")
                self.status_label.setStyleSheet("color: green; margin-top: 12px;")
                self.timer.stop()
                self._fetch_cookies(poll_data.get("url", ""))
        except Exception:
            pass

    def _fetch_cookies(self, cross_url: str):
        try:
            if cross_url:
                self.session.get(cross_url, timeout=10, allow_redirects=True)

            result = {}
            for cookie in self.session.cookies:
                if cookie.name in ["SESSDATA", "bili_jct", "DedeUserID", "buvid3"]:
                    val = cookie.value
                    if cookie.name == "SESSDATA":
                        val = requests.utils.unquote(val)
                    if val or cookie.name not in result:
                        result[cookie.name] = val

            if not result.get("SESSDATA"):
                QMessageBox.warning(self, "登录失败", "未能获取到有效的登录凭证")
                self.reject()
                return

            self.cookies = result
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "登录失败", f"获取Cookie失败: {e}")
            self.reject()

    def get_cookies(self) -> dict:
        return self.cookies

    def closeEvent(self, event):
        self.timer.stop()
        event.accept()
