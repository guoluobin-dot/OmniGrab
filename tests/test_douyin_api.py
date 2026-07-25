"""
抖音 API 模块测试
==================
测试 sec_uid 提取、作品信息解析等核心功能。
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.douyin_api import DouyinAPI


class TestSecUidExtraction(unittest.TestCase):
    """测试 sec_uid 提取"""

    def test_extract_from_long_url(self):
        """从长链接中提取 sec_uid"""
        url = "https://www.douyin.com/user/MS4wLjABAAAAtest_sec_uid_value123"
        sec_uid = DouyinAPI.extract_sec_uid(url)
        self.assertEqual(sec_uid, "MS4wLjABAAAAtest_sec_uid_value123")

    def test_extract_from_url_with_params(self):
        """从带参数的链接中提取 sec_uid"""
        url = "https://www.douyin.com/user/MS4wLjABAAAAtest_sec_uid?sec_uid=MS4wLjABAAAAparam_uid&other=1"
        sec_uid = DouyinAPI.extract_sec_uid(url)
        self.assertEqual(sec_uid, "MS4wLjABAAAAparam_uid")

    def test_extract_from_invalid_url(self):
        """无效链接返回 None"""
        url = "https://www.example.com/some/random/page"
        sec_uid = DouyinAPI.extract_sec_uid(url)
        # 可能从路径中提取也可能返回 None
        # 这里测试的是完全没有 sec_uid 的链接
        self.assertIsNone(sec_uid)

    def test_extract_from_path_format(self):
        """从路径格式链接中提取"""
        url = "https://www.douyin.com/user/MS4wLjABAAAApath_based_uid_456"
        sec_uid = DouyinAPI.extract_sec_uid(url)
        # sec_uid 在 URL 中
        self.assertIsNotNone(sec_uid)


class TestAwemeParsing(unittest.TestCase):
    """测试作品信息解析"""

    def test_parse_video_aweme(self):
        """解析视频作品"""
        mock_aweme = {
            "aweme_id": "test_video_001",
            "desc": "测试视频",
            "create_time": 1700000000,
            "video": {
                "play_addr": {"url_list": ["https://example.com/video.mp4"]},
                "cover": {"url_list": ["https://example.com/cover.jpg"]},
                "duration": 30000,
            },
            "images": [],
            "statistics": {
                "digg_count": 100,
                "comment_count": 20,
                "share_count": 5,
                "play_count": 1000,
            },
            "music": {"play_url": {"url_list": ["https://example.com/music.mp3"]}},
        }

        post = DouyinAPI._parse_aweme(mock_aweme)
        self.assertIsNotNone(post)
        self.assertEqual(post["aweme_id"], "test_video_001")
        self.assertEqual(post["desc"], "测试视频")
        self.assertEqual(post["type"], "video")
        self.assertEqual(post["type_str"], "视频")
        self.assertEqual(post["video_url"], "https://example.com/video.mp4")
        self.assertEqual(post["duration"], 30000)
        self.assertEqual(post["stats"]["digg_count"], 100)

    def test_parse_image_set_aweme(self):
        """解析图集作品"""
        mock_aweme = {
            "aweme_id": "test_image_001",
            "desc": "测试图集",
            "create_time": 1700000000,
            "video": {},
            "images": [
                {"url_list": ["https://example.com/img1_low.jpg", "https://example.com/img1_high.jpg"]},
                {"url_list": ["https://example.com/img2_low.jpg", "https://example.com/img2_high.jpg"]},
            ],
            "statistics": {"digg_count": 50, "comment_count": 5, "share_count": 2, "play_count": 500},
            "music": {},
        }

        post = DouyinAPI._parse_aweme(mock_aweme)
        self.assertIsNotNone(post)
        self.assertEqual(post["aweme_id"], "test_image_001")
        self.assertEqual(post["type"], "image")
        self.assertEqual(post["type_str"], "图文")
        self.assertEqual(len(post["image_urls"]), 2)
        self.assertEqual(post["image_urls"][0], "https://example.com/img1_high.jpg")
        self.assertEqual(post["image_urls"][1], "https://example.com/img2_high.jpg")

    def test_parse_empty_aweme(self):
        """解析空作品不崩溃"""
        post = DouyinAPI._parse_aweme({})
        self.assertIsNotNone(post)
        self.assertEqual(post["desc"], "无标题")
        self.assertEqual(post["type"], "video")


class TestNoWatermark(unittest.TestCase):
    """测试无水印链接提取"""

    def test_replace_playwm(self):
        """playwm 替换为 play"""
        url = "https://example.com/playwm/video.mp4"
        no_wm = DouyinAPI.get_no_watermark_url(url)
        self.assertEqual(no_wm, "https://example.com/play/video.mp4")

    def test_empty_url(self):
        """空 URL 返回空"""
        self.assertEqual(DouyinAPI.get_no_watermark_url(""), "")


class TestSafeFilename(unittest.TestCase):
    """测试安全文件名"""

    def test_remove_illegal_chars(self):
        from src.core.downloader import Downloader
        name = 'test<>:"/\\|?*file'
        safe = Downloader._safe_filename(name)
        self.assertNotIn("<", safe)
        self.assertNotIn(">", safe)
        self.assertNotIn(":", safe)
        self.assertNotIn('"', safe)
        self.assertNotIn("/", safe)
        self.assertNotIn("\\", safe)
        self.assertNotIn("|", safe)
        self.assertNotIn("?", safe)
        self.assertNotIn("*", safe)

    def test_truncate_long_name(self):
        from src.core.downloader import Downloader
        name = "a" * 100
        safe = Downloader._safe_filename(name, max_length=50)
        self.assertEqual(len(safe), 50)

    def test_empty_name(self):
        from src.core.downloader import Downloader
        safe = Downloader._safe_filename("")
        self.assertEqual(safe, "unnamed")


if __name__ == "__main__":
    unittest.main()
