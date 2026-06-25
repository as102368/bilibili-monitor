import asyncio
import os
import time
from typing import Dict, List, Optional

from .ctfile_uploader import CtfileUploader
from .database import DownloadDB
from .logger import get_logger
from .progress import (
    emit_upload_started,
    emit_upload_finished,
)

logger = get_logger(__name__)


class UploadManager:
    """
    下载/上传解耦后的批量上传管理器。

    - 通过扫描下载目录发现待上传文件，不再依赖下载流程的逐条登记。
    - 每累积满 10 个文件后作为一个批次上传。
    - 批次上传完成后统一删除本地源文件。
    - 批次之间固定等待 30 秒，避免触发城通网盘限流。
    - 下载流程与上传完全解耦，只要目录里有新文件就自动排队上传。
    """

    _instances: Dict[str, "UploadManager"] = {}
    BATCH_SIZE = 10
    BATCH_INTERVAL = 30
    CHECK_INTERVAL = 5
    MIN_FILE_AGE = 5  # 文件至少完成 5 秒后才上传，避免捕获正在写入的文件

    def __new__(cls, download_dir: str, db_path: str, ctfile_uploader: Optional[CtfileUploader] = None):
        # 同一下载目录共享同一个上传管理器，保证监控后台与 GUI 共用队列
        if download_dir not in cls._instances:
            cls._instances[download_dir] = super().__new__(cls)
            cls._instances[download_dir]._initialized = False
        return cls._instances[download_dir]

    def __init__(self, download_dir: str, db_path: str, ctfile_uploader: Optional[CtfileUploader] = None):
        if self._initialized:
            self.set_uploader(ctfile_uploader)
            return
        self._initialized = True
        self.download_dir = download_dir
        self.db = DownloadDB(db_path)
        self.uploader = ctfile_uploader
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._stop_event = asyncio.Event()

    def set_uploader(self, ctfile_uploader: Optional[CtfileUploader]):
        self.uploader = ctfile_uploader

    def _scan_files(self) -> List[str]:
        """扫描下载目录，返回按修改时间排序的、可上传的 mp4 文件列表。"""
        if not os.path.isdir(self.download_dir):
            return []

        files = []
        now = time.time()
        for name in os.listdir(self.download_dir):
            file_path = os.path.join(self.download_dir, name)
            if not os.path.isfile(file_path):
                continue
            # 只上传已完成合并的 mp4 文件；过滤临时/部分文件
            if not name.lower().endswith(".mp4"):
                continue
            # 跳过仍在写入的文件（最近修改时间太近）
            try:
                mtime = os.path.getmtime(file_path)
                if now - mtime < self.MIN_FILE_AGE:
                    continue
            except OSError:
                continue
            files.append((file_path, mtime))

        # 先下载完成的先上传
        files.sort(key=lambda x: x[1])
        return [f[0] for f in files]

    async def start_worker(self):
        """启动后台上传 worker；若 worker 已异常退出则自动重启。"""
        if self._worker_task and not self._worker_task.done():
            return
        self._running = True
        self._stop_event.clear()
        self._worker_task = asyncio.get_event_loop().create_task(self._upload_worker())
        logger.info("[UploadManager] 上传 worker 已启动")

    async def stop_worker(self):
        """停止后台上传 worker。"""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("[UploadManager] 上传 worker 已停止")

    async def _upload_worker(self):
        last_heartbeat = time.time()
        while self._running:
            try:
                # 心跳：每分钟打一次日志，方便排查 worker 是否活着
                if time.time() - last_heartbeat >= 60:
                    logger.info("[UploadManager] worker 心跳正常")
                    last_heartbeat = time.time()

                files = self._scan_files()
                # 先把所有候选文件展示到上传队列，让用户能看到排队状态
                for file_path in files:
                    emit_upload_started(os.path.basename(file_path))

                if len(files) >= self.BATCH_SIZE:
                    await self._upload_batch(files[: self.BATCH_SIZE])
                else:
                    # 未满一批，短暂等待后继续扫描
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(), timeout=self.CHECK_INTERVAL
                        )
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[UploadManager] 上传 worker 异常，5 秒后恢复")
                await asyncio.sleep(self.CHECK_INTERVAL)

    async def _upload_batch(self, file_paths: List[str]):
        if not self.uploader:
            logger.warning("[UploadManager] 未配置城通网盘上传器，跳过本批次")
            return

        logger.info(f"[UploadManager] 开始上传批次，共 {len(file_paths)} 个文件")
        success_paths: List[str] = []
        for file_path in file_paths:
            if not os.path.isfile(file_path):
                continue
            file_size = os.path.getsize(file_path)
            file_name = os.path.basename(file_path)
            meta = self.db.get_file_metadata_by_name(file_name)
            bvid = meta.get("bvid", "")
            title = meta.get("title", "")
            uploader = meta.get("uploader", "")
            emit_upload_started(file_name)
            try:
                # 单个文件上传最多等待 5 分钟，避免接口挂死导致整个 worker 停止
                ok = await asyncio.wait_for(
                    asyncio.to_thread(self.uploader.upload_file, file_path),
                    timeout=300,
                )
                if ok:
                    success_paths.append(file_path)
                    self.db.add_upload_record(
                        bvid=bvid,
                        title=title,
                        uploader=uploader,
                        file_name=file_name,
                        file_size=file_size,
                        status="success",
                        message="上传成功",
                        file_path=file_path,
                    )
                    emit_upload_finished(file_name, True, "上传成功")
                    logger.info(f"[UploadManager] 上传成功: {file_name}")
                else:
                    self.db.add_upload_record(
                        bvid=bvid,
                        title=title,
                        uploader=uploader,
                        file_name=file_name,
                        file_size=file_size,
                        status="failed",
                        message="上传失败，保留本地文件稍后重试",
                        file_path=file_path,
                    )
                    emit_upload_finished(file_name, False, "上传失败，保留本地文件稍后重试")
                    logger.warning(f"[UploadManager] 上传失败，保留本地文件稍后重试: {file_name}")
            except asyncio.TimeoutError:
                logger.error(f"[UploadManager] 上传超时: {file_name}")
                self.db.add_upload_record(
                    bvid=bvid,
                    title=title,
                    uploader=uploader,
                    file_name=file_name,
                    file_size=file_size,
                    status="failed",
                    message="上传超时（超过 5 分钟）",
                    file_path=file_path,
                )
                emit_upload_finished(file_name, False, "上传超时（超过 5 分钟）")
            except Exception as e:
                logger.exception(f"[UploadManager] 上传异常: {file_name}")
                self.db.add_upload_record(
                    bvid=bvid,
                    title=title,
                    uploader=uploader,
                    file_name=file_name,
                    file_size=file_size,
                    status="failed",
                    message=f"上传异常: {e}",
                    file_path=file_path,
                )
                emit_upload_finished(file_name, False, f"上传异常: {e}")

        # 上传成功的文件统一删除本地源文件
        for file_path in success_paths:
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    logger.info(f"[UploadManager] 已删除本地文件: {file_path}")
            except OSError as e:
                logger.error(f"[UploadManager] 删除本地文件失败 {file_path}: {e}")

        logger.info(
            f"[UploadManager] 批次完成: 成功 {len(success_paths)}/{len(file_paths)}, "
            f"等待 {self.BATCH_INTERVAL} 秒后处理下一批"
        )
        await asyncio.sleep(self.BATCH_INTERVAL)
