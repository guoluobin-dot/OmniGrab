from douyin_core import BrowserProfileReader, parse_aweme
from douyin_core.downloader import Downloader
from douyin_core.risks import RiskBlockedError, assert_not_risk_response
from unittest.mock import Mock
import tempfile
import time
from threading import Event
from pathlib import Path

from douyin_core.database import DownloadDB

def test_public_imports_and_defaults():
    assert BrowserProfileReader().headless is False
    assert parse_aweme({"aweme_id": "1", "images": [{"url_list": ["low", "high"]}]})["image_urls"] == ["high"]


def test_video_parser_prefers_h264_for_desktop_preview_compatibility():
    post = parse_aweme(
        {
            "aweme_id": "video-1",
            "video": {
                "bit_rate": [
                    {
                        "bit_rate": 8_000,
                        "play_addr": {"url_list": ["https://example.com/hevc.mp4"]},
                    },
                    {
                        "bit_rate": 4_000,
                        "play_addr_h264": {"url_list": ["https://example.com/h264.mp4"]},
                    },
                ]
            },
        }
    )

    assert post["video_url"] == "https://example.com/h264.mp4"

def test_risk_html_is_rejected():
    try: assert_not_risk_response("<html>captcha</html>")
    except RiskBlockedError: return
    assert False

def test_normal_aweme_fields_do_not_trigger_risk_detection():
    assert_not_risk_response({
        "status_code": 0,
        "aweme_list": [{"desc": "请登录后再来看我的作品", "author": {"is_verify": True}}],
        "is_risk": False,
    })

def test_api_error_message_with_captcha_is_rejected():
    try:
        assert_not_risk_response({"status_code": 2483, "status_msg": "请完成验证码"})
    except RiskBlockedError:
        return
    assert False

def test_safe_filename_removes_description_line_breaks_and_windows_controls():
    name = "我发誓！少吃一点碳水都不行。\n但是我想问\t一句：可以吗？"
    safe = Downloader._safe_filename(name)
    assert "\n" not in safe and "\t" not in safe
    assert ":" not in safe and "?" not in safe
    assert safe.startswith("我发誓")

def test_safe_filename_avoids_windows_device_names():
    assert Downloader._safe_filename("CON.txt") == "_CON.txt"

def test_navigation_keeps_visible_page_when_chrome_times_out():
    reader = BrowserProfileReader.__new__(BrowserProfileReader)
    reader.driver = Mock()
    reader.driver.get.side_effect = RuntimeError("renderer timeout")
    reader._navigate("https://www.douyin.com/user/example")
    reader.driver.execute_script.assert_called_once_with("window.stop();")

def test_batch_download_orders_newest_first_and_embeds_publish_rank():
    with tempfile.TemporaryDirectory() as output:
        downloader = Downloader(output, deduplicate=False)
        seen = []
        downloader.download_post = lambda post: seen.append(post) or True
        downloader.download_batch([
            {"aweme_id": "old", "create_time": 1_700_000_000},
            {"aweme_id": "new", "create_time": 1_800_000_000},
        ])
    assert [post["aweme_id"] for post in seen] == ["new", "old"]
    assert seen[0]["_download_order"] == 1
    assert downloader._post_stem(seen[0]).startswith("001_")


def test_missing_media_is_not_treated_as_a_completed_historical_download():
    with tempfile.TemporaryDirectory() as output:
        missing_folder = Path(output) / "images" / "missing-post"
        database = DownloadDB(str(Path(output) / "history.db"))
        database.add_record("missing", "", "image", str(missing_folder))

        assert database.is_downloaded("missing") is False

        missing_folder.mkdir(parents=True)
        (missing_folder / "001.jpg").write_bytes(b"image-data")
        assert database.is_downloaded("missing") is True


def test_completed_batch_includes_human_readable_skip_reason():
    with tempfile.TemporaryDirectory() as output:
        downloader = Downloader(output)
        media_path = Path(output) / "videos" / "already-downloaded.mp4"
        media_path.write_bytes(b"video-data")
        downloader.db.add_record("existing", "example", "video", str(media_path))

        result = downloader.download_batch([{"aweme_id": "existing", "desc": "example"}])

    assert result["skipped"] == 1
    assert result["skipped_details"][0]["reason"] == "本地已有完整文件，已跳过重复下载"


