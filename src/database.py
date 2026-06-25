import sqlite3
import os
from datetime import datetime


def _format_ts(dt: datetime | None = None) -> str:
    """返回精确到秒的时间字符串（不含毫秒）"""
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


class DownloadDB:
    def __init__(self, db_path: str):
        folder = os.path.dirname(db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_table()
        self._init_uploads_table()
        self._init_file_metadata_table()
        self._init_failures_table()

    def _init_table(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS downloaded (
                bvid TEXT PRIMARY KEY,
                title TEXT,
                uploader TEXT,
                uploader_id INTEGER,
                quality TEXT,
                downloaded_at TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monitor_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        self.conn.commit()
        self._migrate_add_quality_column()

    def _migrate_add_quality_column(self):
        try:
            self.conn.execute("ALTER TABLE downloaded ADD COLUMN quality TEXT")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass

    def is_downloaded(self, bvid: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM downloaded WHERE bvid = ?", (bvid,)
        )
        if cur.fetchone() is not None:
            return True
        # 已在失败记录中标记为跳过的充电专属视频也视为"处理过"
        cur2 = self.conn.execute(
            "SELECT 1 FROM failures WHERE bvid = ? AND status = 'skipped'", (bvid,)
        )
        return cur2.fetchone() is not None

    def mark_downloaded(self, bvid: str, title: str, uploader: str, uploader_id: int, quality: str = ""):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO downloaded
            (bvid, title, uploader, uploader_id, quality, downloaded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (bvid, title, uploader, uploader_id, quality, _format_ts()),
        )
        self.conn.commit()

    def get_stats(self) -> dict:
        cur = self.conn.execute("SELECT COUNT(*) FROM downloaded")
        total = cur.fetchone()[0]
        return {"total_downloaded": total}

    def get_downloaded_list(self, limit: int = 10000) -> list:
        cur = self.conn.execute(
            """
            SELECT bvid, title, uploader, uploader_id, quality, downloaded_at
            FROM downloaded
            ORDER BY downloaded_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        result = []
        for row in rows:
            result.append({
                "bvid": row[0],
                "title": row[1],
                "uploader": row[2],
                "uploader_id": row[3],
                "quality": row[4] or "",
                "downloaded_at": row[5],
            })
        return result

    # ---------- uploads ----------

    def _init_uploads_table(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bvid TEXT,
                title TEXT,
                uploader TEXT,
                file_name TEXT,
                file_path TEXT,
                file_size INTEGER,
                status TEXT,
                message TEXT,
                uploaded_at TEXT
            )
            """
        )
        self.conn.commit()
        self._migrate_add_file_path_column()

    def _migrate_add_file_path_column(self):
        try:
            self.conn.execute("ALTER TABLE uploads ADD COLUMN file_path TEXT")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass

    def add_upload_record(
        self,
        bvid: str,
        title: str,
        uploader: str,
        file_name: str,
        file_size: int,
        status: str,
        message: str = "",
        file_path: str = "",
    ):
        self.conn.execute(
            """
            INSERT INTO uploads
            (bvid, title, uploader, file_name, file_path, file_size, status, message, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (bvid, title, uploader, file_name, file_path, file_size, status, message, _format_ts()),
        )
        self.conn.commit()

    def add_pending_upload(
        self,
        file_path: str,
        bvid: str,
        title: str,
        uploader: str,
        file_size: int,
    ) -> int:
        file_name = os.path.basename(file_path)
        cur = self.conn.execute(
            """
            INSERT INTO uploads
            (bvid, title, uploader, file_name, file_path, file_size, status, message, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (bvid, title, uploader, file_name, file_path, file_size, "pending", "", _format_ts()),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_pending_uploads(self, limit: int = 10) -> list:
        cur = self.conn.execute(
            """
            SELECT id, bvid, title, uploader, file_name, file_path, file_size, uploaded_at
            FROM uploads
            WHERE status = 'pending'
            ORDER BY uploaded_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row[0],
                "bvid": row[1],
                "title": row[2],
                "uploader": row[3],
                "file_name": row[4],
                "file_path": row[5],
                "file_size": row[6],
                "uploaded_at": row[7],
            })
        return result

    def count_pending_uploads(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM uploads WHERE status = 'pending'")
        return cur.fetchone()[0]

    def update_upload_status(self, record_id: int, status: str, message: str = ""):
        self.conn.execute(
            "UPDATE uploads SET status = ?, message = ?, uploaded_at = ? WHERE id = ?",
            (status, message, _format_ts(), record_id),
        )
        self.conn.commit()

    # ---------- file metadata (for directory-scan upload records) ----------

    def _init_file_metadata_table(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT,
                file_path TEXT,
                bvid TEXT,
                title TEXT,
                uploader TEXT,
                created_at TEXT
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_file_metadata_name ON file_metadata(file_name)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_file_metadata_path ON file_metadata(file_path)"
        )
        self.conn.commit()

    def add_file_metadata(self, file_path: str, bvid: str, title: str, uploader: str):
        file_name = os.path.basename(file_path)
        self.conn.execute(
            """
            INSERT INTO file_metadata (file_name, file_path, bvid, title, uploader, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (file_name, file_path, bvid, title, uploader, _format_ts()),
        )
        self.conn.commit()

    def get_file_metadata_by_name(self, file_name: str) -> dict:
        cur = self.conn.execute(
            "SELECT bvid, title, uploader FROM file_metadata WHERE file_name = ? ORDER BY created_at DESC LIMIT 1",
            (file_name,),
        )
        row = cur.fetchone()
        if row:
            return {"bvid": row[0], "title": row[1], "uploader": row[2]}
        return {"bvid": "", "title": "", "uploader": ""}

    def get_file_metadata_by_path(self, file_path: str) -> dict:
        cur = self.conn.execute(
            "SELECT bvid, title, uploader FROM file_metadata WHERE file_path = ? ORDER BY created_at DESC LIMIT 1",
            (file_path,),
        )
        row = cur.fetchone()
        if row:
            return {"bvid": row[0], "title": row[1], "uploader": row[2]}
        return {"bvid": "", "title": "", "uploader": ""}

    def get_upload_list(self, limit: int = 10000) -> list:
        cur = self.conn.execute(
            """
            SELECT id, bvid, title, uploader, file_name, file_size, status, message, uploaded_at
            FROM uploads
            ORDER BY uploaded_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row[0],
                "bvid": row[1],
                "title": row[2],
                "uploader": row[3],
                "file_name": row[4],
                "file_size": row[5],
                "status": row[6],
                "message": row[7],
                "uploaded_at": row[8],
            })
        return result

    # ---------- failures ----------

    def _init_failures_table(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bvid TEXT,
                title TEXT,
                uploader TEXT,
                reason TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
            """
        )
        self.conn.commit()
        self._migrate_add_fail_count_column()

    def _migrate_add_fail_count_column(self):
        try:
            self.conn.execute("ALTER TABLE failures ADD COLUMN fail_count INTEGER DEFAULT 1")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass

    def add_failure(self, bvid: str, title: str, uploader: str, reason: str):
        """添加或更新失败记录；若已存在同 BV 的 pending/retried/skipped 记录，仅更新原因和时间"""
        cur = self.conn.execute(
            """
            UPDATE failures
            SET reason = ?, fail_count = COALESCE(fail_count, 1) + 1, created_at = ?, status = 'pending'
            WHERE bvid = ? AND status IN ('pending', 'retried', 'skipped')
            """,
            (reason, _format_ts(), bvid),
        )
        if cur.rowcount == 0:
            self.conn.execute(
                """
                INSERT INTO failures (bvid, title, uploader, reason, status, fail_count, created_at)
                VALUES (?, ?, ?, ?, 'pending', 1, ?)
                """,
                (bvid, title, uploader, reason, _format_ts()),
            )
        self.conn.commit()

    def get_pending_failure_info(self, bvid: str) -> dict:
        cur = self.conn.execute(
            """
            SELECT reason, COALESCE(fail_count, 1) as fail_count
            FROM failures
            WHERE bvid = ? AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (bvid,),
        )
        row = cur.fetchone()
        if row:
            return {"reason": row[0], "fail_count": row[1]}
        return {"reason": "", "fail_count": 0}

    def get_failures(self, limit: int = 10000) -> list:
        cur = self.conn.execute(
            """
            SELECT id, bvid, title, uploader, reason, status, created_at
            FROM failures
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row[0],
                "bvid": row[1],
                "title": row[2],
                "uploader": row[3],
                "reason": row[4],
                "status": row[5],
                "created_at": row[6],
            })
        return result

    def mark_failure_retried(self, failure_id: int):
        self.conn.execute(
            "UPDATE failures SET status = 'retried' WHERE id = ?",
            (failure_id,),
        )
        self.conn.commit()

    def update_failure_status(self, failure_id: int, status: str, reason: str | None = None):
        if reason is not None:
            self.conn.execute(
                "UPDATE failures SET status = ?, reason = ?, created_at = ? WHERE id = ?",
                (status, reason, _format_ts(), failure_id),
            )
        else:
            self.conn.execute(
                "UPDATE failures SET status = ?, created_at = ? WHERE id = ?",
                (status, _format_ts(), failure_id),
            )
        self.conn.commit()

    def get_failure_by_bvid(self, bvid: str) -> dict:
        cur = self.conn.execute(
            "SELECT id, reason, status FROM failures WHERE bvid = ? ORDER BY created_at DESC LIMIT 1",
            (bvid,),
        )
        row = cur.fetchone()
        if row:
            return {"id": row[0], "reason": row[1], "status": row[2]}
        return {}

    def mark_failure_skipped(self, bvid: str):
        self.conn.execute(
            "UPDATE failures SET status = 'skipped' WHERE bvid = ? AND status = 'pending'",
            (bvid,),
        )
        self.conn.commit()

    def delete_failure(self, failure_id: int):
        self.conn.execute("DELETE FROM failures WHERE id = ?", (failure_id,))
        self.conn.commit()

    def delete_failures(self, failure_ids: list):
        if not failure_ids:
            return
        placeholders = ",".join("?" * len(failure_ids))
        self.conn.execute(f"DELETE FROM failures WHERE id IN ({placeholders})", tuple(failure_ids))
        self.conn.commit()

    def delete_downloaded_records(self, bvids: list):
        if not bvids:
            return
        placeholders = ",".join("?" * len(bvids))
        self.conn.execute(f"DELETE FROM downloaded WHERE bvid IN ({placeholders})", tuple(bvids))
        self.conn.commit()

    def delete_upload_records(self, record_ids: list):
        if not record_ids:
            return
        placeholders = ",".join("?" * len(record_ids))
        self.conn.execute(f"DELETE FROM uploads WHERE id IN ({placeholders})", tuple(record_ids))
        self.conn.commit()

    def clear_failures(self):
        self.conn.execute("DELETE FROM failures")
        self.conn.commit()

    def clear_downloaded(self):
        self.conn.execute("DELETE FROM downloaded")
        self.conn.commit()

    def clear_uploads(self):
        self.conn.execute("DELETE FROM uploads")
        self.conn.commit()
