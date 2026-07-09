import glob
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Dict, Optional

import requests

_PLACEHOLDER_RE = re.compile(
    r"%\((uploader|title|id|bvid|avid|cid|uploader_id|category|part_title|upload_date|quality|ext|index|section|audio_quality|video_codec)\)s"
)

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

# 单稿件/单P 最大允许下载大小：1GB
MAX_DOWNLOAD_SIZE = 1 * 1024 * 1024 * 1024


class _DownloadProgress:
    """聚合多路流（视频+音频）的下载进度，并按合并总大小统一发射 UI 信号。

    支持按多P划分进度区间：第 i/P 页仅更新 [min_percent, max_percent] 区间，
    避免多P下载时进度条反复归零。
    """

    def __init__(self, bvid: str, min_percent: int = 0, max_percent: int = 100):
        self.bvid = bvid
        self.min_percent = min_percent
        self.max_percent = max_percent
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
                inner = min(100.0, current_total / total * 100)
                percent = self.min_percent + int(
                    inner * (self.max_percent - self.min_percent) / 100
                )
                if percent - self._last_percent >= 3:
                    emit_download_progress(self.bvid, percent)
                    self._last_percent = percent

    def finish(self):
        emit_download_progress(self.bvid, self.max_percent)
        self._last_percent = self.max_percent


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
        self._last_video_aid = 0
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

    @staticmethod
    def _write_cookie_file(cookie_str: str) -> Optional[str]:
        """将 Cookie 字符串写入 Netscape 格式临时文件，避免在 aria2c 命令行暴露。"""
        if not cookie_str:
            return None
        try:
            fd, path = tempfile.mkstemp(suffix=".cookies.txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("# Netscape HTTP Cookie File\n")
                expiry = int(time.time()) + 365 * 24 * 3600
                for item in cookie_str.split(";"):
                    item = item.strip()
                    if "=" not in item:
                        continue
                    name, value = item.split("=", 1)
                    name = name.strip()
                    value = value.strip()
                    if not name:
                        continue
                    # domain, subdomains, path, secure, expiry, name, value
                    f.write(f".bilibili.com\tTRUE\t/\tFALSE\t{expiry}\t{name}\t{value}\n")
            return path
        except Exception:
            logger.exception("[Cookie] 写入临时 cookie 文件失败")
            return None

    # 音频流 id -> 可读音质（B站 DASH 音频常见 id）
    AUDIO_QUALITY_LABEL_MAP = {
        30280: "320K",
        30232: "128K",
        30216: "64K",
        30250: "Dolby",
    }

    @staticmethod
    def _extract_codec_family(codecs: str) -> str:
        """从 codecs 字符串中提取编码族，如 avc/hevc/av1。"""
        if not codecs:
            return ""
        c = codecs.lower()
        if "avc" in c or "h264" in c:
            return "avc"
        if "hev" in c or "h265" in c:
            return "hevc"
        if "av01" in c or "av1" in c:
            return "av1"
        return codecs.split(".")[0] if "." in codecs else codecs

    @staticmethod
    def _find_section_title(info: dict, cid: int) -> str:
        """根据 cid 在 UGC season 的分区中查找当前分集所属章节标题。"""
        cid = cid or info.get("cid", 0)
        ugc_season = info.get("ugc_season", {})
        for section in ugc_season.get("sections", []):
            section_title = section.get("title", "")
            for ep in section.get("episodes", []):
                if ep.get("cid") == cid:
                    return section_title
        return ugc_season.get("title", "")

    def _build_filename(
        self,
        info: dict,
        quality: str = "",
        index: int = 1,
        audio_stream: Optional[dict] = None,
        video_stream: Optional[dict] = None,
    ) -> str:
        """根据模板构建文件名，支持 yt-dlp 风格的 %(placeholder)s 格式。
        一次完成替换，避免值中的 %(xxx)s 被二次替换；并校验路径安全。"""
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

        cid = info.get("cid", 0)
        audio_qid = audio_stream.get("id", 0) if audio_stream else 0
        audio_quality_label = self.AUDIO_QUALITY_LABEL_MAP.get(
            audio_qid, str(audio_qid) if audio_qid else ""
        )
        video_codecs = video_stream.get("codecs", "") if video_stream else ""

        mapping = {
            "uploader": self._sanitize_filename(owner.get("name", "")),
            "title": self._sanitize_filename(info.get("title", "")),
            "id": info.get("bvid", ""),
            "bvid": info.get("bvid", ""),
            "avid": str(info.get("aid", "")),
            "cid": str(cid),
            "uploader_id": str(owner.get("mid", "")),
            "category": info.get("tname", ""),
            "part_title": self._sanitize_filename(part_title),
            "upload_date": pubdate_str,
            "quality": quality,
            "ext": "mp4",
            "index": index_str,
            "section": self._sanitize_filename(self._find_section_title(info, cid)),
            "audio_quality": audio_quality_label,
            "video_codec": self._extract_codec_family(video_codecs),
        }

        # 单次正则替换，并将值中的 % 临时转义，防止二次替换
        def _repl(m):
            key = m.group(1)
            return mapping.get(key, "").replace("%", "\x00")

        filename = _PLACEHOLDER_RE.sub(_repl, self.template)
        filename = filename.replace("\x00", "%")

        # 限制单文件名长度，避免 Windows 长路径问题
        max_len = 200
        if len(filename) > max_len:
            base, ext = os.path.splitext(filename)
            filename = base[: max_len - len(ext)] + ext

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
        单文件总耗时上限 8 分钟，超时后强制放弃，避免卡死整个队列。
        """
        max_retries = 2
        deadline = time.time() + 480  # 8 分钟单文件总耗时上限
        # 批量下载前小睡一会，降低请求瞬时并发
        time.sleep(random.uniform(0.5, 1.5))

        for attempt in range(max_retries):
            if time.time() > deadline:
                logger.error(
                    f"[Download] 超过单文件总耗时上限，放弃: {os.path.basename(output_path)}"
                )
                return False

            ok = False
            if self.use_aria2:
                ok = self._download_file_aria2(
                    url, output_path, referer, bvid, progress_callback, deadline=deadline
                )
                if ok:
                    return True
                logger.warning(
                    f"[Download] Aria2 尝试 {attempt + 1}/{max_retries} 失败: {os.path.basename(output_path)}"
                )
            # Aria2 不可用或失败后，使用 requests 再试一次
            ok = self._download_file_requests(
                url, output_path, referer, bvid, progress_callback, deadline=deadline
            )
            if ok:
                return True
            logger.warning(
                f"[Download] requests 尝试 {attempt + 1}/{max_retries} 失败: {os.path.basename(output_path)}"
            )

            if attempt < max_retries - 1:
                # 轻量退避，避免一个文件卡死整个队列
                sleep_time = min(5 + random.uniform(0, 3), 30)
                remaining = deadline - time.time()
                if remaining <= 0:
                    logger.error(
                        f"[Download] 超过单文件总耗时上限，放弃: {os.path.basename(output_path)}"
                    )
                    return False
                sleep_time = min(sleep_time, remaining)
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
        deadline: float = 0,
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

            # 根据剩余时间设置 aria2c 本次运行上限
            time_limit = "300"  # 默认 5 分钟
            if deadline:
                remaining = max(30, int(deadline - time.time()))
                time_limit = str(remaining)

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
                # 快速失败，避免在坏链路上长时间重试导致队列卡死
                "--max-tries", "1",
                "--retry-wait", "1",
                "--timeout", "30",
                "--auto-file-renaming=false",
                "--allow-overwrite=true",
                "--quiet",
                "--console-log-level=warn",
            ]
            cookie_file = self._write_cookie_file(cookie_str)
            if cookie_file:
                cmd.extend(["--load-cookies", cookie_file])

            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            logger.info(f"[Aria2] 开始下载: {output_name}")
            proc = None
            try:
                proc = subprocess.Popen(cmd, **kwargs)

                # 轮询进度
                last_emitted = -3
                start_time = time.time()
                while proc.poll() is None:
                    # 总超时 5 分钟或父级 deadline，避免子进程挂死导致 worker 永久占用
                    if time.time() - start_time > int(time_limit):
                        logger.error(f"[Aria2] 下载超时 {output_name}，强制终止子进程")
                        try:
                            proc.kill()
                            proc.wait(timeout=5)
                        except Exception:
                            pass
                        return False
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

                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    logger.error(f"[Aria2] 等待子进程退出超时 {output_name}，强制终止")
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                    return False

                stderr_output = ""
                try:
                    stderr_output = (proc.stderr or "")[-600:]
                except Exception:
                    pass

                if proc.returncode == 0:
                    if progress_callback:
                        progress_callback(total_size, total_size)
                    elif bvid:
                        emit_download_progress(bvid, 100)
                    logger.info(f"[Aria2] 下载完成: {output_name}")
                    return True
                else:
                    logger.error(
                        f"[Aria2] 下载失败 {output_name} (exit code {proc.returncode}): {stderr_output}"
                    )
                    return False
            finally:
                if cookie_file and os.path.exists(cookie_file):
                    try:
                        os.remove(cookie_file)
                    except OSError:
                        pass
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
        deadline: float = 0,
    ) -> bool:
        """使用 requests 下载单个文件（回退方案），针对 SSL EOF / 连接中断做专门重试。"""
        # 复用 WebClient 的 Session，复用 TCP 连接并统一 UA/Cookie
        session = self.web.session
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

        max_retries = 2
        for attempt in range(max_retries):
            if deadline and time.time() > deadline:
                logger.error(
                    f"[Download] requests 超过单文件总耗时上限，放弃: {os.path.basename(output_path)}"
                )
                return False

            try:
                with session.get(url, headers=headers, stream=True, timeout=(15, 90)) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0
                    last_log_percent = -3
                    last_active = time.time()
                    with open(output_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=16384):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                last_active = time.time()
                                if progress_callback:
                                    progress_callback(downloaded, total)
                                elif total > 0 and bvid:
                                    percent = int(downloaded / total * 100)
                                    if percent - last_log_percent >= 5:
                                        logger.info(f"[Download] {os.path.basename(output_path)}: {percent}%")
                                        emit_download_progress(bvid, percent)
                                        last_log_percent = percent
                            # 流式读取过程中也检查总耗时，避免无限挂起
                            if deadline and time.time() > deadline:
                                logger.error(
                                    f"[Download] requests 下载中超时，放弃: {os.path.basename(output_path)}"
                                )
                                return False
                            # 若 90 秒没有收到任何数据，认为连接已僵死
                            if time.time() - last_active > 90:
                                logger.error(
                                    f"[Download] requests 长时间无数据，放弃: {os.path.basename(output_path)}"
                                )
                                return False
                return True
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                logger.warning(
                    f"[Download] SSL/连接异常 {os.path.basename(output_path)} "
                    f"(尝试 {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    sleep_time = min(5 * (2 ** attempt) + random.uniform(1, 3), 30)
                    if deadline:
                        sleep_time = min(sleep_time, max(0, deadline - time.time()))
                    if sleep_time <= 0:
                        logger.error(f"[Download] SSL/连接异常，超过总耗时上限: {url}")
                        return False
                    logger.info(f"[Download] {sleep_time:.1f}秒后重试...")
                    time.sleep(sleep_time)
                else:
                    logger.error(f"[Download] SSL/连接异常，重试耗尽: {url}")
            except requests.exceptions.RequestException as e:
                logger.error(f"[Download] 下载失败 {url}: {e}")
                return False
            except Exception as e:
                logger.error(f"[Download] 下载异常 {url}: {e}")
                return False
        return False

    def _merge_with_ffmpeg(self, video_path: str, audio_path: str, output_path: str) -> bool:
        """使用 FFmpeg 合并音视频（DASH 格式），并捕获 stderr 以便排查原因。"""
        # 简单校验输入文件：不能为空，且应以 ftyp/moov 等 box 开头
        for label, path in [("video", video_path), ("audio", audio_path)]:
            try:
                size = os.path.getsize(path)
                if size == 0:
                    logger.error(f"[FFmpeg] {label} 文件为空: {path}")
                    return False
                with open(path, "rb") as f:
                    header = f.read(16)
                # m4s/mp4 文件通常以 00 00 00 xx 66 74 79 70 (ftyp) 开头
                if len(header) < 8 or header[4:8] != b"ftyp":
                    logger.warning(f"[FFmpeg] {label} 文件头可能异常: {header[:8].hex()}")
            except Exception as e:
                logger.error(f"[FFmpeg] 校验 {label} 文件失败: {e}")
                return False

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
                "check": False,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(cmd, timeout=600, **kwargs)
            if result.returncode != 0:
                stderr = (result.stderr or "")[-1000:]
                logger.error(f"[FFmpeg] 合并失败 (exit code {result.returncode}): {stderr}")
                return False
            os.remove(video_path)
            os.remove(audio_path)
            return True
        except subprocess.TimeoutExpired as e:
            logger.error(f"[FFmpeg] 合并超时（超过 10 分钟）: {e}")
            return False
        except Exception as e:
            logger.error(f"[FFmpeg] 合并异常: {e}")
            return False

    # 这些失败原因触发自动收藏到默认收藏夹，方便后续手动处理
    _FAV_ON_FAILURE_REASONS = (
        "音视频流下载失败（网络异常或被拦截）",
        "视频下载失败（网络异常或被拦截）",
        "FFmpeg 音视频合成失败",
    )

    def _record_failure(
        self,
        bvid: str,
        title: str,
        uploader: str,
        reason: str,
        file_size: int = 0,
    ):
        fav_added = False
        if any(r in reason for r in self._FAV_ON_FAILURE_REASONS):
            try:
                # 通过 bvid 查找 aid；若无 web_client 则跳过
                aid = self._last_video_aid or 0
                if aid and self.web:
                    fav_added = self.web.add_to_favorite(aid)
                    if fav_added:
                        logger.info(f"[Downloader] {bvid} 已自动加入默认收藏夹")
            except Exception as e:
                logger.warning(f"[Downloader] 自动收藏 {bvid} 失败: {e}")
        if self.db:
            self.db.add_failure(
                bvid, title, uploader, reason, file_size=file_size, fav_added=fav_added
            )

    def _check_video_attrs(self, info: dict) -> tuple:
        """返回 (is_upower_exclusive, is_ugc_pay, is_pay, is_arc_pay)"""
        is_upower_exclusive = info.get("is_upower_exclusive", False)
        rights = info.get("rights", {})
        return (
            bool(is_upower_exclusive),
            bool(rights.get("ugc_pay")),
            bool(rights.get("pay")),
            bool(rights.get("arc_pay")),
        )

    def _playurl_error_reason(self, bvid: str, is_upower: bool, is_arc_pay: bool,
                              is_ugc_pay: bool, is_pay: bool) -> str:
        """根据 playurl 错误信息返回可读原因。"""
        error_info = self.video_stream.last_playurl_error
        if error_info:
            code = error_info.get("code")
            message = error_info.get("message", "")
            if code in (10001003, 10001004):
                return "充电专属视频，当前账号未开通包月充电"
            if code == -404:
                return "视频不存在或已删除"
            if code in (-412, 412):
                return "被风控拦截（412）"
            if code == -403:
                return "权限不足（可能需大会员或充电）"
            if is_upower or is_arc_pay:
                return "充电专属视频，当前账号未开通包月充电"
            if is_ugc_pay or is_pay:
                return "付费视频，当前账号未购买"
            return f"无法获取播放地址（code={code}, message={message}）"
        return "无法获取播放地址（网络异常或无响应）"

    def _download_one_page(
        self,
        bvid: str,
        title: str,
        uname: str,
        info: dict,
        page: dict,
        page_index: int,
        total_pages: int,
    ) -> dict:
        """下载单个分页（P）。返回 {"success": bool, "output_path": str, "reason": str, "quality": str, "is_preview": bool}。"""
        self._last_video_aid = info.get("aid", 0)
        cid = page.get("cid")
        part_title = page.get("part", "")

        # 当前 P 预估/实际文件大小，用于写入失败记录
        current_file_size = 0

        def record_failure(reason: str, size: int = 0):
            self._record_failure(
                bvid, title, uname, reason, file_size=size or current_file_size
            )

        if not cid:
            return {"success": False, "reason": "无法获取视频 cid", "quality": ""}

        is_upower, is_ugc_pay, is_pay, is_arc_pay = self._check_video_attrs(info)
        target_qn = self.QUALITY_MAP.get(self.quality, 125)
        playurl = self.video_stream.get_playurl(bvid, cid, qn=target_qn)
        if not playurl:
            reason = self._playurl_error_reason(bvid, is_upower, is_arc_pay, is_ugc_pay, is_pay)
            record_failure(reason)
            return {"success": False, "reason": reason, "quality": ""}

        dash = playurl.get("dash")
        is_preview = False

        if not dash:
            # 充电视频/付费视频可能提供 FLV/durl 试看流
            can_preview = is_upower or is_arc_pay or is_ugc_pay or is_pay
            if can_preview:
                preview_playurl = self.video_stream.get_playurl_preview(bvid, cid, qn=target_qn)
                if preview_playurl and preview_playurl.get("durl"):
                    playurl = preview_playurl
                    is_preview = True
                else:
                    if is_upower or is_arc_pay:
                        reason = "充电专属视频，当前账号未开通包月充电（无试看）"
                    elif is_ugc_pay or is_pay:
                        reason = "付费视频，当前账号未购买（无试看）"
                    else:
                        reason = "视频不支持 DASH 格式"
                    record_failure(reason)
                    return {"success": False, "reason": reason, "quality": ""}
            else:
                reason = "视频不支持 DASH 格式"
                record_failure(reason)
                return {"success": False, "reason": reason, "quality": ""}

        if is_preview:
            # 试看流：durl 单文件，无独立音频
            durl_list = playurl.get("durl", [])
            if not durl_list:
                reason = "无可用试看流"
                record_failure(reason)
                return {"success": False, "reason": reason, "quality": ""}

            durl_item = durl_list[0]
            video_url = durl_item.get("url")
            if not video_url:
                backup_urls = durl_item.get("backup_url", [])
                video_url = backup_urls[0] if backup_urls else None

            if not video_url:
                reason = "无可用试看下载链接"
                record_failure(reason)
                return {"success": False, "reason": reason, "quality": ""}

            audio_url = None
            quality_id = playurl.get("quality", 0) or 0
            quality_label = self._get_quality_label({"id": quality_id}) if quality_id else ""
            quality_str = f"{quality_label}（试看）" if quality_label else "试看"
        else:
            video_streams = dash.get("video", [])
            audio_streams = dash.get("audio", [])
            if not video_streams:
                reason = "无可用视频流"
                record_failure(reason)
                return {"success": False, "reason": reason, "quality": ""}

            video_stream = self.video_stream.select_best_stream(video_streams, target_qn)
            audio_stream = self.video_stream.select_best_stream(audio_streams, 9999) if audio_streams else None
            if not video_stream:
                reason = "无法选择合适的视频流（可能画质不可用）"
                record_failure(reason)
                return {"success": False, "reason": reason, "quality": ""}

            video_url = video_stream.get("base_url")
            if not video_url:
                backup_urls = video_stream.get("backup_url", [])
                video_url = backup_urls[0] if backup_urls else None

            audio_url = None
            if audio_stream:
                audio_url = audio_stream.get("base_url")
                if not audio_url:
                    backup_urls = audio_stream.get("backup_url", [])
                    audio_url = backup_urls[0] if backup_urls else None

            if not video_url:
                reason = "无可用下载链接"
                record_failure(reason)
                return {"success": False, "reason": reason, "quality": ""}

            quality_str = self._get_quality_label(video_stream)

        # 构建当前 P 的文件名：注入该 P 的分集标题
        page_info = dict(info)
        page_info["cid"] = cid
        page_info["pages"] = [{"part": part_title}]
        output_name = self._build_filename(
            page_info,
            quality_str,
            index=page_index,
            audio_stream=audio_stream if not is_preview else None,
            video_stream=video_stream if not is_preview else None,
        )
        if not output_name.lower().endswith(".mp4"):
            output_name += ".mp4"
        # 多P 稿件强制在文件名中区分分P，避免默认模板缺少 %(index)s 时互相覆盖
        if total_pages > 1:
            base, ext = os.path.splitext(output_name)
            output_name = f"{base}_P{page_index:02d}{ext}"
        # 试看流在扩展名前加 -试看
        if is_preview:
            base, ext = os.path.splitext(output_name)
            output_name = f"{base}-试看{ext}"
        output_path = os.path.join(self.output_dir, output_name)

        # 校验路径安全
        abs_output_dir = os.path.abspath(self.output_dir)
        abs_output_path = os.path.abspath(output_path)
        if not abs_output_path.startswith(abs_output_dir + os.sep):
            reason = "输出文件名非法或越界"
            record_failure(reason)
            return {"success": False, "reason": reason, "quality": quality_str}

        referer = f"https://www.bilibili.com/video/{bvid}"
        min_percent = int((page_index - 1) / total_pages * 100)
        max_percent = int(page_index / total_pages * 100)
        progress = _DownloadProgress(bvid, min_percent, max_percent)

        if audio_url:
            video_tmp = output_path + ".video.m4s"
            audio_tmp = output_path + ".audio.m4s"

            video_total = self._get_stream_size(video_url, referer)
            audio_total = self._get_stream_size(audio_url, referer)
            current_file_size = video_total + audio_total

            # 1GB 大小限制检查
            if current_file_size > MAX_DOWNLOAD_SIZE:
                reason = "文件大小超过1GB，跳过下载"
                logger.warning(
                    f"[Download] {bvid} P{page_index} 大小 {video_total + audio_total} "
                    f"超过 {MAX_DOWNLOAD_SIZE}，跳过"
                )
                record_failure(reason)
                return {"success": False, "reason": reason, "quality": quality_str}

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
                record_failure(reason)
                return {"success": False, "reason": reason, "quality": quality_str}

            progress.finish()
            if not self._merge_with_ffmpeg(video_tmp, audio_tmp, output_path):
                reason = "FFmpeg 音视频合成失败"
                record_failure(reason)
                return {"success": False, "reason": reason, "quality": quality_str}
        else:
            total = self._get_stream_size(video_url, referer)
            current_file_size = total
            if total > MAX_DOWNLOAD_SIZE:
                reason = "文件大小超过1GB，跳过下载"
                logger.warning(
                    f"[Download] {bvid} P{page_index} 大小 {total} 超过 {MAX_DOWNLOAD_SIZE}，跳过"
                )
                record_failure(reason)
                return {"success": False, "reason": reason, "quality": quality_str}

            progress.register_stream("single", total)
            if not self._download_file(
                video_url, output_path, referer, bvid,
                progress_callback=lambda c, t: progress.update_stream("single", c),
            ):
                reason = "视频下载失败（网络异常或被拦截）"
                record_failure(reason)
                return {"success": False, "reason": reason, "quality": quality_str}
            progress.finish()

        # 记录文件元数据
        if self.db:
            self.db.add_file_metadata(output_path, bvid, title, uname)

        return {"success": True, "output_path": output_path, "quality": quality_str, "reason": "", "is_preview": is_preview}

    def download(self, bvid: str, title: str, uname: str) -> dict:
        """
        主下载入口。仿照 DownKyi 下载链路：
        1. 获取视频详情（含所有分页）
        2. 逐 P 获取 playurl 并下载
        3. FFmpeg 合并（DASH 格式）
        返回 {"success": bool, "quality": str, "output_path": str, "output_paths": list, "reason": str, "is_preview": bool}
        上传逻辑已解耦到 UploadManager，由调用方在下载成功后自行入队。
        """
        emit_download_started(bvid, title, uname)

        # 1. 获取视频详情
        info = self.video_stream.get_video_info(bvid)
        self._last_video_aid = info.get("aid", 0) if info else 0
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
            self._record_failure(bvid, title, uname, reason, file_size=0)
            emit_download_finished(bvid, False, reason)
            return {"success": False, "quality": "", "reason": reason}

        # 检查视频属性：充电专属、付费等
        is_upower, is_ugc_pay, is_pay, is_arc_pay = self._check_video_attrs(info)

        # 构建分页列表，兼容无 pages 字段的老接口
        pages = info.get("pages") or []
        if not pages and info.get("cid"):
            pages = [{"cid": info.get("cid"), "part": info.get("title", "")}]
        if not pages:
            reason = "无法获取视频分页信息"
            self._record_failure(bvid, title, uname, reason, file_size=0)
            emit_download_finished(bvid, False, reason)
            return {"success": False, "quality": "", "reason": reason}

        total_pages = len(pages)
        quality_str = ""
        first_reason = ""
        output_paths = []
        is_preview_any = False

        # 2. 逐 P 下载
        for page_index, page in enumerate(pages, start=1):
            if total_pages > 1:
                logger.info(f"[Download] {bvid} 开始下载第 {page_index}/{total_pages} P")

            result = self._download_one_page(
                bvid, title, uname, info, page, page_index, total_pages
            )

            if result.get("success"):
                output_paths.append(result["output_path"])
                if not quality_str:
                    quality_str = result.get("quality", "")
                if result.get("is_preview"):
                    is_preview_any = True
            else:
                page_reason = result.get("reason", "")
                if not first_reason:
                    first_reason = page_reason
                # 充电专属/付费视频只记录一次，后续 P 不再重复下载
                if "充电专属" in page_reason or "付费" in page_reason:
                    logger.info(f"[Download] {bvid} P{page_index} {page_reason}，停止该稿件后续分页")
                    break

        if output_paths:
            emit_download_finished(bvid, True, "下载完成")
            return {
                "success": True,
                "quality": quality_str,
                "output_path": output_paths[0],
                "output_paths": output_paths,
                "is_preview": is_preview_any,
            }

        reason = first_reason or "所有分页下载均失败"
        emit_download_finished(bvid, False, reason)
        return {"success": False, "quality": quality_str, "reason": reason}
