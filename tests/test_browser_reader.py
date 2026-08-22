"""浏览器网络响应解析的离线单元测试。"""

import json
import os
import unittest
from unittest.mock import Mock

from src.core.browser_reader import (
    BrowserProfileReader,
    cookie_header_from_browser,
    extract_aweme_list,
    extract_user_info,
    is_profile_response,
    parse_performance_message,
)
from douyin_core.platforms import DouyinAdapter


class TestBrowserResponseHelpers(unittest.TestCase):
    def test_cookie_header_filters_empty_items(self):
        cookie = cookie_header_from_browser([
            {"name": "ttwid", "value": "abc"},
            {"name": "", "value": "skip"},
            {"name": "msToken", "value": "xyz"},
        ])
        self.assertEqual(cookie, "ttwid=abc; msToken=xyz")

    def test_performance_message_unwraps_chrome_entry(self):
        entry = {
            "message": json.dumps({
                "message": {
                    "method": "Network.loadingFinished",
                    "params": {"requestId": "request-1"},
                }
            })
        }
        parsed = parse_performance_message(entry)
        self.assertEqual(parsed["method"], "Network.loadingFinished")
        self.assertEqual(parsed["params"]["requestId"], "request-1")

    def test_profile_response_detects_supported_endpoints(self):
        self.assertTrue(is_profile_response(
            "https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id=test"
        ))
        self.assertTrue(is_profile_response(
            "https://www.douyin.com/aweme/v1/web/user/profile/other/?sec_user_id=test"
        ))
        self.assertFalse(is_profile_response("https://www.douyin.com/aweme/v1/web/general/search/"))

    def test_payload_extractors_support_nested_data(self):
        payload = {
            "data": {
                "user": {
                    "nickname": "测试作者",
                    "sec_uid": "sec-id",
                    "aweme_count": 2,
                    "avatar_thumb": {"url_list": ["https://example.com/avatar.jpg"]},
                },
                "aweme_list": [{"aweme_id": "1"}, {"aweme_id": "2"}],
            }
        }
        self.assertEqual([item["aweme_id"] for item in extract_aweme_list(payload)], ["1", "2"])
        user = extract_user_info(payload)
        self.assertEqual(user["nickname"], "测试作者")
        self.assertEqual(user["avatar"], "https://example.com/avatar.jpg")

    def test_ingest_deduplicates_awemes(self):
        reader = BrowserProfileReader.__new__(BrowserProfileReader)
        reader.platform = DouyinAdapter()
        reader._raw_awemes = {}
        reader._user_info = {}
        payload = {
            "user": {"nickname": "作者", "aweme_count": 1},
            "aweme_list": [{"aweme_id": "same"}, {"aweme_id": "same"}],
        }
        reader._ingest_payload(payload)
        self.assertEqual(list(reader._raw_awemes), ["same"])
        self.assertEqual(reader._user_info["nickname"], "作者")

    def test_login_gate_is_detected_from_page_text(self):
        reader = BrowserProfileReader.__new__(BrowserProfileReader)
        reader.platform = DouyinAdapter()
        reader.driver = Mock()
        reader.driver.execute_script.return_value = True

        self.assertTrue(reader._has_login_gate())
        reader.driver.execute_script.assert_called_once()

    def test_default_browser_profile_is_persistent_project_storage(self):
        reader = BrowserProfileReader()
        self.assertEqual(os.path.basename(reader.browser_profile_dir), "browser_profile")
        self.assertIn("config", reader.browser_profile_dir)


if __name__ == "__main__":
    unittest.main()