def test_image_posts_use_one_flat_date_named_library():
    with tempfile.TemporaryDirectory() as output:
        downloader = Downloader(output, deduplicate=False)
        first_timestamp = int(time.mktime((2026, 7, 21, 12, 0, 0, 0, 0, -1)))
        second_timestamp = int(time.mktime((2026, 7, 21, 18, 0, 0, 0, 0, -1)))

        def write_image(_url, filepath, _progress_callback=None, headers=None):
            del headers
            Path(filepath).write_bytes(b"image-data")
            return True

        downloader.download_file = write_image
        result = downloader.download_batch([
            {
                "aweme_id": "first",
                "type": "image",
                "create_time": first_timestamp,
                "image_urls": ["https://example.com/one.jpg", "https://example.com/two.jpg"],
            },
            {
                "aweme_id": "second",
                "type": "image",
                "create_time": second_timestamp,
                "image_urls": ["https://example.com/three.png"],
            },
        ])
        image_dir = Path(output) / "images"

        assert result["success"] == 2
        assert sorted(path.name for path in image_dir.iterdir()) == [
            "20260721_1.png",
            "20260721_2.jpg",
            "20260721_3.jpg",
        ]
        assert not any(path.is_dir() for path in image_dir.iterdir())


def test_flat_image_record_requires_every_image_to_still_exist():
    with tempfile.TemporaryDirectory() as output:
        first = Path(output) / "20260721_1.jpg"
        second = Path(output) / "20260721_2.jpg"
        first.write_bytes(b"image-data")
        database = DownloadDB(str(Path(output) / "history.db"))
        database.add_record("image-post", "", "image", [str(first), str(second)])

        assert database.is_downloaded("image-post") is False
        second.write_bytes(b"image-data")
        assert database.is_downloaded("image-post") is True


def test_batch_stops_cleanly_when_cancellation_is_requested():
    with tempfile.TemporaryDirectory() as output:
        cancel_event = Event()
        downloader = Downloader(output, deduplicate=False, cancel_event=cancel_event)
        seen = []

        def download_one(post):
            seen.append(post["aweme_id"])
            cancel_event.set()
            return True

        downloader.download_post = download_one
        result = downloader.download_batch([
            {"aweme_id": "first"},
            {"aweme_id": "second"},
        ])

    assert seen == ["first"]
    assert result == {
        "success": 1,
        "failed": 0,
        "skipped": 0,
        "total": 2,
        "cancelled": True,
        "remaining": 1,
    }


def test_cancelling_a_media_stream_removes_its_temporary_file():
    class Response:
        headers = {"content-length": "10", "content-type": "image/jpeg"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=8192):
            del chunk_size
            yield b"first"
            cancel_event.set()
            yield b"second"

    class Session:
        headers: dict = {}

        def get(self, _url, **_kwargs):
            return Response()

    with tempfile.TemporaryDirectory() as output:
        cancel_event = Event()
        downloader = Downloader(
            output,
            deduplicate=False,
            session=Session(),
            cancel_event=cancel_event,
        )
        target = Path(output) / "cancelled.jpg"

        assert downloader.download_file("https://example.com/cancelled.jpg", str(target)) is False
        assert target.exists() is False
        assert Path(f"{target}.part").exists() is False


def test_cancelling_an_image_set_removes_partial_images_before_resume():
    with tempfile.TemporaryDirectory() as output:
        cancel_event = Event()
        downloader = Downloader(output, deduplicate=False, cancel_event=cancel_event)
        calls = []

        def download_image(_url, filepath, _progress_callback=None, headers=None):
            del headers
            calls.append(filepath)
            Path(filepath).write_bytes(b"image-data")
            if len(calls) == 2:
                cancel_event.set()
                return False
            return True

        downloader.download_file = download_image
        downloaded = downloader.download_image_set(
            {
                "aweme_id": "image-post",
                "type": "image",
                "create_time": 1_784_721_600,
                "image_urls": ["https://example.com/one.jpg", "https://example.com/two.jpg"],
            }
        )

        assert downloaded is False
        assert list((Path(output) / "images").iterdir()) == []


def test_profile_scroll_advances_page_and_content_container():
    reader = BrowserProfileReader.__new__(BrowserProfileReader)
    reader.driver = Mock()

    reader._scroll_profile_once()

    script = reader.driver.execute_script.call_args.args[0]
    assert "window.scrollBy" in script
    assert "WheelEvent" in script
    assert "overflowY" in script
