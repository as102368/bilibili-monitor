"""
进度/状态追踪器。

- 单例，供下载器、上传管理器在后台线程中发射信号。
- GUI 的“运行状态”页连接这些信号并展示进度条。
- 非 GUI 环境（如 main.py 后台）下信号不会被处理，仅作为空转开销。
"""

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication


class ProgressTracker(QObject):
    # 下载：bvid, 标题, UP主
    download_started = Signal(str, str, str)
    # 下载进度：bvid, 百分比(0-100)
    download_progress = Signal(str, int)
    # 下载结束：bvid, 是否成功, 原因/消息
    download_finished = Signal(str, bool, str)

    # 上传：文件名
    upload_started = Signal(str)
    # 上传进度：文件名, 百分比(0-100)
    upload_progress = Signal(str, int)
    # 上传结束：文件名, 是否成功, 消息
    upload_finished = Signal(str, bool, str)

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


def _tracker():
    """仅在 GUI 事件循环存在时返回 tracker，否则返回 None，避免非 GUI 环境报错。"""
    app = QApplication.instance()
    if app is None:
        return None
    return ProgressTracker.instance()


def emit_download_started(bvid: str, title: str, uploader: str):
    t = _tracker()
    if t:
        t.download_started.emit(bvid, title, uploader)


def emit_download_progress(bvid: str, percent: int):
    t = _tracker()
    if t:
        t.download_progress.emit(bvid, max(0, min(100, percent)))


def emit_download_finished(bvid: str, success: bool, message: str = ""):
    t = _tracker()
    if t:
        t.download_finished.emit(bvid, success, message)


def emit_upload_started(file_name: str):
    t = _tracker()
    if t:
        t.upload_started.emit(file_name)


def emit_upload_progress(file_name: str, percent: int):
    t = _tracker()
    if t:
        t.upload_progress.emit(file_name, max(0, min(100, percent)))


def emit_upload_finished(file_name: str, success: bool, message: str = ""):
    t = _tracker()
    if t:
        t.upload_finished.emit(file_name, success, message)
