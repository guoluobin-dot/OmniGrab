"""
自动化 Cookie 获取模块测试
==========================
测试 Cookie 验证、字段检查等功能。
（浏览器自动化部分需要真实环境，此处仅测试逻辑层）
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.auto_cookie import AutoCookieFetcher, _get_missing_keys


class TestCookieValidation(unittest.TestCase):
    """测试 Cookie 验证逻辑"""

    def test_valid_cookie_with_ttwid(self):
        """包含 ttwid 的 Cookie 验证通过"""
        cookie = "ttwid=abc123; msToken=xyz; sessionid=def"
        fetcher = AutoCookieFetcher()
        is_valid, missing = fetcher._validate_cookie(cookie)
        self.assertTrue(is_valid)
        self.assertEqual(len(missing), 0)

    def test_cookie_missing_ttwid(self):
        """缺少 ttwid 的 Cookie 验证失败"""
        cookie = "msToken=xyz; sessionid=def"
        fetcher = AutoCookieFetcher()
        is_valid, missing = fetcher._validate_cookie(cookie)
        self.assertFalse(is_valid)
        self.assertIn("ttwid", missing)

    def test_empty_cookie(self):
        """空 Cookie 验证失败"""
        fetcher = AutoCookieFetcher()
        is_valid, missing = fetcher._validate_cookie("")
        self.assertFalse(is_valid)
        self.assertIn("ttwid", missing)

    def test_cookie_with_special_chars(self):
        """包含特殊字符的 Cookie"""
        cookie = "ttwid=abc-_123; msToken=xyz=="
        fetcher = AutoCookieFetcher()
        is_valid, _ = fetcher._validate_cookie(cookie)
        self.assertTrue(is_valid)


class TestGetMissingKeys(unittest.TestCase):
    """测试缺失字段检查"""

    def test_all_keys_present(self):
        """所有字段都存在"""
        cookie = "ttwid=abc; msToken=xyz"
        missing = _get_missing_keys(cookie, ["ttwid", "msToken"])
        self.assertEqual(len(missing), 0)

    def test_some_keys_missing(self):
        """部分字段缺失"""
        cookie = "ttwid=abc"
        missing = _get_missing_keys(cookie, ["ttwid", "msToken"])
        self.assertIn("msToken", missing)
        self.assertNotIn("ttwid", missing)

    def test_all_keys_missing(self):
        """所有字段都缺失"""
        cookie = "other=value"
        missing = _get_missing_keys(cookie, ["ttwid", "msToken"])
        self.assertIn("ttwid", missing)
        self.assertIn("msToken", missing)


class TestAutoPipelineImport(unittest.TestCase):
    """测试 AutoPipeline 能正确导入"""

    def test_import_auto_pipeline(self):
        """AutoPipeline 模块可导入"""
        from src.core.auto_pipeline import AutoPipeline
        pipeline = AutoPipeline(download_dir="test_downloads")
        self.assertIsNotNone(pipeline)
        self.assertEqual(pipeline.download_dir, "test_downloads")


if __name__ == "__main__":
    unittest.main()
