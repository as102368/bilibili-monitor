import asyncio
from datetime import datetime
from typing import List, Dict, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .wbi import WBI
from .database import DownloadDB
from .downloader import Downloader
from .web_client import BilibiliWebClient
from .video_stream import VideoStream
from .ctfile_uploader import CtfileUploader
from .logger import get_logger

logger = get_logger(__name__)


class BilibiliMonitor:
    def __init__(self, config: dict):
        self.config = config
        self.sessdata = config["cookie"]["sessdata"]
        self.db = DownloadDB(config["database"]["path"])
        self.wbi = WBI(self.sessdata)

        # 仿照 DownKyi：使用统一 WebClient，自动获取 buvid3/buvid4 并复用连接
        self.web = BilibiliWebClient(
            sessdata=self.sessdata,
            bili_jct=config["cookie"]["bili_jct"],
            buvid3=config["cookie"].get("buvid3", ""),
            dedeuserid=config["cookie"].get("dedeuserid", ""),
        )

        # 仿照 DownKyi：VideoStream 获取直链，Downloader 使用 requests + FFmpeg
        self.video_stream = VideoStream(self.web, self.wbi)

        # 城通网盘上传器（可选）
        ctfile_cfg = config.get("ctfile", {})
        ctfile_uploader = None
        if ctfile_cfg.get("upload_after_download") and ctfile_cfg.get("session"):
            ctfile_uploader = CtfileUploader(
                session_token=ctfile_cfg["session"],
                folder_id=ctfile_cfg.get("folder_id", "0"),
            )

        self.downloader = Downloader(
            output_dir=config["download"]["output_dir"],
            quality=config["download"]["quality"],
            template=config["download"]["filename_template"],
            web_client=self.web,
            video_stream=self.video_stream,
            ctfile_uploader=ctfile_uploader,
            db=self.db,
        )

        self.scheduler = AsyncIOScheduler()
        self._download_sem = asyncio.Semaphore(1)
        self.my_mid: int = 0

    def _sync_auth_check(self):
        data = self.web.request(
            "https://api.bilibili.com/x/web-interface/nav",
            referer="https://www.bilibili.com",
            timeout=10,
        )
        if data.get("code") != 0:
            raise RuntimeError(f"登录验证失败: {data}")
        self.my_mid = data["data"]["mid"]
        uname = data["data"]["uname"]
        logger.info(f"[Auth] 登录成功: {uname}")

    async def init(self):
        await asyncio.to_thread(self._sync_auth_check)
        logger.info("[Monitor] 初始化完成，准备通过动态流监控视频投稿")

    async def _fetch_dynamic_videos(self) -> List[Dict]:
        def _fetch():
            data = self.web.request(
                "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all",
                referer="https://t.bilibili.com",
                params={"type": "all", "timezone_offset": -480},
                timeout=15,
            )
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
            logger.error(f"[Monitor] 获取动态失败: {e}")
            return

        page_size = self.config["monitor"].get("page_size", 20)
        new_count = 0
        for item in items[:page_size]:
            try:
                video = self._extract_video_from_dynamic(item)
                if not video or not video.get("bvid"):
                    continue

                bvid = video["bvid"]
                title = video["title"]
                uname = video["uname"]
                mid = video["mid"]

                if self.db.is_downloaded(bvid):
                    continue

                logger.info(f"[New] {uname} 发布新视频: {title} ({bvid})")
                async with self._download_sem:
                    result = await asyncio.to_thread(self.downloader.download, bvid, title, uname)
                if result.get("success"):
                    self.db.mark_downloaded(bvid, title, uname, mid, result.get("quality", ""))
                    logger.info(f"[Done] {bvid} 下载完成")
                    new_count += 1
                else:
                    logger.warning(f"[Fail] {bvid} 下载失败，将在下次重试")

                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"[Monitor] 处理视频时异常: {e}")

        if new_count == 0:
            logger.info("[Monitor] 暂无新视频")

    def start(self):
        interval = self.config["monitor"]["interval"]
        self.scheduler.add_job(
            self.check_all,
            "interval",
            seconds=interval,
            id="check_all",
            replace_existing=True,
            next_run_time=datetime.now(),
        )
        self.scheduler.start()
        logger.info(f"[Monitor] 调度器已启动，每 {interval} 秒扫描一次")

    def stop(self):
        self.scheduler.shutdown(wait=False)
        logger.info("[Monitor] 调度器已停止")
