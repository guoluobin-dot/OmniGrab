"""TikTok Shop 适配器的链接识别、响应匹配与解析单元测试。"""
import pytest

from douyin_core.platforms import TikTokAdapter, TikTokShopAdapter, detect_platform


PRODUCT_URL = "https://shop.tiktok.com/us/pdp/trendy-phone-case/1731098552908944370"
VIEW_PRODUCT_URL = "https://shop.tiktok.com/view/product/1731098552908944370?region=BR"
WWW_PDP_URL = "https://www.tiktok.com/shop/pdp/1731098552908944370"
STORE_URL = "https://shop.tiktok.com/us/store/timeless-teapot/7496126292994264050"

# 商家上传的商品视频
SHOP_VIDEO = {
    "video_id": "7301",
    "play_url": "https://sf16-scmcdn-va.tiktokcdn.com/obj/shop/video.mp4",
    "cover_url": "https://sf16-scmcdn-va.tiktokcdn.com/obj/shop/cover.webp",
    "duration": 21,
    "product_url": PRODUCT_URL,
}
# 关联带货视频：标准 itemStruct
RELATED_VIDEO = {
    "id": "7302",
    "desc": "must have",
    "createTime": 1750000000,
    "video": {"playAddr": "https://v16.tiktokcdn.com/related.mp4"},
    "author": {"uniqueId": "seller", "nickname": "Seller"},
    "stats": {"diggCount": 4},
}
PRODUCT_PAYLOAD = {
    "code": 0,
    "data": {
        "product_detail": {
            "product": {
                "product_id": "1731098552908944370",
                "title": "Trendy Phone Case",
                "sold_count": 5801,
            },
            "shop": {"shop_name": "Timeless Teapot"},
            "videos": [SHOP_VIDEO],
            "relatedVideos": [RELATED_VIDEO],
        }
    },
}


class TestLinkDetection:
    def test_shop_links_select_shop_adapter(self):
        for url in (PRODUCT_URL, VIEW_PRODUCT_URL, WWW_PDP_URL, STORE_URL):
            adapter = detect_platform(url)
            assert isinstance(adapter, TikTokShopAdapter), url
            assert adapter.implemented is True

    def test_regular_tiktok_links_still_use_tiktok_adapter(self):
        adapter = detect_platform("https://www.tiktok.com/@artemisiaallure")
        assert isinstance(adapter, TikTokAdapter)
        assert not isinstance(adapter, TikTokShopAdapter)

    def test_other_platforms_are_unaffected(self):
        assert detect_platform("https://www.douyin.com/user/MS4w").name == "douyin"
        assert detect_platform("https://space.bilibili.com/1").name == "bilibili"
        assert detect_platform("https://www.xiaohongshu.com/user/profile/1").name == "xiaohongshu"
        # 未知链接回退到 douyin（ADAPTERS[0]）
        assert detect_platform("https://example.com/x").name == "douyin"

    def test_product_and_store_pages_are_distinguished(self):
        adapter = TikTokShopAdapter()
        assert adapter.is_product_url(PRODUCT_URL) is True
        assert adapter.is_product_url(WWW_PDP_URL) is True
        assert adapter.is_product_url(STORE_URL) is False
        # 商品页无需滚动，店铺页需要滚动
        assert adapter.is_single_item_url(PRODUCT_URL) is True
        assert adapter.is_single_item_url(STORE_URL) is False


class TestResponseMatching:
    def test_shop_api_hosts_are_collected(self):
        adapter = TikTokShopAdapter()
        assert adapter.is_profile_response("https://oec.tiktok.com/api/ec/product/detail?product_id=1")
        assert adapter.is_profile_response("https://ec.tiktok.com/api/shop/product/123")
        assert adapter.is_profile_response("https://www.tiktok.com/api/product/detail?id=1")

    def test_unrelated_hosts_are_ignored(self):
        adapter = TikTokShopAdapter()
        assert not adapter.is_profile_response("https://www.douyin.com/aweme/v1/web/aweme/post/")
        assert not adapter.is_profile_response("https://cdn.example.com/app.js")


class TestExtraction:
    def test_product_videos_and_related_videos_are_extracted(self):
        items = TikTokShopAdapter().extract_items(PRODUCT_PAYLOAD)
        ids = {item.get("video_id") or item.get("id") for item in items}
        assert ids == {"7301", "7302"}

    def test_product_meta_becomes_user_info(self):
        info = TikTokShopAdapter().extract_user_info(PRODUCT_PAYLOAD)
        assert "Trendy Phone Case" in info["nickname"]
        assert "Timeless Teapot" in info["nickname"]
        assert info["uid"] == "1731098552908944370"

    def test_empty_payload_yields_nothing(self):
        adapter = TikTokShopAdapter()
        assert adapter.extract_items({"data": {}}) == []
        assert adapter.extract_items("") == []


