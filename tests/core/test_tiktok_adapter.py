"""TikTok 适配器的解析与登录检测单元测试。"""
import pytest
from unittest.mock import Mock

from douyin_core import BrowserProfileReader
from douyin_core.downloader import Downloader
from douyin_core.platforms import TikTokAdapter, detect_platform
from douyin_core.risks import ParseError

VIDEO_ITEM = {
    "id": "7301",
    "desc": "funny cat",
    "createTime": 1750000000,
    "video": {
        "playAddr": "https://v16.tiktokcdn.com/play.mp4",
        "downloadAddr": "https://v16.tiktokcdn.com/download.mp4",
    },
    "stats": {"diggCount": "12", "commentCount": 3, "shareCount": 1, "collectCount": 5},
    "author": {"uniqueId": "linh.nky08", "nickname": "Linh", "id": "7001", "secUid": "sec-1"},
}

IMAGE_ITEM = {
    "id": "7302",
    "desc": "photo set",
    "createTime": 1750000100,
    "imagePost": {
        "images": [
            {"imageURL": {"urlList": ["https://p16-sign/a.jpg", "https://p19/a_alt.jpg"]}},
            {"imageURL": {"urlList": ["https://p16-sign/b.jpg"]}},
        ]
    },
    "stats": {"diggCount": 9},
    "author": {"uniqueId": "linh.nky08"},
}

USER_INFO_PAYLOAD = {
    "itemList": [VIDEO_ITEM],
    "userInfo": {
        "user": {"nickname": "Linh", "uniqueId": "linh.nky08", "id": "7001", "signature": "hi"},
        "stats": {"followerCount": "1000", "followingCount": 10, "videoCount": 42, "heartCount": "999"},
    },
}


class TestDetection:
    def test_tiktok_link_selects_implemented_adapter(self):
        adapter = detect_platform("https://www.tiktok.com/@linh.nky08")
        assert isinstance(adapter, TikTokAdapter)
        assert adapter.implemented is True

    def test_host_matching(self):
        assert TikTokAdapter().matches("https://www.tiktok.com/@x")
        assert TikTokAdapter().matches("https://vm.tiktok.com/abc")
        assert not TikTokAdapter().matches("https://www.douyin.com/user/x")

    def test_profile_response_endpoints(self):
        adapter = TikTokAdapter()
        assert adapter.is_profile_response("https://www.tiktok.com/api/post/item_list/?secUid=x")
        assert adapter.is_profile_response("https://www.tiktok.com/graphql/")
        assert not adapter.is_profile_response("https://www.tiktok.com/aweme/v1/web/aweme/post/")


class TestExtraction:
    def test_items_from_rest_and_graphql_shapes(self):
        adapter = TikTokAdapter()
        assert [i["id"] for i in adapter.extract_items(USER_INFO_PAYLOAD)] == ["7301"]
        nested = {"data": {"itemList": [{"id": "9"}]}}
        assert [i["id"] for i in adapter.extract_items(nested)] == ["9"]
        assert adapter.extract_items({"data": {"user": {}}}) == []

    def test_user_info_maps_camel_case_stats(self):
        info = TikTokAdapter().extract_user_info(USER_INFO_PAYLOAD)
        assert info["nickname"] == "Linh"
        assert info["uid"] == "7001"
        assert info["follower_count"] == 1000
        assert info["aweme_count"] == 42
        assert info["favoriting_count"] == 999


class TestParseItem:
    def test_video_item_prefers_download_addr(self):
        post = TikTokAdapter().parse_item(VIDEO_ITEM)
        assert post["type"] == "video"
        assert post["video_url"] == "https://v16.tiktokcdn.com/download.mp4"
        assert post["aweme_id"] == "7301"
        assert post["stats"]["digg_count"] == 12
        assert "/video/7301" in post["web_url"]
        assert post["referer"] == "https://www.tiktok.com/"

    def test_image_item_collects_one_url_per_image(self):
        post = TikTokAdapter().parse_item(IMAGE_ITEM)
        assert post["type"] == "image"
        assert post["image_urls"] == ["https://p16-sign/a.jpg", "https://p16-sign/b.jpg"]
        assert post["video_url"] == ""
        assert "/photo/7302" in post["web_url"]

    def test_legacy_image_post_info_is_supported(self):
        legacy = {
            "id": "7303",
            "image_post_info": {"images": [{"display_image": {"url_list": ["https://cdn/c.jpg"]}}]},
            "author": {"uniqueId": "u"},
        }
        post = TikTokAdapter().parse_item(legacy)
        assert post["type"] == "image"
        assert post["image_urls"] == ["https://cdn/c.jpg"]

    def test_item_without_media_raises_parse_error(self):
        with pytest.raises(ParseError):
            TikTokAdapter().parse_item({"id": "7304", "desc": "empty"})

    def test_bitrate_info_falls_back_to_lowest_reasonable_stream(self):
        item = {
            "id": "7305",
            "author": {"uniqueId": "u"},
            "video": {"bitrateInfo": [
                {"Bitrate": 4_000_000, "PlayAddr": {"UrlList": ["https://cdn/hq.mp4"]}},
                {"Bitrate": 1_500_000, "PlayAddr": {"UrlList": ["https://cdn/lq.mp4"]}},
            ]},
        }
        assert TikTokAdapter().parse_item(item)["video_url"] == "https://cdn/lq.mp4"


