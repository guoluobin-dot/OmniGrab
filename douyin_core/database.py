from __future__ import annotations

import os
import sqlite3
import json
from contextlib import closing
from datetime import datetime


class DownloadDB:
    def __init__(self, db_path: str = "downloads/history.db") -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        with closing(sqlite3.connect(db_path)) as connection:
            with connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS download_history (
                    aweme_id TEXT UNIQUE NOT NULL, description TEXT, type TEXT, file_path TEXT,
                    downloaded_at TEXT, status TEXT DEFAULT 'success')""")

    def is_downloaded(self, aweme_id: str) -> bool:
        with closing(sqlite3.connect(self.db_path)) as connection:
            record = connection.execute(
                "SELECT file_path FROM download_history "
                "WHERE aweme_id=? AND status='success'",
                (aweme_id,),
            ).fetchone()

        if not record or not record[0]:
            return False

        paths = self._stored_paths(record[0])
        if paths:
            return all(self._file_is_complete(path) for path in paths)

        path = record[0]
        if os.path.isfile(path):
            return self._file_is_complete(path)

        if not os.path.isdir(path):
            return False

        # A picture post is stored as a directory.  An empty folder can be
        # left behind by an interrupted download, so it is not sufficient
        # evidence to skip the work on a later run.
        for root, _directories, filenames in os.walk(path):
            for filename in filenames:
                try:
                    if os.path.getsize(os.path.join(root, filename)) > 0:
                        return True
                except OSError:
                    continue
        return False

    @staticmethod
    def _stored_paths(value: str) -> list[str]:
        """Read image-file lists stored by the flat image-library layout."""
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list) or not parsed or not all(
            isinstance(path, str) and path for path in parsed
        ):
            return []
        return parsed

    @staticmethod
    def _file_is_complete(path: str) -> bool:
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 0
        except OSError:
            return False

    def add_record(
        self,
        aweme_id: str,
        description: str,
        post_type: str,
        file_path: str | list[str],
    ) -> None:
        stored_path = json.dumps(file_path, ensure_ascii=False) if isinstance(file_path, list) else file_path
        with closing(sqlite3.connect(self.db_path)) as connection:
            with connection:
                connection.execute("INSERT OR REPLACE INTO download_history VALUES (?, ?, ?, ?, ?, 'success')", (aweme_id, description, post_type, stored_path, datetime.now().isoformat()))
