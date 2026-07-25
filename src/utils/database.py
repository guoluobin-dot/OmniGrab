"""
数据库模块
==========
使用 SQLite 存储下载历史，实现去重功能。
"""

import os
import sqlite3
from datetime import datetime


class DownloadDB:
    """下载历史数据库"""

    def __init__(self, db_path: str = "downloads/history.db"):
        """
        初始化数据库

        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS download_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aweme_id TEXT UNIQUE NOT NULL,
                description TEXT,
                type TEXT,
                file_path TEXT,
                downloaded_at TEXT,
                status TEXT DEFAULT 'success'
            )
        """)
        conn.commit()
        conn.close()

    def is_downloaded(self, aweme_id: str) -> bool:
        """检查作品是否已下载过"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM download_history WHERE aweme_id = ? AND status = 'success'",
            (aweme_id,),
        )
        result = cursor.fetchone()
        conn.close()
        return result is not None

    def add_record(
        self,
        aweme_id: str,
        description: str,
        post_type: str,
        file_path: str,
    ):
        """添加下载记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT OR REPLACE INTO download_history
               (aweme_id, description, type, file_path, downloaded_at, status)
               VALUES (?, ?, ?, ?, ?, 'success')""",
            (aweme_id, description, post_type, file_path, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

    def get_all_records(self) -> list:
        """获取所有下载记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT aweme_id, description, type, file_path, downloaded_at "
            "FROM download_history ORDER BY downloaded_at DESC"
        )
        records = cursor.fetchall()
        conn.close()
        return records
