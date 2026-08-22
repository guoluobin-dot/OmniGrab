"""主窗口的离屏冒烟测试。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from src.gui.main_window import MainWindow
from src.gui.preview_dialog import PreviewDialog


class TestMainWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()

    def test_read_and_preview_controls_are_present(self):
        self.assertEqual(self.window.read_button.text(), "读取作品")
        self.assertEqual(self.window.table.columnCount(), 7)
        self.assertFalse(hasattr(self.window, "cookie_input"))
        self.assertEqual(self.window.save_cookie_button.text(), "保存 Cookie")

        self.window.posts = [{
            "aweme_id": "demo-1",
            "type": "video",
            "type_str": "视频",
            "desc": "演示作品",
            "create_time_str": "2026-07-26 00:00:00",
            "stats": {},
            "video_url": "",
        }]
        self.window._populate_posts()

        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(self.window.table.cellWidget(0, 6).text(), "预览")

    def test_download_completion_details_include_skip_reason(self):
        lines = MainWindow._format_download_details(
            {
                "skipped_details": [
                    {"reason": "本地已有完整文件，已跳过重复下载"},
                    {"reason": "本地已有完整文件，已跳过重复下载"},
                ]
            }
        )

        self.assertEqual(lines, ["跳过原因：本地已有完整文件，已跳过重复下载（2 个）"])

    def test_cancel_download_button_is_available_and_initially_disabled(self):
        self.assertEqual(self.window.cancel_download_button.text(), "中断下载")
        self.assertFalse(self.window.cancel_download_button.isEnabled())
        self.assertEqual(self.window.resume_download_button.text(), "继续下载")
        self.assertFalse(self.window.resume_download_button.isEnabled())

    def test_preview_dialog_has_a_larger_desktop_minimum_size(self):
        dialog = PreviewDialog({"type": "image", "type_str": "图文", "image_urls": []})
        self.assertGreaterEqual(dialog.minimumWidth(), 980)
        self.assertGreaterEqual(dialog.minimumHeight(), 720)
        dialog.close()

    def test_type_download_buttons_filter_the_current_post_list(self):
        self.window.posts = [
            {"aweme_id": "image-1", "type": "image"},
            {"aweme_id": "video-1", "type": "video"},
            {"aweme_id": "image-2", "type": "image"},
        ]
        started = []
        self.window.start_download = lambda posts: started.append(posts)

        self.window.download_all_images()
        self.window.download_all_videos()

        self.assertEqual(
            [[post["aweme_id"] for post in posts] for posts in started],
            [["image-1", "image-2"], ["video-1"]],
        )


if __name__ == "__main__":
    unittest.main()
