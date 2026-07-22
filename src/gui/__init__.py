"""
GUI 公共工具：应用图标与全局样式。
"""
import os

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..config_loader import BASE_DIR


APP_ICON_PATH = os.path.join(BASE_DIR, "data", "icon.png")


def load_app_icon() -> QIcon:
    """加载应用图标；若不存在则返回空图标，避免崩溃。"""
    if os.path.exists(APP_ICON_PATH):
        return QIcon(APP_ICON_PATH)
    return QIcon()


# 统一深色主题样式表
GLOBAL_STYLE_SHEET = """
/* 基础调色板 */
QWidget {
    background-color: #1e1e1e;
    color: #e0e0e0;
    font-size: 13px;
    font-family: "Microsoft YaHei UI", "PingFang SC", "Segoe UI", sans-serif;
}

QMainWindow, QDialog {
    background-color: #1e1e1e;
}

/* 链接与提示 */
QLabel {
    color: #e0e0e0;
    background: transparent;
}

QLabel[link="true"] {
    color: #4fc3f7;
}

/* 顶部标题 */
QLabel#title_label {
    font-size: 18px;
    font-weight: bold;
    color: #ffffff;
}

/* 按钮统一样式 */
QPushButton {
    background-color: #333333;
    color: #ffffff;
    border: 1px solid #4a4a4a;
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 28px;
    font-size: 13px;
}

QPushButton:hover {
    background-color: #404040;
    border-color: #5a5a5a;
}

QPushButton:pressed {
    background-color: #505050;
}

QPushButton:disabled {
    background-color: #2a2a2a;
    color: #777777;
    border-color: #3a3a3a;
}

QPushButton#primary_btn {
    background-color: #2196F3;
    color: #ffffff;
    border: none;
    font-weight: bold;
}

QPushButton#primary_btn:hover {
    background-color: #1976D2;
}

QPushButton#primary_btn:pressed {
    background-color: #1565C0;
}

QPushButton#danger_btn {
    background-color: #f44336;
    color: #ffffff;
    border: none;
    font-weight: bold;
}

QPushButton#danger_btn:hover {
    background-color: #d32f2f;
}

QPushButton#success_btn {
    background-color: #4CAF50;
    color: #ffffff;
    border: none;
    font-weight: bold;
}

QPushButton#success_btn:hover {
    background-color: #388E3C;
}

/* 标签页 */
QTabWidget::pane {
    border: 1px solid #333333;
    border-radius: 8px;
    background-color: #252525;
    top: -1px;
}

QTabBar::tab {
    background-color: #2a2a2a;
    color: #aaaaaa;
    border: 1px solid #333333;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 16px;
    margin-right: 2px;
    min-width: 70px;
}

QTabBar::tab:selected {
    background-color: #252525;
    color: #ffffff;
    border-color: #333333;
}

QTabBar::tab:hover:!selected {
    background-color: #333333;
    color: #e0e0e0;
}

/* 分组框 */
QGroupBox {
    background-color: #252525;
    border: 1px solid #333333;
    border-radius: 8px;
    margin-top: 18px;
    padding-top: 16px;
    padding-bottom: 14px;
    padding-left: 16px;
    padding-right: 16px;
    font-weight: bold;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 16px;
    top: -8px;
    padding: 2px 8px;
    color: #bbbbbb;
    background-color: #252525;
}

/* 输入框 */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox, QDateTimeEdit {
    background-color: #2a2a2a;
    color: #e0e0e0;
    border: 1px solid #444444;
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 22px;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus, QDateTimeEdit:focus {
    border-color: #2196F3;
}

QLineEdit:disabled, QTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled, QDateTimeEdit:disabled {
    background-color: #252525;
    color: #777777;
    border-color: #333333;
}

QSpinBox::up-button, QSpinBox::down-button {
    background-color: #3a3a3a;
    border: 1px solid #444444;
    width: 18px;
}

QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background-color: #4a4a4a;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #aaaaaa;
    width: 0px;
    height: 0px;
}

QComboBox QAbstractItemView {
    background-color: #2a2a2a;
    color: #e0e0e0;
    border: 1px solid #444444;
    selection-background-color: #2196F3;
}

QDateTimeEdit::drop-down {
    border: none;
    width: 24px;
}

/* 表格 */
QTableWidget {
    background-color: #252525;
    alternate-background-color: #2a2a2a;
    border: 1px solid #333333;
    border-radius: 6px;
    gridline-color: #333333;
    selection-background-color: #1E88E5;
    selection-color: #ffffff;
}

QTableWidget::item {
    padding: 6px;
    border-bottom: 1px solid #2f2f2f;
}

QTableWidget::item:selected {
    background-color: #1E88E5;
}

QHeaderView::section {
    background-color: #2a2a2a;
    color: #ffffff;
    padding: 7px;
    border: none;
    border-bottom: 1px solid #333333;
    border-right: 1px solid #333333;
    font-weight: bold;
}

QHeaderView::section:last {
    border-right: none;
}

QTableCornerButton::section {
    background-color: #2a2a2a;
    border: none;
}

/* 列表 */
QListWidget {
    background-color: #252525;
    border: 1px solid #333333;
    border-radius: 6px;
    outline: none;
}

QListWidget::item {
    padding: 10px 12px;
    border-radius: 4px;
    border-bottom: 1px solid #2f2f2f;
}

QListWidget::item:hover {
    background-color: #333333;
}

QListWidget::item:selected {
    background-color: #1E88E5;
    color: #ffffff;
}

/* 复选框 */
QCheckBox {
    spacing: 6px;
    color: #e0e0e0;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #555555;
    border-radius: 3px;
    background-color: #2a2a2a;
}

QCheckBox::indicator:checked {
    background-color: #2196F3;
    border-color: #2196F3;
}

QCheckBox::indicator:hover {
    border-color: #777777;
}

/* 进度条 */
QProgressBar {
    border: 1px solid #333333;
    border-radius: 4px;
    background-color: #2a2a2a;
    text-align: center;
    color: #ffffff;
    min-height: 18px;
}

QProgressBar::chunk {
    background-color: #4CAF50;
    border-radius: 3px;
}

/* 滚动条 */
QScrollBar:vertical {
    background-color: #252525;
    width: 10px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background-color: #4a4a4a;
    min-height: 30px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background-color: #5a5a5a;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background-color: #252525;
    height: 10px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background-color: #4a4a4a;
    min-width: 30px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #5a5a5a;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* 菜单与工具提示 */
QMenu {
    background-color: #2a2a2a;
    color: #e0e0e0;
    border: 1px solid #444444;
}

QMenu::item:selected {
    background-color: #2196F3;
}

QToolTip {
    background-color: #333333;
    color: #ffffff;
    border: 1px solid #555555;
    padding: 4px 8px;
    border-radius: 4px;
}

/* 表单布局标签 */
QFormLayout QLabel {
    color: #bbbbbb;
}

/* 分割线 */
QSplitter::handle {
    background-color: #333333;
}

QSplitter::handle:horizontal {
    width: 2px;
}

QSplitter::handle:vertical {
    height: 2px;
}
"""


def apply_global_style(app: QApplication) -> None:
    """为整个应用设置统一样式表。"""
    app.setStyleSheet(GLOBAL_STYLE_SHEET)
