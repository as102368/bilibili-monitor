import os
import yt_dlp


class Downloader:
    def __init__(self, output_dir: str, quality: str, template: str, cookies_path: str):
        self.output_dir = output_dir
        self.quality = quality
        self.template = template
        self.cookies_path = cookies_path
        os.makedirs(output_dir, exist_ok=True)

    def download(self, url: str) -> bool:
        quality = self.quality
        if quality in ("best", "", None):
            quality = "bv*+ba/b"
        opts = {
            "outtmpl": os.path.join(self.output_dir, self.template),
            "format": quality,
            "cookiefile": self.cookies_path,
            "quiet": False,
            "noprogress": False,
            "merge_output_format": "mp4",
            "postprocessors": [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
            ],
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            return True
        except Exception as e:
            print(f"[Download] 下载失败: {e}")
            return False
