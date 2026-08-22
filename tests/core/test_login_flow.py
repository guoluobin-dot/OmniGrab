"""交互式登录等待与平台适配器的离线单元测试。"""
import pytest
from threading import Event
from unittest.mock import Mock

from douyin_core import BrowserReadError, BrowserProfileReader, SessionExpiredError
from douyin_core.platforms import (
    DouyinAdapter,
    TikTokAdapter,
    detect_platform,
    extract_aweme_list,
)


def make_reader(**overrides) -> BrowserProfileReader:
    """构造一个不启动浏览器的读取器，便于直接测试登录等待逻辑。"""
    auto_platform = overrides.pop("auto_platform", False)
    reader = BrowserProfileReader.__new__(BrowserProfileReader)
    reader.cookie = ""
    reader.headless = overrides.pop("headless", False)
    reader.timeout = 35
    reader.max_scrolls = 240
    reader.idle_rounds = 12
    reader.scroll_wait = 1.0
    reader.verification_timeout = overrides.pop("verification_timeout", 0)
    reader.user_agent = "test-agent"
    reader.browser_profile_dir = "."
    reader.keep_open = False
    # auto_platform=True 时保持为空，由 read_profile 按 URL 自动识别。
    reader.platform = None if auto_platform else (overrides.pop("platform", None) or DouyinAdapter())
    reader.continue_event = Event()
    reader.cancel_event = Event()
    reader.login_wait_callback = None
    reader._login_poll_interval = 0.01
    reader.driver = Mock()
    reader._raw_awemes = {}
    reader._user_info = {}
    reader._pending_requests = {}
    reader._processed_requests = set()
    reader._response_count = 0
    for name, value in overrides.items():
        setattr(reader, name, value)
    return reader


class TestPlatformDetection:
    def test_douyin_link_selects_douyin_adapter(self):
        assert detect_platform("https://www.douyin.com/user/MS4w").name == "douyin"

    def test_tiktok_link_selects_tiktok_adapter(self):
        assert detect_platform("https://www.tiktok.com/@example").name == "tiktok"

    def test_unknown_link_falls_back_to_douyin(self):
        assert detect_platform("https://example.com/user/x").name == "douyin"

    def test_douyin_adapter_matches_only_douyin_hosts(self):
        assert DouyinAdapter().matches("https://www.douyin.com/user/x")
        assert not DouyinAdapter().matches("https://www.tiktok.com/@x")

    def test_douyin_adapter_recognises_profile_endpoints(self):
        adapter = DouyinAdapter()
        assert adapter.is_profile_response("https://www.douyin.com/aweme/v1/web/aweme/post/?a=1")
        assert not adapter.is_profile_response("https://www.douyin.com/aweme/v1/web/search/item/")

    def test_extractors_are_shared_between_module_and_adapter(self):
        payload = {"aweme_list": [{"aweme_id": "1"}]}
        assert extract_aweme_list(payload) == DouyinAdapter().extract_items(payload)

    def test_tiktok_adapter_now_implements_collection(self):
        adapter = TikTokAdapter()
        assert adapter.implemented is True
        assert adapter.matches("https://www.tiktok.com/@x")
        assert adapter.parse_item({
            "id": "1",
            "author": {"uniqueId": "u"},
            "video": {"playAddr": "https://cdn.example.com/v.mp4"},
        })["video_url"] == "https://cdn.example.com/v.mp4"


class TestLoginSignals:
    def test_default_wait_is_unlimited(self):
        assert BrowserProfileReader().verification_timeout == 0

    def test_session_cookie_requires_real_login_cookie(self):
        reader = make_reader()
        reader.driver.get_cookies.return_value = [{"name": "ttwid"}, {"name": "msToken"}]
        assert reader.has_session_cookie() is False

        reader.driver.get_cookies.return_value = [{"name": "sessionid", "value": "s"}]
        assert reader.has_session_cookie() is True

    def test_session_cookie_without_browser_is_false(self):
        reader = make_reader()
        reader.driver = None
        assert reader.has_session_cookie() is False

    def test_gate_probe_contains_keywords_and_selectors(self):
        script = DouyinAdapter().gate_probe_script()
        assert "扫码登录" in script
        assert "captcha_verify_container" in script


class TestWaitForLogin:
    def test_cancel_event_raises_immediately(self):
        reader = make_reader()
        reader.cancel_event.set()
        with pytest.raises(SessionExpiredError, match="取消"):
            reader.wait_for_login("https://www.douyin.com/user/x")

    def test_headless_never_enters_interactive_wait(self):
        reader = make_reader(headless=True)
        with pytest.raises(SessionExpiredError, match="headless"):
            reader.wait_for_login("https://www.douyin.com/user/x")

    def test_manual_confirm_resumes_when_posts_arrive(self):
        collected = Mock()
        reader = make_reader(
            _collect_network_responses=collected,
            _navigate=Mock(),
            wait_for_document=Mock(),
            has_login_gate=Mock(return_value=False),
        )
        reader._raw_awemes = {"post-1": {}}
        reader.continue_event.set()

        reader.wait_for_login("https://www.douyin.com/user/x")

        collected.assert_called_once()
        reader._navigate.assert_called_once()

    def test_cookie_appearance_triggers_auto_resume(self):
        reader = make_reader(
            _navigate=Mock(),
            wait_for_document=Mock(),
            _collect_network_responses=Mock(),
            has_login_gate=Mock(return_value=False),
        )
        reader.driver.get_cookies.side_effect = [
            [{"name": "ttwid"}],
            [{"name": "sessionid", "value": "s"}],
        ]

        reader.wait_for_login("https://www.douyin.com/user/x")

        reader._navigate.assert_called_once()

    def test_still_gated_after_manual_click_keeps_waiting_until_timeout(self):
        reader = make_reader(
            verification_timeout=0.05,
            _navigate=Mock(),
            wait_for_document=Mock(),
            _collect_network_responses=Mock(),
            has_login_gate=Mock(return_value=True),
        )
        reader.continue_event.set()

        with pytest.raises(SessionExpiredError, match="超时"):
            reader.wait_for_login("https://www.douyin.com/user/x")

        reader._navigate.assert_called_once()

    def test_closed_browser_window_aborts_the_wait(self):
        reader = make_reader(_driver_alive=Mock(return_value=False))

        with pytest.raises(SessionExpiredError, match="浏览器窗口已关闭"):
            reader.wait_for_login("https://www.douyin.com/user/x")


class TestReadProfileGuards:
    def test_unimplemented_platform_is_rejected_before_opening_a_browser(self):
        from douyin_core.platforms import PlatformAdapter

        class _StubAdapter(PlatformAdapter):
            name = "stub"
            implemented = False

            def matches(self, url): return True
            def is_profile_response(self, url): return False
            def extract_items(self, payload): return []
            def extract_user_info(self, payload): return {}
            def parse_item(self, raw): return {}

        reader = make_reader(headless=True, platform=_StubAdapter())
        with pytest.raises(BrowserReadError, match="尚未实现"):
            reader.read_profile("https://stub.example.com/@x")
