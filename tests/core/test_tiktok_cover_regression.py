"""TikTok 封面图被误当作视频地址的回归测试。"""
import os

import pytest

from douyin_core.platforms import (
    TikTokAdapter,
    TikTokLoginRequired,
    is_video_media_url,
)
from douyin_core.risks import ParseError


COVER_URL = "https://p16-common-sign.tiktokcdn.com/tos-alisg-p-0037/xyz~tplv-tiktokx-origin.image?dr=14575"

# 真实复现：未登录时 TikTok 返回空的 playAddr，只剩封面图
GATED_ITEM = {
    "id": "7683739482964692242",
    "desc": "#plussizebra",
    "createTime": 1758000000,
    "video": {
        "downloadAddr": "",
        "playAddr": "",
        "cover": COVER_URL,
        "originCover": COVER_URL,
        "dynamicCover": COVER_URL,
        "duration": 15,
        "width": 720,
        "height": 1280,
    },
    "author": {"uniqueId": "artemisiaallure", "nickname": "ArtemisiaAllure"},
}


class TestVideoUrlDetection:
    @pytest.mark.parametrize(
        "url",
        [
            "https://p16-common-sign.tiktokcdn.com/tos-alisg-p-0037/xyz~tplv-tiktokx-origin.image?dr=1",
            "https://p16.tiktokcdn.com/aweme/100x100/x.jpeg",
            "https://p16-sg.tiktokcdn.com/a/b/cover~tplv-cropcenter.webp",
            "https://cdn.example.com/cover.png",
            "https://cdn.example.com/a.GIF",
        ],
    )
    def test_image_urls_are_rejected(self, url):
        assert is_video_media_url(url) is False

    @pytest.mark.parametrize(
        "url",
        [
            "https://v16-webapp.tiktokcdn.com/video/tos/x.mp4",
            "https://v16m-default.tiktokcdn.com/abc/def/video/tos/alisg/x/oYFD.mp4",
            "https://sf16-scmcdn-va.tiktokcdn.com/obj/tos/a.m3u8",
            "https://cdn.example.com/clip.webm?dr=1",
        ],
    )
    def test_video_urls_are_accepted(self, url):
        assert is_video_media_url(url) is True

    def test_non_http_values_are_rejected(self):
        assert is_video_media_url("") is False
        assert is_video_media_url("blob:https://x/y") is False
        assert is_video_media_url(None) is False


class TestGatedPostsNeverProduceCoverUrl:
    def test_cover_is_not_returned_as_video_url(self):
        """核心回归：曾经把 ~tplv-*.image 封面当成视频地址下载成 .mp4。"""
        assert TikTokAdapter()._video_url(GATED_ITEM) == ""

    def test_gated_post_raises_login_required(self):
        with pytest.raises(TikTokLoginRequired) as excinfo:
            TikTokAdapter().parse_item(GATED_ITEM)
        assert "播放地址" in str(excinfo.value)

    def test_gated_error_is_a_parse_error_for_callers(self):
        assert issubclass(TikTokLoginRequired, ParseError)

    def test_post_with_real_play_addr_still_works(self):
        raw = dict(GATED_ITEM)
        raw["video"] = dict(GATED_ITEM["video"], playAddr="https://v16-webapp.tiktokcdn.com/video/tos/ok.mp4")
        post = TikTokAdapter().parse_item(raw)
        assert post["type"] == "video"
        assert post["video_url"].endswith("ok.mp4")


class TestDownloaderRejectsCoverSavedAsVideo:
    class _ImageResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg", "content-length": "9"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=8192):
            yield b"\xff\xd8\xff\xe0jpegdata"

    class _Session:
        headers: dict = {}

        def get(self, _url, **_kwargs):
            return TestDownloaderRejectsCoverSavedAsVideo._ImageResponse()

    def test_jpeg_response_is_not_saved_as_mp4(self, tmp_path):
        from douyin_core.downloader import Downloader

        downloader = Downloader(str(tmp_path), deduplicate=False, session=self._Session())
        target = tmp_path / "videos" / "fake.mp4"
        assert downloader.download_file(COVER_URL, str(target)) is False
        assert not target.exists()
        assert not os.path.exists(f"{target}.part")
        assert "图片" in downloader.last_error

    def test_image_targets_still_download_normally(self, tmp_path):
        """图文作品下载到 .jpg 不应被视频校验误伤。"""
        from douyin_core.downloader import Downloader

        downloader = Downloader(str(tmp_path), deduplicate=False, session=self._Session())
        target = tmp_path / "images" / "cover.jpg"
        assert downloader.download_file(COVER_URL, str(target)) is True
        assert target.exists()


