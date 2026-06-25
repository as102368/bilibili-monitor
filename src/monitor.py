import asyncio
import random
from datetime import datetime
from typing import List, Dict, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .wbi import WBI
from .database import DownloadDB
from .downloader import Downloader
from .web_client import BilibiliWebClient
from .video_stream import VideoStream
from .ctfile_uploader import CtfileUploader
from .upload_manager import UploadManager
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
            time_format=config["download"].get("time_format", "yyyy-MM-dd HH-mm-ss"),
            index_format=config["download"].get("index_format", "自然数"),
        )

        # 上传管理器：与下载解耦，扫描下载目录满 10 个一批上传
        download_dir = config["download"].get("output_dir", "./downloads")
        self.upload_manager = UploadManager(download_dir, config["database"]["path"], ctfile_uploader)

        self.scheduler = AsyncIOScheduler(
            job_defaults={
                "misfire_grace_time": None,
                "coalesce": True,
                "max_instances": 1,
            }
        )
        concurrent = max(1, min(5, config["download"].get("concurrent_downloads", 2)))
        self._download_sem = asyncio.Semaphore(concurrent)
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
        await self.upload_manager.start_worker()
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

    async def _throttled_download(self, bvid: str, title: str, uname: str, stagger_delay: float):
        """错峰下载：先冷却再进入并发槽位，避免 API 请求同时爆发触发风控"""
        await asyncio.sleep(stagger_delay)
        async with self._download_sem:
            result = await asyncio.to_thread(self.downloader.download, bvid, title, uname)
        return result

    async def _download_and_track(self, bvid: str, title: str, uname: str, mid: int, stagger_delay: float):
        """下载单个视频并在成功后入队上传；异常或失败均记录到失败表。"""
        try:
            result = await self._throttled_download(bvid, title, uname, stagger_delay)
        except Exception as e:
            logger.error(f"[Monitor] 下载异常 {bvid}: {e}")
            return

        if not isinstance(result, dict):
            logger.error(f"[Monitor] 下载返回异常 {bvid}: {result}")
            return

        if result.get("success"):
            self.db.mark_downloaded(bvid, title, uname, mid, result.get("quality", ""))
            logger.info(f"[Done] {bvid} 下载完成，上传由 UploadManager 扫描目录自动处理")
        else:
            reason = result.get("reason", "")
            if "充电专属" in reason:
                logger.info(f"[Monitor] {bvid} 为充电专属视频，跳过")
            else:
                logger.warning(f"[Fail] {bvid} 下载失败，将在下次重试: {reason}")

    async def check_all(self):
        try:
            await self._do_check_all()
        except Exception:
            logger.exception("[Monitor] check_all 发生未捕获异常，调度器将继续运行")

    async def _do_check_all(self):
        try:
            items = await self._fetch_dynamic_videos()
        except Exception as e:
            logger.error(f"[Monitor] 获取动态失败: {e}")
            return

        page_size = self.config["monitor"].get("page_size", 20)
        new_videos = []
        for item in items:
            try:
                video = self._extract_video_from_dynamic(item)
                if not video or not video.get("bvid"):
                    continue
                if self.db.is_downloaded(video["bvid"]):
                    continue
                new_videos.append(video)
            except Exception as e:
                logger.error(f"[Monitor] 解析动态时异常: {e}")

        if not new_videos:
            logger.info("[Monitor] 暂无新视频")
            return

        # 限制本次同时下载的数量
        new_videos = new_videos[:page_size]

        # 扫描到新视频后立即后台下载，不等待下载/上传完成
        for idx, video in enumerate(new_videos):
            bvid = video["bvid"]
            title = video["title"]
            uname = video["uname"]
            mid = video["mid"]

            # 检查失败重试次数
            failure_info = self.db.get_pending_failure_info(bvid)
            fail_count = failure_info.get("fail_count", 0)
            if fail_count > 0:
                reason = failure_info.get("reason", "")
                if "充电专属" in reason:
                    logger.debug(f"[Monitor] {bvid} 为充电专属视频，跳过下载")
                    continue
                if fail_count >= 5:
                    logger.debug(
                        f"[Monitor] {bvid} 已达到最大重试次数 ({fail_count}/5)，跳过: {reason}"
                    )
                    self.db.mark_failure_skipped(bvid)
                    continue

            logger.info(f"[New] {uname} 发布新视频: {title} ({bvid})")
            delay = idx * random.uniform(4, 6)
            task = asyncio.create_task(self._download_and_track(bvid, title, uname, mid, delay))
            task.add_done_callback(self._on_download_task_done)

    def _on_download_task_done(self, task: asyncio.Task):
        try:
            task.result()
        except Exception:
            logger.exception("[Monitor] 后台下载任务异常")

    async def fetch_dynamics_in_range(self, start_ts: int, end_ts: int) -> List[Dict]:
        """分页获取指定时间段内的动态视频（时间倒序）"""
        videos: List[Dict] = []
        offset = ""
        max_pages = 500  # 安全上限，防止无限循环

        for _ in range(max_pages):
            def _fetch():
                params = {"type": "all", "timezone_offset": -480}
                if offset:
                    params["offset"] = offset
                data = self.web.request(
                    "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all",
                    referer="https://t.bilibili.com",
                    params=params,
                    timeout=15,
                )
                return data

            data = await asyncio.to_thread(_fetch)
            if data.get("code") != 0:
                raise RuntimeError(f"获取动态失败: {data}")

            items = data["data"].get("items", [])
            if not items:
                break

            has_more = data["data"].get("has_more", False)
            offset = data["data"].get("offset", "")

            stop_paging = False
            for item in items:
                pub_ts = (
                    item.get("modules", {})
                    .get("module_author", {})
                    .get("pub_ts", 0)
                )
                try:
                    pub_ts = int(pub_ts)
                    # B站动态API的pub_ts是毫秒时间戳，转换为秒
                    if pub_ts > 10000000000:
                        pub_ts = pub_ts // 1000
                except (ValueError, TypeError):
                    pub_ts = 0
                # 时间倒序：如果已经早于开始时间，后续也不用再看了
                if pub_ts and pub_ts < start_ts:
                    stop_paging = True
                    break
                if pub_ts and pub_ts > end_ts:
                    continue

                video = self._extract_video_from_dynamic(item)
                if video:
                    video["pub_ts"] = pub_ts
                    video["type"] = "动态"
                    videos.append(video)

            if stop_paging or not has_more or not offset:
                break

        return videos

    async def fetch_user_videos_in_range(self, mid: int, start_ts: int, end_ts: int) -> List[Dict]:
        """分页获取指定UP主在时间段内的投稿（按发布日期倒序）"""
        videos: List[Dict] = []
        pn = 1
        ps = 30
        max_pages = 500

        for _ in range(max_pages):
            params = {
                "mid": mid,
                "ps": ps,
                "pn": pn,
                "order": "pubdate",
            }
            signed = self.wbi.sign(params)
            data = await asyncio.to_thread(
                self.web.request,
                "https://api.bilibili.com/x/space/wbi/arc/search",
                referer=f"https://space.bilibili.com/{mid}",
                params=signed,
                timeout=15,
            )

            if data.get("code") != 0:
                raise RuntimeError(f"获取UP主投稿失败: {data}")

            vlist = data["data"].get("list", {}).get("vlist", [])
            if not vlist:
                break

            for v in vlist:
                created = v.get("created", 0)
                try:
                    created = int(created)
                except (ValueError, TypeError):
                    created = 0
                if created < start_ts:
                    return videos
                if created > end_ts:
                    continue
                videos.append({
                    "bvid": v["bvid"],
                    "title": v["title"],
                    "uname": v.get("author", ""),
                    "mid": v.get("mid", mid),
                    "pub_ts": created,
                    "type": "投稿",
                })

            page_info = data["data"].get("page", {})
            total = page_info.get("count", 0)
            if pn * ps >= total:
                break
            pn += 1

        return videos

    def start(self):
        try:
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
        except Exception:
            logger.exception("[Monitor] 启动调度器失败")
            raise

    def stop(self):
        try:
            self.scheduler.shutdown(wait=False)
            logger.info("[Monitor] 调度器已停止")
        except Exception:
            pass
