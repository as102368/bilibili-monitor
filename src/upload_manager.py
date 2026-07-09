import asyncio
import os
import time
from typing import Any, Dict, List, Optional

from .ctfile_uploader import CtfileUploader
from .database import DownloadDB
from .logger import get_logger
from .progress import (
    emit_upload_started,
    emit_upload_finished,
    emit_upload_progress,
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

    @classmethod
    def reset_instance(cls, download_dir: str):
        """配置变更时允许显式释放旧实例，避免 uploader/db 状态长期耦合。"""
        instance = cls._instances.pop(download_dir, None)
        if instance is not None:
            instance._initialized = False

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
        # 当前正在上传的文件名集合，防止同一 worker 周期内重复入队
        self._processing: set = set()

    def set_uploader(self, ctfile_uploader: Optional[CtfileUploader]):
        self.uploader = ctfile_uploader

    def pending_upload_count(self) -> int:
        """返回当前待上传队列长度。"""
        try:
            return self.db.count_pending_uploads()
        except Exception as e:
            logger.warning(f"[UploadManager] 查询待上传数量失败: {e}")
            return 0

    def _cleanup_failed_m4s(self, now: float):
        """清理合成失败的孤立 m4s 临时文件（没有对应 mp4 且已存在一段时间）。"""
        if not os.path.isdir(self.download_dir):
            return
        deleted = 0
        for name in os.listdir(self.download_dir):
            if not name.lower().endswith(".m4s"):
                continue
            file_path = os.path.join(self.download_dir, name)
            if not os.path.isfile(file_path):
                continue
            # 只删除已存在较长时间的孤立 m4s，避免误删正在下载的文件
            try:
                mtime = os.path.getmtime(file_path)
                if now - mtime < 60:
                    continue
            except OSError:
                continue
            # 判断是否有对应的 mp4：例如 video.mp4.video.m4s / video.mp4.audio.m4s -> video.mp4
            base = name
            for suffix in (".video.m4s", ".audio.m4s"):
                if base.lower().endswith(suffix):
                    base = base[: -len(suffix)]
                    break
            mp4_path = os.path.join(self.download_dir, base)
            if os.path.isfile(mp4_path):
                continue
            try:
                os.remove(file_path)
                deleted += 1
                logger.info(f"[UploadManager] 清理合成失败的 m4s: {name}")
            except OSError:
                pass
        if deleted:
            logger.info(f"[UploadManager] 共清理 {deleted} 个孤立 m4s 文件")

    def _is_already_uploaded(self, file_name: str) -> bool:
        """检查数据库中是否已有该文件名的成功上传记录。"""
        try:
            return self.db.is_upload_success(file_name)
        except Exception as e:
            logger.warning(f"[UploadManager] 查询上传记录失败: {e}")
            return False

    def _scan_files(self) -> List[Dict[str, Any]]:
        """扫描下载目录和待上传队列，返回待上传记录列表（按入队时间排序）。"""
        if not os.path.isdir(self.download_dir):
            return []

        now = time.time()
        # 先清理合成失败的孤立 m4s
        self._cleanup_failed_m4s(now)

        pending = self.db.get_pending_uploads(limit=1000)
        pending_paths = {r["file_path"] for r in pending}
        pending_names = {r["file_name"] for r in pending}

        for name in os.listdir(self.download_dir):
            file_path = os.path.join(self.download_dir, name)
            if not os.path.isfile(file_path):
                continue
            # 只上传已完成合并的 mp4 文件；过滤临时/部分文件
            if not name.lower().endswith(".mp4"):
                continue
            # 已在待上传队列中则跳过
            if file_path in pending_paths or name in pending_names:
                continue
            # 跳过仍在写入的文件（最近修改时间太近）
            try:
                mtime = os.path.getmtime(file_path)
                if now - mtime < self.MIN_FILE_AGE:
                    continue
            except OSError:
                continue
            # 已上传成功的文件跳过（防止程序重启后重复上传）
            if self._is_already_uploaded(name):
                logger.info(f"[UploadManager] {name} 已上传过，跳过")
                continue
            try:
                file_size = os.path.getsize(file_path)
                meta = self.db.get_file_metadata_by_name(name)
                self.db.add_pending_upload(
                    file_path=file_path,
                    bvid=meta.get("bvid", ""),
                    title=meta.get("title", ""),
                    uploader=meta.get("uploader", ""),
                    file_size=file_size,
                )
                pending_paths.add(file_path)
                pending_names.add(name)
                logger.info(f"[UploadManager] {name} 已加入待上传队列")
            except Exception as e:
                logger.warning(f"[UploadManager] 加入待上传队列失败 {name}: {e}")

        return self.db.get_pending_uploads(limit=1000)

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

                records = self._scan_files()
                # 先把所有候选文件展示到上传队列，让用户能看到排队状态
                for record in records:
                    emit_upload_started(record["file_name"])

                if len(records) >= self.BATCH_SIZE:
                    await self._upload_batch(records[: self.BATCH_SIZE])
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
                self._processing.clear()
                await asyncio.sleep(self.CHECK_INTERVAL)

    async def _upload_batch(self, records: List[Dict[str, Any]]):
        if not self.uploader:
            logger.warning("[UploadManager] 未配置城通网盘上传器，跳过本批次")
            return

        logger.info(f"[UploadManager] 开始上传批次，共 {len(records)} 个文件")
        success_paths: List[str] = []
        for record in records:
            record_id = record["id"]
            file_path = record["file_path"]
            file_name = record["file_name"]

            # 防止同一个文件在本次批次或 worker 周期内被重复处理
            if file_name in self._processing:
                logger.debug(f"[UploadManager] {file_name} 正在处理中，跳过")
                continue
            self._processing.add(file_name)

            if not os.path.isfile(file_path):
                logger.warning(f"[UploadManager] 本地文件不存在，标记失败: {file_name}")
                self.db.update_upload_status(record_id, "failed", "本地文件不存在")
                emit_upload_finished(file_name, False, "本地文件不存在")
                self._processing.discard(file_name)
                continue

            emit_upload_started(file_name)
            try:
                def _progress(pct: int):
                    emit_upload_progress(file_name, pct)

                # 单个文件上传最多等待 5 分钟，避免接口挂死导致整个 worker 停止
                ok = await asyncio.wait_for(
                    asyncio.to_thread(self.uploader.upload_file, file_path, _progress),
                    timeout=300,
                )
                if ok:
                    # 上传成功后立即删除本地文件，删除成功才视为真正成功
                    deleted = False
                    try:
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                            deleted = True
                            logger.info(f"[UploadManager] 已删除本地文件: {file_path}")
                    except OSError as e:
                        logger.error(f"[UploadManager] 删除本地文件失败 {file_path}: {e}")

                    if deleted:
                        success_paths.append(file_path)
                        self.db.update_upload_status(record_id, "success", "上传成功")
                        emit_upload_finished(file_name, True, "上传成功")
                        logger.info(f"[UploadManager] 上传成功: {file_name}")
                    else:
                        # 上传成功但删除失败：记录为失败，下次重试，避免网盘重复
                        self.db.update_upload_status(
                            record_id, "failed", "上传成功但删除本地文件失败，下次重试"
                        )
                        emit_upload_finished(file_name, False, "上传成功但删除本地文件失败，下次重试")
                        logger.warning(f"[UploadManager] 上传成功但删除失败，下次重试: {file_name}")
                else:
                    self.db.update_upload_status(
                        record_id, "failed", "上传失败，保留本地文件稍后重试"
                    )
                    emit_upload_finished(file_name, False, "上传失败，保留本地文件稍后重试")
                    logger.warning(f"[UploadManager] 上传失败，保留本地文件稍后重试: {file_name}")
            except asyncio.TimeoutError:
                logger.error(f"[UploadManager] 上传超时: {file_name}")
                self.db.update_upload_status(record_id, "failed", "上传超时（超过 5 分钟）")
                emit_upload_finished(file_name, False, "上传超时（超过 5 分钟）")
            except Exception as e:
                logger.exception(f"[UploadManager] 上传异常: {file_name}")
                self.db.update_upload_status(record_id, "failed", f"上传异常: {e}")
                emit_upload_finished(file_name, False, f"上传异常: {e}")
            finally:
                self._processing.discard(file_name)

        logger.info(
            f"[UploadManager] 批次完成: 成功 {len(success_paths)}/{len(records)}, "
            f"等待 {self.BATCH_INTERVAL} 秒后处理下一批"
        )
        await asyncio.sleep(self.BATCH_INTERVAL)
