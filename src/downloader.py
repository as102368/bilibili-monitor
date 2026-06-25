import glob
import os
import random
import re
import subprocess
import sys
import threading
import time
from typing import Callable, Dict, Optional

import requests

from .video_stream import VideoStream
from .web_client import BilibiliWebClient
from .ctfile_uploader import CtfileUploader
from .database import DownloadDB
from .logger import get_logger
from .progress import (
    emit_download_started,
    emit_download_progress,
    emit_download_finished,
)

logger = get_logger(__name__)


class _DownloadProgress:
    """聚合多路流（视频+音频）的下载进度，并按合并总大小统一发射 UI 信号。"""

    def __init__(self, bvid: str):
        self.bvid = bvid
        self._lock = threading.Lock()
        self._streams: Dict[str, Dict[str, int]] = {}
        self._last_percent = -3

    def register_stream(self, stream_key: str, total: int):
        with self._lock:
            self._streams[stream_key] = {"total": max(0, total), "current": 0}

    def update_stream(self, stream_key: str, current: int):
        with self._lock:
            stream = self._streams.setdefault(stream_key, {"total": 0, "current": 0})
            stream["current"] = max(0, current)
            total = sum(s["total"] for s in self._streams.values())
            current_total = sum(s["current"] for s in self._streams.values())
            if total > 0:
                percent = int(min(100, current_total / total * 100))
                if percent - self._last_percent >= 3:
                    emit_download_progress(self.bvid, percent)
                    self._last_percent = percent

    def finish(self):
        emit_download_progress(self.bvid, 100)


def _get_ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    # 优先使用项目内置的 ffmpeg
    builtin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ffmpeg", "bin", "ffmpeg.exe")
    if os.path.isfile(builtin):
        return builtin
    return "ffmpeg"


def _get_aria2c_path() -> str:
    """优先查找项目内置的 aria2c，其次查找 PATH 中的 aria2c"""
    builtin_pattern = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "tools", "aria2", "**", "aria2c.exe"
    )
    try:
        matches = glob.glob(builtin_pattern, recursive=True)
        if matches:
            return matches[0]
    except Exception:
        pass
    # 检查 PATH
    for cmd in ["aria2c.exe", "aria2c"]:
        for path in os.environ.get("PATH", "").split(os.pathsep):
            exe = os.path.join(path.strip('"'), cmd)
            if os.path.isfile(exe):
                return exe
    return "aria2c"


def _aria2c_available(path: str) -> bool:
    try:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.run([path, "-v"], check=True, **kwargs)
        return True
    except Exception:
        return False


