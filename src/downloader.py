import os
import re
import subprocess
from typing import Optional

import requests

from .video_stream import VideoStream
from .web_client import BilibiliWebClient
from .ctfile_uploader import CtfileUploader


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


class Downloader:
    """
    仿照 DownKyi：使用 B站 API 获取直链，requests 下载，FFmpeg 合并。
    完全绕过 yt-dlp 以避免 412 风控拦截。
    """

    # 画质代码映射（qn -> 最高允许 id）
    QUALITY_MAP = {
        "4K": 125,
        "1080P60": 120,
        "1080P+": 116,
        "1080P": 112,
        "720P": 80,
        "480P": 32,
        "360P": 16,
        "best": 125,
    }

    def __init__(
        self,
        output_dir: str,
        quality: str,
        template: str,
        web_client: BilibiliWebClient,
        video_stream: VideoStream,
        ctfile_uploader: Optional[CtfileUploader] = None,
    ):
        self.output_dir = output_dir
        self.quality = quality
        self.template = template
        self.web = web_client
        self.video_stream = video_stream
        self.ctfile_uploader = ctfile_uploader
        self.ffmpeg_path = _get_ffmpeg_path()
        os.makedirs(output_dir, exist_ok=True)

    def _sanitize_filename(self, name: str) -> str:
        """去除文件名中的非法字符"""
        return re.sub(r'[\\/:*?"<>|]', "", name)

    def _build_filename(self, title: str, uploader: str, bvid: str) -> str:
        """根据模板构建文件名，支持 yt-dlp 风格的 %(placeholder)s 格式"""
        # 将 yt-dlp 风格 %(name)s 转换为 Python format 风格 {name}
        template = self.template
        mapping = {
            "%(uploader)s": "{uploader}",
            "%(title)s": "{title}",
            "%(id)s": "{bvid}",
            "%(ext)s": "mp4",
        }
        for old, new in mapping.items():
            template = template.replace(old, new)
        filename = template.format(
            title=self._sanitize_filename(title),
            uploader=self._sanitize_filename(uploader),
            bvid=bvid,
        )
        return filename

    def _download_file(self, url: str, output_path: str, referer: str) -> bool:
        """使用 requests 下载单个文件，仿照 DownKyi Aria2c 的直链下载"""
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
                with open(output_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                percent = downloaded / total * 100
                                print(f"\r[Download] {os.path.basename(output_path)}: {percent:.1f}%", end="")
            print()
            return True
        except Exception as e:
            print(f"\n[Download] 下载失败 {url}: {e}")
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
            print(f"[FFmpeg] 合并中... {os.path.basename(output_path)}")
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            os.remove(video_path)
            os.remove(audio_path)
            return True
        except subprocess.CalledProcessError as e:
            print(f"[FFmpeg] 合并失败: {e}")
            return False

    def download(self, bvid: str, title: str, uname: str) -> bool:
        """
        主下载入口。仿照 DownKyi 下载链路：
        1. 获取视频详情 (cid)
        2. 获取 playurl (DASH 直链)
        3. 下载视频流 + 音频流
        4. FFmpeg 合并
        """
        # 1. 获取视频详情
        info = self.video_stream.get_video_info(bvid)
        if not info:
            print(f"[Download] 无法获取视频详情: {bvid}")
            return False

        cid = info.get("cid")
        if not cid:
            print(f"[Download] 无法获取 cid: {bvid}")
            return False

        # 2. 获取 playurl
        target_qn = self.QUALITY_MAP.get(self.quality, 125)
        playurl = self.video_stream.get_playurl(bvid, cid, qn=target_qn)
        if not playurl:
            print(f"[Download] 无法获取 playurl: {bvid}")
            return False

        # 3. 解析 DASH 直链
        dash = playurl.get("dash")
        if not dash:
            print(f"[Download] 视频不支持 DASH 格式: {bvid}")
            return False

        video_streams = dash.get("video", [])
        audio_streams = dash.get("audio", [])

        if not video_streams:
            print(f"[Download] 无可用视频流: {bvid}")
            return False

        video_stream = self.video_stream.select_best_stream(video_streams, target_qn)
        audio_stream = self.video_stream.select_best_stream(audio_streams, 9999) if audio_streams else None

        if not video_stream:
            print(f"[Download] 无法选择合适的视频流: {bvid}")
            return False

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
            print(f"[Download] 无可用下载链接: {bvid}")
            return False

        # 4. 构建输出文件名
        output_name = self._build_filename(title, uname, bvid)
        # 如果没有扩展名，加上 .mp4
        if not output_name.lower().endswith(".mp4"):
            output_name += ".mp4"
        output_path = os.path.join(self.output_dir, output_name)

        # 5. 下载
        referer = f"https://www.bilibili.com/video/{bvid}"

        if audio_url:
            # DASH 格式：分别下载视频和音频，再合并
            video_tmp = output_path + ".video.m4s"
            audio_tmp = output_path + ".audio.m4s"

            ok1 = self._download_file(video_url, video_tmp, referer)
            ok2 = self._download_file(audio_url, audio_tmp, referer)
            if not (ok1 and ok2):
                return False

            success = self._merge_with_ffmpeg(video_tmp, audio_tmp, output_path)
        else:
            # 无音频分离，直接下载视频（MP4 直链）
            success = self._download_file(video_url, output_path, referer)

        # 上传至城通网盘并删除源文件
        if success and self.ctfile_uploader:
            self.ctfile_uploader.upload_and_delete(output_path)

        return success
