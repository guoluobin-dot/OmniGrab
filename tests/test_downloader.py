"""下载器的离线单元测试。"""

import os
import tempfile
import unittest

from src.core.downloader import Downloader


class FakeResponse:
    def __init__(self, chunks, content_type="video/mp4"):
        self._chunks = chunks
        self.headers = {
            "content-length": str(sum(len(chunk) for chunk in chunks)),
            "content-type": content_type,
        }

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):
        del chunk_size
        yield from self._chunks


class FakeSession:
    def __init__(self, response):
        self.headers = {}
        self.response = response
        self.requested_urls = []

    def get(self, url, **kwargs):
        self.requested_urls.append((url, kwargs))
        return self.response


class TestDownloader(unittest.TestCase):
    def test_no_dedup_does_not_access_database(self):
        """--no-dedup 路径应跳过数据库查询和写入。"""
        with tempfile.TemporaryDirectory() as download_dir:
            downloader = Downloader(download_dir, deduplicate=False)
            downloader.download_post = lambda post: True

            result = downloader.download_batch([
                {"aweme_id": "post-1"},
                {"aweme_id": "post-2"},
            ])

        self.assertIsNone(downloader.db)
        self.assertEqual(result, {
            "success": 2,
            "failed": 0,
            "skipped": 0,
            "total": 2,
        })

    def test_download_file_writes_atomically_with_cookie_header(self):
        response = FakeResponse([b"media", b"-bytes"])
        session = FakeSession(response)
        with tempfile.TemporaryDirectory() as download_dir:
            target = os.path.join(download_dir, "video.mp4")
            downloader = Downloader(
                download_dir,
                deduplicate=False,
                cookie="ttwid=abc",
                session=session,
            )
            self.assertTrue(downloader.download_file("https://cdn.example.com/v.mp4", target))
            with open(target, "rb") as file:
                self.assertEqual(file.read(), b"media-bytes")
            self.assertFalse(os.path.exists(target + ".part"))

        self.assertEqual(session.headers["Cookie"], "ttwid=abc")
        self.assertTrue(session.requested_urls)

    def test_download_file_rejects_html_error_page(self):
        response = FakeResponse([b"<html>blocked</html>"], content_type="text/html")
        with tempfile.TemporaryDirectory() as download_dir:
            target = os.path.join(download_dir, "video.mp4")
            downloader = Downloader(
                download_dir,
                max_retry=1,
                deduplicate=False,
                session=FakeSession(response),
            )
            self.assertFalse(downloader.download_file("https://cdn.example.com/v.mp4", target))
            self.assertFalse(os.path.exists(target))


if __name__ == "__main__":
    unittest.main()
