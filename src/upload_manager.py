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
        self._db_path = db_path
        self._db: Optional[DownloadDB] = None
        self.uploader = ctfile_uploader
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._stop_event = asyncio.Event()
        # 当前正在上传的文件名集合，防止同一 worker 周期内重复入队
        self._processing: set = set()
        # 未配置上传器时只警告一次，避免控制台刷屏
        self._uploader_missing_logged = False

    @property
    def db(self) -> DownloadDB:
        """延迟初始化数据库连接，避免在 GUI 主线程创建 UploadManager 时阻塞。"""
        if self._db is None:
            self._db = DownloadDB(self._db_path)
        return self._db

    def _get_db(self) -> DownloadDB:
        return self.db

    def set_uploader(self, ctfile_uploader: Optional[CtfileUploader]):
        self.uploader = ctfile_uploader

    def pending_upload_count(self) -> int:
        """返回当前待上传队列长度（先清理已不存在的记录，确保统计准确）。"""
        try:
            db = self._get_db()
            cleaned = self._cleanup_missing_pending_files(db)
            if cleaned:
                logger.info(f"[UploadManager] pending 统计前清理 {cleaned} 个缺失记录")
            return db.count_pending_uploads()
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

    def _cleanup_aria2_files(self, now: float):
        """清理 Aria2 残留的 .aria2 控制文件。

        Aria2 在下载时为输出文件生成 ``<输出文件名>.aria2`` 控制文件，正常完成/取消
        后会自动删除。若进程被强制终止或崩溃，控制文件会残留；这里删除对应输出文件
        已不存在或已经完成的控制文件，避免目录无限增长。
        """
        if not os.path.isdir(self.download_dir):
            return
        deleted = 0
        for name in os.listdir(self.download_dir):
            if not name.lower().endswith(".aria2"):
                continue
            file_path = os.path.join(self.download_dir, name)
            if not os.path.isfile(file_path):
                continue
            base_name = name[: -len(".aria2")]
            base_path = os.path.join(self.download_dir, base_name)
            # 若对应输出文件已不存在，控制文件已无意义
            if not os.path.isfile(base_path):
                remove = True
            else:
                # 输出文件已存在时，只删除已经稳定完成一段时间的控制文件，
                # 避免误删正在活跃下载中的控制文件
                try:
                    mtime = os.path.getmtime(base_path)
                    remove = now - mtime >= 60
                except OSError:
                    continue
            if remove:
                try:
                    os.remove(file_path)
                    deleted += 1
                    logger.info(f"[UploadManager] 清理 Aria2 残留控制文件: {name}")
                except OSError:
                    pass
        if deleted:
            logger.info(f"[UploadManager] 共清理 {deleted} 个 Aria2 控制文件")

    def _is_already_uploaded(self, file_name: str) -> bool:
        """检查数据库中是否已有该文件名的成功上传记录。"""
        try:
            return self._get_db().is_upload_success(file_name)
        except Exception as e:
            logger.warning(f"[UploadManager] 查询上传记录失败: {e}")
            return False

    def _mark_failed(
        self,
        db: DownloadDB,
        record_id: int,
        file_name: str,
        bvid: str,
        title: str,
        uploader: str,
        file_size: int,
        reason: str,
        failed_records: Optional[List[Dict[str, Any]]] = None,
        emit_signal: bool = True,
    ):
        """统一标记上传失败，写入失败记录并释放处理锁；内部异常不影响后续流程。"""
        try:
            db.update_upload_status(record_id, "failed", reason)
        except Exception as e:
            logger.warning(f"[UploadManager] 更新上传状态失败 {file_name}: {e}")
        try:
            db.add_failure(
                bvid=bvid,
                title=title or file_name,
                uploader=uploader,
                reason=reason,
                file_size=file_size,
                file_name=file_name,
            )
        except Exception as e:
            logger.warning(f"[UploadManager] 写入失败记录失败 {file_name}: {e}")
        if emit_signal:
            try:
                emit_upload_finished(file_name, False, reason)
            except Exception:
                pass
        self._processing.discard(file_name)
        if failed_records is not None:
            failed_records.append({"file_name": file_name, "reason": reason})

    def _cleanup_missing_pending_files(self, db: DownloadDB) -> int:
        """清理本地文件已不存在的 pending 记录，避免僵尸记录占满队列。"""
        try:
            pending = db.get_pending_uploads(limit=100000)
            missing_ids = [
                r["id"] for r in pending if not os.path.isfile(r.get("file_path", ""))
            ]
            if not missing_ids:
                return 0
            db.delete_upload_records(missing_ids)
            cleaned = len(missing_ids)
            logger.info(f"[UploadManager] 已清理 {cleaned} 个本地文件不存在的待上传记录")
            return cleaned
        except Exception as e:
            logger.warning(f"[UploadManager] 清理缺失文件记录失败: {e}")
            return 0

    def _scan_files(self) -> List[Dict[str, Any]]:
        """扫描下载目录和待上传队列，返回待上传记录列表（按入队时间排序）。"""
        if not os.path.isdir(self.download_dir):
            return []

        now = time.time()
        # 先清理合成失败的孤立 m4s
        self._cleanup_failed_m4s(now)
        # 再清理 Aria2 残留控制文件
        self._cleanup_aria2_files(now)

        db = self._get_db()

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
            # 已上传成功的文件跳过（防止程序重启后重复上传）
            if self._is_already_uploaded(name):
                continue
            try:
                file_size = os.path.getsize(file_path)
                meta = db.get_file_metadata_by_name(name)
                inserted = db.add_pending_upload(
                    file_path=file_path,
                    bvid=meta.get("bvid", ""),
                    title=meta.get("title", ""),
                    uploader=meta.get("uploader", ""),
                    file_size=file_size,
                )
                # 只在真正新增时打印日志，避免重复扫描刷屏
                if inserted is not None:
                    logger.info(f"[UploadManager] {name} 已加入待上传队列")
            except Exception as e:
                logger.warning(f"[UploadManager] 加入待上传队列失败 {name}: {e}")

        # 清理本地文件已不存在的 pending 记录，避免僵尸记录占满上传队列
        self._cleanup_missing_pending_files(db)

        return db.get_pending_uploads(limit=1000)

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
        deduplicated = False
        while self._running:
            try:
                # 首次运行时清理历史重复记录，避免数据无限膨胀
                if not deduplicated:
                    removed = await asyncio.to_thread(self.db.deduplicate_uploads)
                    if removed:
                        logger.info(f"[UploadManager] 已清理 {removed} 条重复上传记录")
                    deduplicated = True

                # 心跳：每分钟打一次日志，方便排查 worker 是否活着
                if time.time() - last_heartbeat >= 60:
                    logger.info("[UploadManager] worker 心跳正常")
                    last_heartbeat = time.time()

                # 扫描目录和数据库是同步 IO，放到独立线程避免阻塞 GUI 事件循环
                records = await asyncio.to_thread(self._scan_files)
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
            if not self._uploader_missing_logged:
                logger.warning("[UploadManager] 未配置城通网盘上传器，跳过上传")
                self._uploader_missing_logged = True
            # 避免无上传器时 CPU/日志空转
            await asyncio.sleep(self.BATCH_INTERVAL)
            return
        self._uploader_missing_logged = False

        logger.info(f"[UploadManager] 开始上传批次，共 {len(records)} 个文件")
        db = self._get_db()
        success_paths: List[str] = []
        failed_records: List[Dict[str, Any]] = []
        for record in records:
            record_id = record["id"]
            file_path = record["file_path"]
            file_name = record["file_name"]
            bvid = record.get("bvid", "")
            title = record.get("title", "")
            uploader = record.get("uploader", "")
            file_size = record.get("file_size", 0) or 0

            # 防止同一个文件在本次批次或 worker 周期内被重复处理
            if file_name in self._processing:
                logger.debug(f"[UploadManager] {file_name} 正在处理中，跳过")
                continue
            self._processing.add(file_name)

            if not os.path.isfile(file_path):
                # 扫描阶段已清理大部分缺失记录；此处静默处理，避免刷屏
                self._mark_failed(
                    db=db,
                    record_id=record_id,
                    file_name=file_name,
                    bvid=bvid,
                    title=title,
                    uploader=uploader,
                    file_size=file_size,
                    reason="本地文件不存在",
                    failed_records=failed_records,
                    emit_signal=False,
                )
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
                        db.update_upload_status(record_id, "success", "上传成功")
                        emit_upload_finished(file_name, True, "上传成功")
                        logger.info(f"[UploadManager] 上传成功: {file_name}")
                    else:
                        # 上传成功但删除失败：记录为失败，下次重试，避免网盘重复
                        self._mark_failed(
                            db=db,
                            record_id=record_id,
                            file_name=file_name,
                            bvid=bvid,
                            title=title,
                            uploader=uploader,
                            file_size=file_size,
                            reason="上传成功但删除本地文件失败，下次重试",
                            failed_records=failed_records,
                        )
                        logger.warning(f"[UploadManager] 上传成功但删除失败，下次重试: {file_name}")
                else:
                    self._mark_failed(
                        db=db,
                        record_id=record_id,
                        file_name=file_name,
                        bvid=bvid,
                        title=title,
                        uploader=uploader,
                        file_size=file_size,
                        reason="上传失败，保留本地文件稍后重试",
                        failed_records=failed_records,
                    )
                    logger.warning(f"[UploadManager] 上传失败，保留本地文件稍后重试: {file_name}")
            except asyncio.TimeoutError:
                self._mark_failed(
                    db=db,
                    record_id=record_id,
                    file_name=file_name,
                    bvid=bvid,
                    title=title,
                    uploader=uploader,
                    file_size=file_size,
                    reason="上传超时（超过 5 分钟）",
                    failed_records=failed_records,
                )
                logger.error(f"[UploadManager] 上传超时: {file_name}")
            except Exception as e:
                self._mark_failed(
                    db=db,
                    record_id=record_id,
                    file_name=file_name,
                    bvid=bvid,
                    title=title,
                    uploader=uploader,
                    file_size=file_size,
                    reason=f"上传异常: {e}",
                    failed_records=failed_records,
                )
                logger.exception(f"[UploadManager] 上传异常: {file_name}")
            finally:
                self._processing.discard(file_name)

        if failed_records:
            reasons = ", ".join({r["reason"] for r in failed_records})
            logger.warning(
                f"[UploadManager] 本批次失败 {len(failed_records)} 个（原因: {reasons}），"
                f"已加入失败记录并跳过"
            )

        logger.info(
            f"[UploadManager] 批次完成: 成功 {len(success_paths)}/{len(records)}, "
            f"等待 {self.BATCH_INTERVAL} 秒后处理下一批"
        )
        await asyncio.sleep(self.BATCH_INTERVAL)