class TestParseItem:
    def test_shop_video_normalises_to_post_schema(self):
        post = TikTokShopAdapter().parse_item(SHOP_VIDEO)
        assert post["aweme_id"] == "7301"
        assert post["type"] == "video"
        assert post["type_str"] == "Shop视频"
        assert post["video_url"] == "https://sf16-scmcdn-va.tiktokcdn.com/obj/shop/video.mp4"
        assert post["cover"] == "https://sf16-scmcdn-va.tiktokcdn.com/obj/shop/cover.webp"
        assert post["duration"] == 21
        # Referer 必须指向商品页，Shop CDN 才会放行
        assert post["referer"] == PRODUCT_URL

    def test_related_shop_video_reuses_tiktok_parsing(self):
        post = TikTokShopAdapter().parse_item(RELATED_VIDEO)
        assert post["aweme_id"] == "7302"
        assert post["video_url"] == "https://v16.tiktokcdn.com/related.mp4"
        assert post["web_url"] == "https://www.tiktok.com/@seller/video/7302"

    def test_entry_without_media_raises_parse_error(self):
        from douyin_core.risks import ParseError

        with pytest.raises(ParseError):
            TikTokShopAdapter().parse_item({"video_id": "7303"})

    def test_dom_only_entries_get_stable_synthetic_ids(self):
        entry = {"url": "https://cdn.example.com/a.mp4", "cover_url": "", "product_url": PRODUCT_URL}
        adapter = TikTokShopAdapter()
        assert adapter.item_id(entry) == adapter.item_id(dict(entry))
        assert adapter.item_id(entry).startswith("shop_")
        post = adapter.parse_item(entry)
        assert post["video_url"] == "https://cdn.example.com/a.mp4"


class TestDomMediaProbe:
    def test_probe_skips_blob_sources(self):
        script = TikTokShopAdapter().dom_media_script()
        assert "blob:" in script
        assert "product_url: location.href" in script

    def test_tiktok_profile_has_no_dom_media_probe(self):
        assert TikTokAdapter().dom_media_script() == ""


class TestTikTokShopAwareVideoUrl:
    def test_shop_post_without_video_key_is_still_parsed(self):
        raw = {
            "id": "7310",
            "desc": "shop post",
            "createTime": 1750000500,
            "playAddr": "https://v16.tiktokcdn.com/shop-anchored.mp4",
            "author": {"uniqueId": "u", "nickname": "N"},
        }
        post = TikTokAdapter().parse_item(raw)
        assert post["video_url"] == "https://v16.tiktokcdn.com/shop-anchored.mp4"
        assert post["referer"] == "https://www.tiktok.com/@u/video/7310"


class TestNormalizeUrl:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("shop.tiktok.com/us/pdp/x/123", "https://shop.tiktok.com/us/pdp/x/123"),
            ("httpsshop.tiktok.com/us/pdp/x/123", "https://shop.tiktok.com/us/pdp/x/123"),
        ],
    )
    def test_shop_links_are_repaired(self, raw, expected):
        from douyin_core.platforms import normalize_profile_url

        assert normalize_profile_url(raw) == expected


class TestDownloaderShopHeaders:
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
            TestDownloaderShopHeaders._Session.captured = kwargs.get("headers")
            return TestDownloaderShopHeaders._Response()

    def test_shop_referer_adds_origin(self, tmp_path):
        from douyin_core.downloader import Downloader

        downloader = Downloader(str(tmp_path), deduplicate=False, session=self._Session())
        post = {
            "aweme_id": "7301",
            "type": "video",
            "video_url": "https://cdn.example.com/v.mp4",
            "create_time": 0,
            "referer": PRODUCT_URL,
        }
        assert downloader.download_post(post) is True
        assert self._Session.captured == {"Referer": PRODUCT_URL, "Origin": "https://shop.tiktok.com"}

    def test_plain_tiktok_referer_has_no_origin(self, tmp_path):
        from douyin_core.downloader import Downloader

        downloader = Downloader(str(tmp_path), deduplicate=False, session=self._Session())
        post = {
            "aweme_id": "7302",
            "type": "video",
            "video_url": "https://cdn.example.com/v2.mp4",
            "create_time": 0,
            "referer": "https://www.tiktok.com/@u/video/7302",
        }
        assert downloader.download_post(post) is True
        assert self._Session.captured == {"Referer": "https://www.tiktok.com/@u/video/7302"}
