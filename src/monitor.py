import asyncio
from typing import List, Dict, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import requests

from .wbi import WBI
from .database import DownloadDB
from .downloader import Downloader
from .credential_manager import export_cookies_to_netscape


class BilibiliMonitor:
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://t.bilibili.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    def __init__(self, config: dict):
        self.config = config
        self.sessdata = config["cookie"]["sessdata"]
        self.db = DownloadDB(config["database"]["path"])
        self.wbi = WBI(self.sessdata)

        cookies_path = "data/cookies_netscape.txt"
        export_cookies_to_netscape(
            self.sessdata,
            config["cookie"]["bili_jct"],
            config["cookie"].get("buvid3", ""),
            config["cookie"].get("dedeuserid", ""),
            cookies_path,
        )
        self.downloader = Downloader(
            output_dir=config["download"]["output_dir"],
            quality=config["download"]["quality"],
            template=config["download"]["filename_template"],
            cookies_path=cookies_path,
        )

        self.scheduler = AsyncIOScheduler()
        self.my_mid: int = 0

    def _sync_auth_check(self):
        cookies = {"SESSDATA": self.sessdata}
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=self.HEADERS,
            cookies=cookies,
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"登录验证失败: {data}")
        self.my_mid = data["data"]["mid"]
        uname = data["data"]["uname"]
        print(f"[Auth] 登录成功: {uname}")

    async def init(self):
        self._sync_auth_check()
        print("[Monitor] 初始化完成，准备通过动态流监控视频投稿")

    async def _fetch_dynamic_videos(self) -> List[Dict]:
        def _fetch():
            cookies = {"SESSDATA": self.sessdata}
            resp = requests.get(
                "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all",
                params={"type": "all", "timezone_offset": -480},
                headers=self.HEADERS,
                cookies=cookies,
                timeout=15,
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"获取动态失败: {data}")
            return data["data"].get("items", [])

        return await asyncio.to_thread(_fetch)

    @staticmethod
    def _extract_video_from_dynamic(item: Dict) -> Optional[Dict]:
        dtype = item.get("type", "")
        if dtype == "DYNAMIC_TYPE_AV":
            archive = (
                item.get("modules", {})
                .get("module_dynamic", {})
                .get("major", {})
                .get("archive", {})
            )
            if not archive:
                return None
            author = item.get("modules", {}).get("module_author", {})
            return {
                "bvid": archive.get("bvid"),
                "title": archive.get("title", ""),
                "uname": author.get("name", ""),
                "mid": author.get("mid", 0),
            }
        if dtype == "DYNAMIC_TYPE_FORWARD":
            orig = item.get("orig", {})
            return BilibiliMonitor._extract_video_from_dynamic(orig)
        return None

    async def check_all(self):
        try:
            items = await self._fetch_dynamic_videos()
        except Exception as e:
            print(f"[Monitor] 获取动态失败: {e}")
            return

        page_size = self.config["monitor"].get("page_size", 20)
        new_count = 0
        for item in items[:page_size]:
            video = self._extract_video_from_dynamic(item)
            if not video or not video.get("bvid"):
                continue

            bvid = video["bvid"]
            title = video["title"]
            uname = video["uname"]
            mid = video["mid"]

            if self.db.is_downloaded(bvid):
                continue

            print(f"[New] {uname} 发布新视频: {title} (BV{bvid})")
            url = f"https://www.bilibili.com/video/{bvid}"
            success = self.downloader.download(url)
            if success:
                self.db.mark_downloaded(bvid, title, uname, mid)
                print(f"[Done] BV{bvid} 下载完成")
                new_count += 1
            else:
                print(f"[Fail] BV{bvid} 下载失败，将在下次重试")

            await asyncio.sleep(2)

        if new_count == 0:
            print("[Monitor] 暂无新视频")

    def start(self):
        interval = self.config["monitor"]["interval"]
        self.scheduler.add_job(
            self.check_all,
            "interval",
            seconds=interval,
            id="check_all",
            replace_existing=True,
        )
        self.scheduler.start()
        print(f"[Monitor] 调度器已启动，每 {interval} 秒扫描一次")

    def stop(self):
        self.scheduler.shutdown(wait=True)
        print("[Monitor] 调度器已停止")
