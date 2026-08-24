"""代理探测与媒体链接刷新的单元测试。"""
import pytest
import requests

from douyin_core import proxy as proxy_module
from douyin_core.downloader import Downloader
from douyin_core.platforms import TikTokAdapter
from douyin_core.refresh import profile_url_for_post


class TestProxyCandidates:
    def test_direct_is_always_first(self, monkeypatch):
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.delenv("ALL_PROXY", raising=False)
        monkeypatch.delenv("all_proxy", raising=False)
        monkeypatch.setattr(proxy_module, "_registry_proxy_settings", lambda: (False, ""))
        assert proxy_module.proxy_candidates() == [None]

    def test_env_proxy_is_included(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setattr(proxy_module, "_registry_proxy_settings", lambda: (False, ""))
        candidates = proxy_module.proxy_candidates()
        assert candidates[0] is None
        assert {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"} in candidates

    def test_enabled_registry_proxy_is_used(self, monkeypatch):
        for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(
            proxy_module, "_registry_proxy_settings", lambda: (True, "127.0.0.1:4780")
        )
        candidates = proxy_module.proxy_candidates()
        assert {"http": "http://127.0.0.1:4780", "https": "http://127.0.0.1:4780"} in candidates

    def test_disabled_local_proxy_probed_only_when_port_alive(self, monkeypatch):
        for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(
            proxy_module, "_registry_proxy_settings", lambda: (False, "127.0.0.1:4780")
        )
        monkeypatch.setattr(proxy_module, "_port_alive", lambda host, port, timeout=1.0: True)
        assert {"http": "http://127.0.0.1:4780", "https": "http://127.0.0.1:4780"} in proxy_module.proxy_candidates()

        monkeypatch.setattr(proxy_module, "_port_alive", lambda host, port, timeout=1.0: False)
        assert proxy_module.proxy_candidates() == [None]

    def test_per_protocol_registry_value_is_parsed(self, monkeypatch):
        monkeypatch.setattr(
            proxy_module,
            "_registry_proxy_settings",
            lambda: (True, "http=127.0.0.1:8080;https=127.0.0.1:8443"),
        )
        candidates = proxy_module.proxy_candidates()
        assert {"http": "http://127.0.0.1:8443", "https": "http://127.0.0.1:8443"} in candidates


class TestDownloaderProxyFallback:
    class _Response:
        def __init__(self, status_code=200):
            self.status_code = status_code
            self.headers = {"content-length": "4", "content-type": "video/mp4"}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.exceptions.HTTPError(str(self.status_code))

        def iter_content(self, chunk_size=8192):
            yield b"data"

    def test_connection_reset_falls_back_to_proxy(self, tmp_path, monkeypatch):
        calls = []

        class Session:
            headers = {}

            def get(self, _url, **kwargs):
                calls.append(kwargs.get("proxies"))
                if kwargs.get("proxies") is None:
                    raise requests.exceptions.ConnectionError("reset")
                return TestDownloaderProxyFallback._Response()

        downloader = Downloader(str(tmp_path), deduplicate=False, session=Session())
        monkeypatch.setattr(
            "douyin_core.downloader.proxy_candidates",
            lambda: [None, {"http": "http://127.0.0.1:4780", "https": "http://127.0.0.1:4780"}],
        )

        assert downloader.download_file("https://cdn.example.com/v.mp4", str(tmp_path / "v.mp4")) is True
        assert calls[0] is None
        assert calls[1] == {"http": "http://127.0.0.1:4780", "https": "http://127.0.0.1:4780"}
        # 记住可用代理，后续直接使用
        assert downloader.download_file("https://cdn.example.com/v2.mp4", str(tmp_path / "v2.mp4")) is True
        assert calls[2] == {"http": "http://127.0.0.1:4780", "https": "http://127.0.0.1:4780"}

    def test_403_html_reports_expired_link(self, tmp_path):
        class Session:
            headers = {}

            def get(self, _url, **kwargs):
                response = requests.Response()
                response.status_code = 403
                response.headers["content-type"] = "text/html"
                response._content = b"<html>forbidden</html>"
                return response

        downloader = Downloader(str(tmp_path), deduplicate=False, session=Session())
        ok = downloader.download_file("https://cdn.example.com/expired.mp4", str(tmp_path / "x.mp4"))
        assert ok is False
        assert "过期" in downloader.last_error


class TestRefresh:
    def test_profile_url_derived_from_tiktok_web_url(self):
        post = {"web_url": "https://www.tiktok.com/@linh.nky08/video/123", "author": {}}
        assert profile_url_for_post(post) == "https://www.tiktok.com/@linh.nky08"

    def test_profile_url_derived_from_douyin_sec_uid(self):
        post = {"web_url": "", "author": {"sec_uid": "MS4wLjABAAA"}}
        assert profile_url_for_post(post) == "https://www.douyin.com/user/MS4wLjABAAA"

    def test_untraceable_post_returns_empty(self):
        assert profile_url_for_post({"web_url": "", "author": {}}) == ""


class TestTikTokDisplayFields:
    def test_parse_item_includes_gui_display_strings(self):
        post = TikTokAdapter().parse_item({
            "id": "7301",
            "desc": "x",
            "createTime": 1750000000,
            "author": {"uniqueId": "u"},
            "video": {"playAddr": "https://cdn/v.mp4"},
        })
        assert post["type_str"] == "视频"
        assert post["create_time_str"] != "未知"
        assert post["create_time_str"][4] == "-"