class Downloader:
    """
    仿照 DownKyi：使用 B站 API 获取直链，requests 下载，FFmpeg 合并。
    完全绕过 yt-dlp 以避免 412 风控拦截。
    """

    # 画质代码映射（qn -> 最高允许 id）
    QUALITY_MAP = {
        "8K": 127,
        "4K": 120,
        "1080P60": 116,
        "1080P+": 112,
        "1080P": 80,
        "720P60": 74,
        "720P": 64,
        "480P": 32,
        "360P": 16,
        "best": 127,
    }

    # 实际流 id -> B站画质名称（兜底用，优先按分辨率判断）
    QN_LABEL_MAP = {
        127: "8K 超高清",
        126: "杜比视界",
        125: "HDR 真彩",
        120: "4K 超高清",
        116: "1080P 高帧率",
        112: "1080P 高码率",
        80: "1080P 高清",
        74: "720P 高帧率",
        64: "720P 高清",
        32: "480P 标清",
        16: "360P 流畅",
    }

    def __init__(
        self,
        output_dir: str,
        quality: str,
        template: str,
        web_client: BilibiliWebClient,
        video_stream: VideoStream,
        ctfile_uploader: Optional[CtfileUploader] = None,
        db: Optional[DownloadDB] = None,
        time_format: str = "yyyy-MM-dd HH-mm-ss",
        index_format: str = "自然数",
    ):
        self.output_dir = output_dir
        self.quality = quality
        self.template = template
        self.web = web_client
        self.video_stream = video_stream
        self.ctfile_uploader = ctfile_uploader
        self.db = db
        self.time_format = time_format
        self.index_format = index_format
        self.ffmpeg_path = _get_ffmpeg_path()
        self.aria2c_path = _get_aria2c_path()
        self.use_aria2 = _aria2c_available(self.aria2c_path)
        if self.use_aria2:
            logger.info(f"[Downloader] 使用 Aria2 下载器: {self.aria2c_path}")
        else:
            logger.warning("[Downloader] 未检测到 Aria2，将回退到 requests 下载")
        os.makedirs(output_dir, exist_ok=True)

    def _get_quality_label(self, stream: dict) -> str:
        """按 B 站画质 ID 返回官方画质名称"""
        sid = stream.get("id", 0) or 0
        return self.QN_LABEL_MAP.get(sid, f"{sid}P")

    def _sanitize_filename(self, name: str) -> str:
        """去除文件名中的非法字符"""
        return re.sub(r'[\\/:*?"<>|]', "", name)

    def _build_filename(self, info: dict, quality: str = "", index: int = 1) -> str:
        """根据模板构建文件名，支持 yt-dlp 风格的 %(placeholder)s 格式"""
        owner = info.get("owner", {})
        pages = info.get("pages", [])
        part_title = pages[0].get("part", "") if pages else ""

        pubdate_ts = info.get("pubdate")
        pubdate_str = ""
        if pubdate_ts:
            from datetime import datetime
            fmt_map = {
                "yyyy-MM-dd": "%Y-%m-%d",
                "yyyy-MM-dd HH-mm-ss": "%Y-%m-%d-%H-%M-%S",
                "yyyyMMdd": "%Y%m%d",
                "yyyy/MM/dd": "%Y/%m/%d",
            }
            dt_fmt = fmt_map.get(self.time_format, "%Y-%m-%d-%H-%M-%S")
            pubdate_str = datetime.fromtimestamp(pubdate_ts).strftime(dt_fmt)

        if self.index_format == "两位数字":
            index_str = f"{index:02d}"
        elif self.index_format == "三位数字":
            index_str = f"{index:03d}"
        else:
            index_str = str(index)

        mapping = {
            "%(uploader)s": self._sanitize_filename(owner.get("name", "")),
            "%(title)s": self._sanitize_filename(info.get("title", "")),
            "%(id)s": info.get("bvid", ""),
            "%(bvid)s": info.get("bvid", ""),
            "%(avid)s": str(info.get("aid", "")),
            "%(cid)s": str(info.get("cid", "")),
            "%(uploader_id)s": str(owner.get("mid", "")),
            "%(category)s": info.get("tname", ""),
            "%(part_title)s": self._sanitize_filename(part_title),
            "%(upload_date)s": pubdate_str,
            "%(quality)s": quality,
            "%(ext)s": "mp4",
            "%(index)s": index_str,
            "%(section)s": "",
            "%(audio_quality)s": "",
            "%(video_codec)s": "",
        }

        filename = self.template
        for old, new in mapping.items():
            filename = filename.replace(old, new)
        return filename

    def _get_stream_size(self, url: str, referer: str) -> int:
        """通过 HEAD 预获取流大小，失败时返回 0。"""
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": referer,
            }
            cookies = self.web.get_cookies_dict()
            resp = requests.head(url, headers=headers, cookies=cookies, timeout=15)
            if resp.status_code == 200:
                return int(resp.headers.get("content-length", 0))
        except Exception:
            pass
        return 0

    def _download_file(
        self,
        url: str,
        output_path: str,
        referer: str,
        bvid: str = "",
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """使用 Aria2c 下载单个文件，失败时回退到 requests，带指数退避重试。

        progress_callback(current: int, total: int) 供调用方聚合多路流进度。
        """
        max_retries = 3
        # 批量下载前小睡一会，降低请求瞬时并发
        time.sleep(random.uniform(0.5, 2.0))

        for attempt in range(max_retries):
            ok = False
            if self.use_aria2:
                ok = self._download_file_aria2(url, output_path, referer, bvid, progress_callback)
                if ok:
                    return True
                logger.warning(
                    f"[Download] Aria2 尝试 {attempt + 1}/{max_retries} 失败: {os.path.basename(output_path)}"
                )
            # Aria2 不可用或失败后，使用 requests 再试一次
            ok = self._download_file_requests(url, output_path, referer, bvid, progress_callback)
            if ok:
                return True
            logger.warning(
                f"[Download] requests 尝试 {attempt + 1}/{max_retries} 失败: {os.path.basename(output_path)}"
            )

            if attempt < max_retries - 1:
                # 指数退避 + 随机抖动，遇到风控时留足冷却时间
                sleep_time = min(5 * (2 ** attempt) + random.uniform(0, 5), 120)
                logger.info(f"[Download] {sleep_time:.1f}秒后重试...")
                time.sleep(sleep_time)
        logger.error(f"[Download] 全部 {max_retries} 次尝试均失败: {os.path.basename(output_path)}")
        return False

    def _download_file_aria2(
        self,
        url: str,
        output_path: str,
        referer: str,
        bvid: str = "",
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """调用 aria2c 进行多线程下载，并通过轮询文件大小反馈进度。"""
        try:
            output_dir = os.path.dirname(os.path.abspath(output_path))
            output_name = os.path.basename(output_path)
            cookie_str = self.web.get_cookie_string()

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": referer,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            }

            # 预获取总大小，用于进度计算；失败也不影响下载
            total_size = self._get_stream_size(url, referer)
            if progress_callback:
                progress_callback(0, total_size)

            cmd = [
                self.aria2c_path,
                url,
                "-o", output_name,
                "--dir", output_dir,
                "--header", f"User-Agent: {headers['User-Agent']}",
                "--header", f"Referer: {headers['Referer']}",
                "--header", f"Accept: {headers['Accept']}",
                "--header", f"Accept-Language: {headers['Accept-Language']}",
                # 进一步降低连接数，避免批量下载时触发风控
                "-x", "2",
                "-s", "2",
                "-k", "1M",
                "--max-connection-per-server", "2",
                "--max-tries", "3",
                "--retry-wait", "3",
                "--timeout", "60",
                "--auto-file-renaming=false",
                "--allow-overwrite=true",
                "--quiet",
                "--console-log-level=warn",
            ]
            if cookie_str:
                cmd.extend(["--header", f"Cookie: {cookie_str}"])

            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            logger.info(f"[Aria2] 开始下载: {output_name}")
            proc = subprocess.Popen(cmd, **kwargs)

            # 轮询进度
            last_emitted = -3
            while proc.poll() is None:
                try:
                    current_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                    if progress_callback:
                        progress_callback(current_size, total_size)
                    elif total_size > 0 and bvid:
                        # 兼容旧调用：直接按单路流发射
                        percent = int(current_size / total_size * 100)
                        if percent - last_emitted >= 5:
                            emit_download_progress(bvid, percent)
                            logger.info(f"[Aria2] {output_name}: {percent}%")
                            last_emitted = percent
                except Exception:
                    pass
                time.sleep(1.0)

            proc.wait()
            if proc.returncode == 0:
                if progress_callback:
                    progress_callback(total_size, total_size)
                elif bvid:
                    emit_download_progress(bvid, 100)
                logger.info(f"[Aria2] 下载完成: {output_name}")
                return True
            else:
                logger.error(f"[Aria2] 下载失败 {url} (exit code {proc.returncode})")
                return False
        except subprocess.CalledProcessError as e:
            logger.error(f"[Aria2] 下载失败 {url} (exit code {e.returncode})")
            return False
        except Exception as e:
            logger.error(f"[Aria2] 下载异常 {url}: {e}")
            return False

    def _download_file_requests(
        self,
        url: str,
        output_path: str,
        referer: str,
        bvid: str = "",
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """使用 requests 下载单个文件（回退方案）"""
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": referer,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
            }
            cookies = self.web.get_cookies_dict()
            with requests.get(url, headers=headers, cookies=cookies, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                last_log_percent = -3
                with open(output_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                progress_callback(downloaded, total)
                            elif total > 0 and bvid:
                                percent = int(downloaded / total * 100)
                                if percent - last_log_percent >= 5:
                                    logger.info(f"[Download] {os.path.basename(output_path)}: {percent}%")
                                    emit_download_progress(bvid, percent)
                                    last_log_percent = percent
            return True
        except Exception as e:
            logger.error(f"[Download] 下载失败 {url}: {e}")
            return False

    def _merge_with_ffmpeg(self, video_path: str, audio_path: str, output_path: str) -> bool:
        """使用 FFmpeg 合并音视频（DASH 格式）"""
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        try:
            logger.info(f"[FFmpeg] 合并中... {os.path.basename(output_path)}")
            kwargs = {
                "check": True,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.run(cmd, **kwargs)
            os.remove(video_path)
            os.remove(audio_path)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"[FFmpeg] 合并失败 (exit code {e.returncode})")
            return False

    def _record_failure(self, bvid: str, title: str, uploader: str, reason: str):
        if self.db:
            self.db.add_failure(bvid, title, uploader, reason)

    def download(self, bvid: str, title: str, uname: str) -> dict:
        """
        主下载入口。仿照 DownKyi 下载链路：
        1. 获取视频详情 (cid)
        2. 获取 playurl (DASH 直链)
        3. 下载视频流 + 音频流
        4. FFmpeg 合并
        返回 {"success": bool, "quality": str, "output_path": str}
        上传逻辑已解耦到 UploadManager，由调用方在下载成功后自行入队。
        """
        emit_download_started(bvid, title, uname)

        # 1. 获取视频详情
        info = self.video_stream.get_video_info(bvid)
        if not info:
            logger.error(f"[Download] 无法获取视频详情: {bvid}")
            error_info = self.video_stream.last_video_info_error
            if error_info:
                code = error_info.get("code")
                if code == -404:
                    reason = "视频不存在或已删除"
                elif code == 62002:
                    reason = "视频已被UP主隐藏"
                elif code == 62004:
                    reason = "视频正在审核中"
                elif code == 62012:
                    reason = "视频仅UP主自己可见"
                elif code in (-412, 412):
                    reason = "被风控拦截（412）"
                else:
                    reason = f"无法获取视频详情（code={code}）"
            else:
                reason = "无法获取视频详情（网络异常或无响应）"
            self._record_failure(bvid, title, uname, reason)
            emit_download_finished(bvid, False, reason)
            return {"success": False, "quality": "", "reason": reason}

        cid = info.get("cid")
        if not cid:
            logger.error(f"[Download] 无法获取 cid: {bvid}")
            reason = "无法获取视频 cid"
            self._record_failure(bvid, title, uname, reason)
            emit_download_finished(bvid, False, reason)
            return {"success": False, "quality": "", "reason": reason}

        # 检查视频属性：充电专属、付费等
        is_upower_exclusive = info.get("is_upower_exclusive", False)
        rights = info.get("rights", {})
        is_ugc_pay = bool(rights.get("ugc_pay"))
        is_pay = bool(rights.get("pay"))
        is_arc_pay = bool(rights.get("arc_pay"))

        # 画质名称先初始化为空，避免后续失败分支引用未定义变量
        quality_str = ""

        # 2. 获取 playurl
        target_qn = self.QUALITY_MAP.get(self.quality, 125)
        playurl = self.video_stream.get_playurl(bvid, cid, qn=target_qn)
        if not playurl:
            logger.error(f"[Download] 无法获取 playurl: {bvid}")
            error_info = self.video_stream.last_playurl_error
            if error_info:
                code = error_info.get("code")
                message = error_info.get("message", "")
                if code in (10001003, 10001004):
                    reason = "充电专属视频，当前账号未开通包月充电"
                elif code == -404:
                    reason = "视频不存在或已删除"
                elif code in (-412, 412):
                    reason = "被风控拦截（412）"
                elif code == -403:
                    reason = "权限不足（可能需大会员或充电）"
                elif is_upower_exclusive or is_arc_pay:
                    reason = "充电专属视频，当前账号未开通包月充电"
                elif is_ugc_pay or is_pay:
                    reason = "付费视频，当前账号未购买"
                else:
                    reason = f"无法获取播放地址（code={code}, message={message}）"
            else:
                reason = "无法获取播放地址（网络异常或无响应）"
            self._record_failure(bvid, title, uname, reason)
            emit_download_finished(bvid, False, reason)
            return {"success": False, "quality": quality_str, "reason": reason}

        # 3. 解析 DASH 直链
        dash = playurl.get("dash")
        if not dash:
            logger.error(f"[Download] 视频不支持 DASH 格式: {bvid}")
            reason = "充电专属视频，当前账号未开通包月充电"
            self._record_failure(bvid, title, uname, reason)
            emit_download_finished(bvid, False, reason)
            return {"success": False, "quality": quality_str, "reason": reason}

        video_streams = dash.get("video", [])
        audio_streams = dash.get("audio", [])

        if not video_streams:
            logger.error(f"[Download] 无可用视频流: {bvid}")
            reason = "无可用视频流"
            self._record_failure(bvid, title, uname, reason)
            emit_download_finished(bvid, False, reason)
            return {"success": False, "quality": quality_str, "reason": reason}

        video_stream = self.video_stream.select_best_stream(video_streams, target_qn)
        audio_stream = self.video_stream.select_best_stream(audio_streams, 9999) if audio_streams else None

        if not video_stream:
            logger.error(f"[Download] 无法选择合适的视频流: {bvid}")
            reason = "无法选择合适的视频流（可能画质不可用）"
            self._record_failure(bvid, title, uname, reason)
            emit_download_finished(bvid, False, reason)
            return {"success": False, "quality": quality_str, "reason": reason}

        video_url = video_stream.get("base_url")
        if not video_url:
            # 尝试备用地址
            backup_urls = video_stream.get("backup_url", [])
            video_url = backup_urls[0] if backup_urls else None

        audio_url = None
        if audio_stream:
            audio_url = audio_stream.get("base_url")
            if not audio_url:
                backup_urls = audio_stream.get("backup_url", [])
                audio_url = backup_urls[0] if backup_urls else None

        if not video_url:
            logger.error(f"[Download] 无可用下载链接: {bvid}")
            reason = "无可用下载链接"
            self._record_failure(bvid, title, uname, reason)
            emit_download_finished(bvid, False, reason)
            return {"success": False, "quality": quality_str, "reason": reason}

        # 4. 构建输出文件名
        quality_str = self._get_quality_label(video_stream)
        output_name = self._build_filename(info, quality_str)
        # 如果没有扩展名，加上 .mp4
        if not output_name.lower().endswith(".mp4"):
            output_name += ".mp4"
        output_path = os.path.join(self.output_dir, output_name)

        # 5. 下载
        referer = f"https://www.bilibili.com/video/{bvid}"
        progress = _DownloadProgress(bvid)

        if audio_url:
            # DASH 格式：分别下载视频和音频，再合并
            video_tmp = output_path + ".video.m4s"
            audio_tmp = output_path + ".audio.m4s"

            video_total = self._get_stream_size(video_url, referer)
            audio_total = self._get_stream_size(audio_url, referer)
            progress.register_stream("video", video_total)
            progress.register_stream("audio", audio_total)

            ok1 = self._download_file(
                video_url, video_tmp, referer, bvid,
                progress_callback=lambda c, t: progress.update_stream("video", c),
            )
            ok2 = self._download_file(
                audio_url, audio_tmp, referer, bvid,
                progress_callback=lambda c, t: progress.update_stream("audio", c),
            )
            if not (ok1 and ok2):
                reason = "音视频流下载失败（网络异常或被拦截）"
                self._record_failure(bvid, title, uname, reason)
                emit_download_finished(bvid, False, reason)
                return {"success": False, "quality": quality_str, "reason": reason}

            progress.finish()
            success = self._merge_with_ffmpeg(video_tmp, audio_tmp, output_path)
            if not success:
                reason = "FFmpeg 音视频合成失败"
                self._record_failure(bvid, title, uname, reason)
                emit_download_finished(bvid, False, reason)
                return {"success": False, "quality": quality_str, "reason": reason}
        else:
            # 无音频分离，直接下载视频（MP4 直链）
            total = self._get_stream_size(video_url, referer)
            progress.register_stream("single", total)
            success = self._download_file(
                video_url, output_path, referer, bvid,
                progress_callback=lambda c, t: progress.update_stream("single", c),
            )
            if not success:
                reason = "视频下载失败（网络异常或被拦截）"
                self._record_failure(bvid, title, uname, reason)
                emit_download_finished(bvid, False, reason)
                return {"success": False, "quality": quality_str, "reason": reason}
            progress.finish()

        # 记录文件元数据，供上传记录反查 BV/标题/UP主
        if self.db:
            self.db.add_file_metadata(output_path, bvid, title, uname)

        emit_download_finished(bvid, True, "下载完成")
        # 下载完成后返回本地文件路径，上传由 UploadManager 异步批量处理
        return {"success": True, "quality": quality_str, "output_path": output_path}
