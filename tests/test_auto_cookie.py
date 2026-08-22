"""
自动化 Cookie 获取模块测试
==========================
测试 Cookie 验证、字段检查等功能。
（浏览器自动化部分需要真实环境，此处仅测试逻辑层）
"""

import sys
import os
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.auto_cookie import AutoCookieFetcher, _get_missing_keys
from src.utils.cookie_helper import validate_cookie


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

    def test_cookie_name_match_is_exact(self):
        """相似字段不能冒充必需 Cookie 字段"""
        fetcher = AutoCookieFetcher()
        is_valid, missing = fetcher._validate_cookie("not_ttwid=abc; msToken=xyz")
        self.assertFalse(is_valid)
        self.assertIn("ttwid", missing)

    def test_saved_cookie_validation_uses_exact_names(self):
        """手动粘贴 Cookie 的校验同样不能接受相似字段。"""
        self.assertFalse(validate_cookie("not_ttwid=abc; other=value"))
        self.assertTrue(validate_cookie("ttwid=abc; other=value"))


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


class TestCookieFetchStrategy(unittest.TestCase):
    """测试浏览器和手动登录的分支选择，不启动真实浏览器。"""

    def test_wait_for_login_uses_selenium_when_available(self):
        fetcher = AutoCookieFetcher.__new__(AutoCookieFetcher)
        fetcher._has_selenium = True
        fetcher._fetch_with_selenium = Mock(return_value=("cookie", None))
        fetcher._fetch_with_requests = Mock(return_value=("requests-cookie", None))

        cookie, error = fetcher.fetch_cookie(wait_for_login=True)

        self.assertEqual((cookie, error), ("cookie", None))
        fetcher._fetch_with_selenium.assert_called_once_with(
            fetcher.DOUYIN_HOME, 3, True
        )
        fetcher._fetch_with_requests.assert_not_called()

    def test_wait_for_login_reports_missing_selenium(self):
        fetcher = AutoCookieFetcher.__new__(AutoCookieFetcher)
        fetcher._has_selenium = False
        fetcher._fetch_with_requests = Mock()

        cookie, error = fetcher.fetch_cookie(wait_for_login=True)

        self.assertIsNone(cookie)
        self.assertIn("selenium", error)
        fetcher._fetch_with_requests.assert_not_called()


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