class TestGateProbe:
    def test_probe_lowercases_page_text_for_english_keywords(self):
        script = TikTokAdapter().gate_probe_script()
        assert "toLowerCase()" in script
        assert "log in to see more" in script

    def test_bare_log_in_phrase_is_rejected_to_avoid_header_button(self):
        script = TikTokAdapter().gate_probe_script()
        assert '"log in"' not in script

    def test_session_cookie_names_include_sessionid_ss(self):
        assert "sessionid_ss" in TikTokAdapter().login_cookie_names


class TestReaderIngest:
    def test_ingest_deduplicates_by_tiktok_id(self):
        reader = BrowserProfileReader.__new__(BrowserProfileReader)
        reader.platform = TikTokAdapter()
        reader._raw_awemes = {}
        reader._user_info = {}
        reader._ingest_payload(USER_INFO_PAYLOAD)
        reader._ingest_payload(dict(USER_INFO_PAYLOAD))
        assert list(reader._raw_awemes) == ["7301"]
        assert reader._user_info["nickname"] == "Linh"


def test_find_item_containers_walks_ssr_state():
    from douyin_core.browser_reader import find_item_containers

    universal = {
        "__DEFAULT_SCOPE__": {
            "webapp.user-detail": {
                "itemList": [VIDEO_ITEM],
                "userInfo": USER_INFO_PAYLOAD["userInfo"],
            },
            "webapp.other": {"foo": 1},
        }
    }
    containers = list(find_item_containers(universal))
    assert len(containers) == 1
    assert containers[0]["itemList"][0]["id"] == "7301"


class TestDomHarvest:
    class _Element:
        def __init__(self, text):
            self._text = text

        def get_attribute(self, name):
            return self._text if name in ("textContent", "innerHTML") else None

    def _reader_with_driver(self, scripts: dict[str, str]):
        reader = BrowserProfileReader.__new__(BrowserProfileReader)
        reader.platform = TikTokAdapter()
        reader._raw_awemes = {}
        reader._user_info = {}

        def find_element(_by, selector):
            script_id = selector.split("'")[1]
            if script_id in scripts:
                return TestDomHarvest._Element(scripts[script_id])
            raise Exception("no such element")

        reader.driver = Mock()
        reader.driver.find_element.side_effect = find_element
        return reader

    def test_universal_data_script_feeds_posts_and_user(self):
        import json

        universal = json.dumps({
            "__DEFAULT_SCOPE__": {
                "webapp.user-detail": {
                    "itemList": [VIDEO_ITEM, IMAGE_ITEM],
                    "userInfo": USER_INFO_PAYLOAD["userInfo"],
                }
            }
        })
        reader = self._reader_with_driver({"__UNIVERSAL_DATA_FOR_REHYDRATION__": universal})

        reader._harvest_dom_payloads()

        assert sorted(reader._raw_awemes) == ["7301", "7302"]
        assert reader._user_info["follower_count"] == 1000

    def test_missing_or_invalid_scripts_are_ignored(self):
        reader = self._reader_with_driver({})

        reader._harvest_dom_payloads()

        assert reader._raw_awemes == {}


class TestDownloaderReferer:
    class _Response:
        status_code = 200
        headers = {"content-length": "4", "content-type": "video/mp4"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=8192):
            yield b"data"

    class _Session:
        headers = {}
        captured = None

        def get(self, _url, **kwargs):
            TestDownloaderReferer._Session.captured = kwargs.get("headers")
            return TestDownloaderReferer._Response()

    def test_post_level_referer_overrides_default(self, tmp_path):
        downloader = Downloader(str(tmp_path), deduplicate=False, session=self._Session())
        post = {
            "aweme_id": "7301",
            "type": "video",
            "video_url": "https://cdn.example.com/v.mp4",
            "create_time": 0,
            "referer": "https://www.tiktok.com/",
        }
        assert downloader.download_post(post) is True
        assert self._Session.captured == {"Referer": "https://www.tiktok.com/"}
