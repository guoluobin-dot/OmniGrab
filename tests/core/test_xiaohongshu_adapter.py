"""小红书适配器的解析与登录检测单元测试。"""
import pytest

from douyin_core import BrowserProfileReader
from douyin_core.platforms import XiaohongshuAdapter, detect_platform
from douyin_core.risks import ParseError

VIDEO_ITEM = {
    "note_id": "66f1aa2b000000001e02aa11",
    "display_title": "今日穿搭",
    "desc": "今日穿搭分享，附链接",
    "type": "video",
    "time": 1750000000000,
    "cover": {"url_default": "https://sns-img-qc.xhscdn.com/cover.jpg"},
    "video_info_v2": {
        "media": {
            "stream": {
                "h264": [{"master_url": "https://vod.xhscdn.com/v.mp4", "backup_urls": ["https://vod2.xhscdn.com/v.mp4"]}],
                "h265": [{"master_url": "https://vod.xhscdn.com/v_h265.mp4"}],
            }
        }
    },
    "user": {"user_id": "5d1a2b3c000000000101abcd", "nickname": "穿搭博主", "avatar": "https://sns-avatar-qc.xhscdn.com/a.jpg"},
    "interact_info": {"liked_count": 1200, "collected_count": 300, "comment_count": 45, "share_count": 12},
}

IMAGE_ITEM = {
    "note_id": "66f1aa2b000000001e02aa22",
    "display_title": "",
    "desc": "三图探店合集，值得收藏",
    "type": "normal",
    "time": 1750000100000,
    "cover": {"url_default": "https://sns-img-qc.xhscdn.com/c0.jpg"},
    "image_list": [
        {"url_default": "https://sns-img-qc.xhscdn.com/1.jpg", "info_list": [{"url": "https://sns-img-qc.xhscdn.com/1_s.jpg", "width": 540}, {"url": "https://sns-img-qc.xhscdn.com/1_l.jpg", "width": 1080}]},
        {"info_list": [{"url": "https://sns-img-qc.xhscdn.com/2.jpg", "width": 1080}]},
    ],
    "user": {"user_id": "5d1a2b3c000000000101abcd", "nickname": "穿搭博主"},
    "interact_info": {"liked_count": "2.3万", "collected_count": 500, "comment_count": 60, "share_count": 20},
}

USER_POST_PAYLOAD = {
    "code": 0,
    "msg": "success",
    "data": {
        "cursor": "abc",
        "has_more": True,
        "notes": [VIDEO_ITEM, IMAGE_ITEM],
    },
}

FEED_PAYLOAD = {
    "code": 0,
    "data": [
        {"note_card": dict(VIDEO_ITEM)},
        {"note_card": dict(IMAGE_ITEM)},
    ],
}


class TestDetection:
    def test_xiaohongshu_link_selects_implemented_adapter(self):
        adapter = detect_platform("https://www.xiaohongshu.com/user/profile/5d1a2b3c000000000101abcd")
        assert isinstance(adapter, XiaohongshuAdapter)
        assert adapter.implemented is True

    def test_host_matching(self):
        assert XiaohongshuAdapter().matches("https://www.xiaohongshu.com/explore/abc123")
        assert XiaohongshuAdapter().matches("https://xhslink.com/a/b123")
        assert not XiaohongshuAdapter().matches("https://www.douyin.com/user/x")

    def test_profile_response_endpoints(self):
        adapter = XiaohongshuAdapter()
        assert adapter.is_profile_response("https://edith.xiaohongshu.com/api/sns/web/v1/user_post")
        assert adapter.is_profile_response("https://edith.xiaohongshu.com/api/sns/web/v2/user_post")
        assert adapter.is_profile_response("https://edith.xiaohongshu.com/api/sns/web/v1/feed")
        assert not adapter.is_profile_response("https://www.douyin.com/aweme/v1/web/aweme/post/")


class TestExtraction:
    def test_items_from_user_post(self):
        items = XiaohongshuAdapter().extract_items(USER_POST_PAYLOAD)
        assert [i["note_id"] for i in items] == [VIDEO_ITEM["note_id"], IMAGE_ITEM["note_id"]]

    def test_items_from_feed_unwraps_note_card(self):
        items = XiaohongshuAdapter().extract_items(FEED_PAYLOAD)
        assert [i["note_id"] for i in items] == [VIDEO_ITEM["note_id"], IMAGE_ITEM["note_id"]]

    def test_user_info_from_first_note_author(self):
        info = XiaohongshuAdapter().extract_user_info(USER_POST_PAYLOAD)
        assert info["nickname"] == "穿搭博主"
        assert info["uid"] == "5d1a2b3c000000000101abcd"


class TestParseItem:
    def test_video_item_prefers_h264_master_url(self):
        post = XiaohongshuAdapter().parse_item(VIDEO_ITEM)
        assert post["type"] == "video"
        assert post["video_url"] == "https://vod.xhscdn.com/v.mp4"
        assert post["image_urls"] == []
        assert post["aweme_id"] == "66f1aa2b000000001e02aa11"
        assert post["desc"] == "今日穿搭"
        assert post["create_time"] == 1750000000
        assert post["web_url"] == "https://www.xiaohongshu.com/explore/66f1aa2b000000001e02aa11"
        assert post["referer"] == post["web_url"]
        assert post["stats"]["digg_count"] == 1200
        assert post["stats"]["collect_count"] == 300

    def test_image_item_collects_urls_and_parses_wan_counts(self):
        post = XiaohongshuAdapter().parse_item(IMAGE_ITEM)
        assert post["type"] == "image"
        assert post["image_urls"] == ["https://sns-img-qc.xhscdn.com/1.jpg", "https://sns-img-qc.xhscdn.com/2.jpg"]
        assert post["video_url"] == ""
        assert post["stats"]["digg_count"] == 23000

    def test_item_without_media_raises_parse_error(self):
        with pytest.raises(ParseError):
            XiaohongshuAdapter().parse_item({"note_id": "abc", "type": "normal"})

    def test_item_without_note_id_raises_parse_error(self):
        with pytest.raises(ParseError):
            XiaohongshuAdapter().parse_item({"type": "video"})


class TestGateProbe:
    def test_probe_contains_keywords_and_selectors(self):
        script = XiaohongshuAdapter().gate_probe_script()
        assert "扫码登录" in script
        assert "login-modal" in script

    def test_session_cookie_names(self):
        assert "web_session" in XiaohongshuAdapter().login_cookie_names


class TestReaderIngest:
    def test_ingest_deduplicates_by_note_id(self):
        reader = BrowserProfileReader.__new__(BrowserProfileReader)
        reader.platform = XiaohongshuAdapter()
        reader._raw_awemes = {}
        reader._user_info = {}
        reader._ingest_payload(USER_POST_PAYLOAD)
        reader._ingest_payload(dict(USER_POST_PAYLOAD))
        assert sorted(reader._raw_awemes) == sorted([VIDEO_ITEM["note_id"], IMAGE_ITEM["note_id"]])
        assert reader._user_info["nickname"] == "穿搭博主"
