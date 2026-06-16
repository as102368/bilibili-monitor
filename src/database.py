import sqlite3
import os
from datetime import datetime


class DownloadDB:
    def __init__(self, db_path: str):
        folder = os.path.dirname(db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_table()

    def _init_table(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS downloaded (
                bvid TEXT PRIMARY KEY,
                title TEXT,
                uploader TEXT,
                uploader_id INTEGER,
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

    def is_downloaded(self, bvid: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM downloaded WHERE bvid = ?", (bvid,)
        )
        return cur.fetchone() is not None

    def mark_downloaded(self, bvid: str, title: str, uploader: str, uploader_id: int):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO downloaded
            (bvid, title, uploader, uploader_id, downloaded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (bvid, title, uploader, uploader_id, datetime.now().isoformat()),
        )
        self.conn.commit()

    def get_stats(self) -> dict:
        cur = self.conn.execute("SELECT COUNT(*) FROM downloaded")
        total = cur.fetchone()[0]
        return {"total_downloaded": total}
