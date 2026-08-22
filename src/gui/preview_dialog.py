"""作品预览对话框。

图文作品在窗口内异步加载图片；视频作品使用 QtMultimedia 提供播放控件，
并始终保留“在浏览器打开”回退按钮，以兼容本机缺少视频解码器的情况。
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

from PyQt5.QtCore import QUrl, Qt, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QPixmap
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
    from PyQt5.QtMultimediaWidgets import QVideoWidget

    MEDIA_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于本机 PyQt5 安装
    MEDIA_AVAILABLE = False


class PreviewDialog(QDialog):
    """展示单个视频或图文作品，并可请求下载该作品。"""

    download_requested = pyqtSignal(dict)

    def __init__(
        self,
        post: dict,
        parent: Optional[QWidget] = None,
        *,
        cookie: str = "",
        user_agent: str = "",
    ) -> None:
        super().__init__(parent)
        self.post = post
        self.cookie = cookie
        self.user_agent = user_agent
        self.player = None
        self._pending_replies = set()
        self._video_reply = None
        self._video_file = None
        self._video_file_path = ""
        self.network = QNetworkAccessManager(self)

        self.setWindowTitle(f"作品预览 - {post.get('type_str', '作品')}")
        self.setMinimumSize(980, 720)
        self.resize(1160, 860)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel(self.post.get("desc") or "无标题")
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 16px; font-weight: 600; padding: 4px 0;")
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._build_media_tab(), "内容预览")
        tabs.addTab(self._build_info_tab(), "作品信息")
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        download_button = buttons.addButton("下载此作品", QDialogButtonBox.ActionRole)
        open_button = buttons.addButton("在浏览器打开", QDialogButtonBox.ActionRole)
        download_button.clicked.connect(lambda: self.download_requested.emit(self.post))
        open_button.clicked.connect(self._open_in_browser)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_media_tab(self) -> QWidget:
        if self.post.get("type") == "image":
            return self._build_image_preview()
        return self._build_video_preview()

    def _build_image_preview(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignTop)

        image_urls = self.post.get("image_urls", [])
        if not image_urls:
            layout.addWidget(QLabel("没有可预览的图片链接。"))
        for index, url in enumerate(image_urls, start=1):
            label = QLabel(f"正在加载第 {index} 张图片…")
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(180)
            label.setStyleSheet("border: 1px solid #ddd; background: #fafafa; margin: 4px;")
            layout.addWidget(label)
            self._load_image(url, label, max_width=1040)

        scroll.setWidget(content)
        return scroll

    def _build_video_preview(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        video_url = self.post.get("video_url", "")

        if MEDIA_AVAILABLE and video_url:
            self.video_widget = QVideoWidget()
            self.video_widget.setMinimumHeight(560)
            self.video_widget.setStyleSheet("background: #111;")
            layout.addWidget(self.video_widget, 1)

            self.video_status = QLabel("正在加载视频预览…")
            self.video_status.setAlignment(Qt.AlignCenter)
            self.video_status.setWordWrap(True)
            layout.addWidget(self.video_status)

            self.player = QMediaPlayer(self, QMediaPlayer.VideoSurface)
            self.player.setVideoOutput(self.video_widget)
            self.player.error.connect(self._on_player_error)

            controls = QHBoxLayout()
            self.play_button = QPushButton("播放")
            self.play_button.setEnabled(False)
            self.play_button.clicked.connect(self._toggle_playback)
            controls.addWidget(self.play_button)
            controls.addStretch()
            layout.addLayout(controls)
            self._load_video_preview(video_url)
        else:
            cover = QLabel("当前环境无法直接播放该视频，将显示封面并支持在浏览器中打开。")
            cover.setAlignment(Qt.AlignCenter)
            cover.setWordWrap(True)
            cover.setMinimumHeight(360)
            cover.setStyleSheet("border: 1px solid #ddd; background: #111; color: #eee;")
            layout.addWidget(cover, 1)
            if self.post.get("cover"):
                self._load_image(self.post["cover"], cover, max_width=680)

        if not video_url:
            layout.addWidget(QLabel("未找到可播放的视频地址。"))
        return widget

    def _load_video_preview(self, video_url: str) -> None:
        """Fetch a protected CDN video with the browser session headers first."""
        try:
            descriptor, self._video_file_path = tempfile.mkstemp(
                prefix="douyin-preview-", suffix=".mp4"
            )
            self._video_file = os.fdopen(descriptor, "wb")
        except OSError as exc:
            self._set_video_error(f"无法创建视频预览临时文件：{exc}")
            return

        request = QNetworkRequest(QUrl(video_url))
        request.setRawHeader(b"Referer", b"https://www.douyin.com/")
        if self.user_agent:
            request.setRawHeader(b"User-Agent", self.user_agent.encode("utf-8"))
        if self.cookie:
            request.setRawHeader(b"Cookie", self.cookie.encode("utf-8"))

        self._video_reply = self.network.get(request)
        self._video_reply.readyRead.connect(self._write_video_chunk)
        self._video_reply.downloadProgress.connect(self._update_video_progress)
        self._video_reply.finished.connect(self._finish_video_download)

    def _write_video_chunk(self) -> None:
        if self._video_reply and self._video_file:
            self._video_file.write(bytes(self._video_reply.readAll()))

    def _update_video_progress(self, received: int, total: int) -> None:
        if total > 0:
            percent = max(0, min(100, round(received * 100 / total)))
            self.video_status.setText(f"正在加载视频预览：{percent}%")
        else:
            self.video_status.setText("正在加载视频预览…")

    def _finish_video_download(self) -> None:
        reply = self._video_reply
        if not reply:
            return
        if self._video_file:
            self._video_file.write(bytes(reply.readAll()))
        self._video_reply = None
        if self._video_file:
            self._video_file.close()
            self._video_file = None

        if reply.error() or not self._video_file_path or not os.path.getsize(self._video_file_path):
            message = reply.errorString() if reply.error() else "视频响应为空"
            self._set_video_error(f"视频加载失败：{message}")
            reply.deleteLater()
            return

        self.player.setMedia(QMediaContent(QUrl.fromLocalFile(self._video_file_path)))
        self.player.play()
        self.play_button.setEnabled(True)
        self.play_button.setText("暂停")
        self.video_status.setText("视频预览已就绪")
        reply.deleteLater()

    def _on_player_error(self, _error) -> None:
        if self.player:
            self._set_video_error(
                f"本机视频播放器无法解码：{self.player.errorString()}。可点击“在浏览器打开”观看。"
            )

    def _set_video_error(self, message: str) -> None:
        if hasattr(self, "video_status"):
            self.video_status.setText(message)
        if hasattr(self, "play_button"):
            self.play_button.setEnabled(False)

    def _cleanup_video_preview(self) -> None:
        if self._video_reply:
            self._video_reply.abort()
            self._video_reply.deleteLater()
            self._video_reply = None
        if self._video_file:
            self._video_file.close()
            self._video_file = None
        if self.player:
            self.player.stop()
        if self._video_file_path:
            try:
                os.remove(self._video_file_path)
            except OSError:
                pass
            self._video_file_path = ""

    def done(self, result: int) -> None:
        self._cleanup_video_preview()
        super().done(result)

    def _build_info_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        stats = self.post.get("stats", {})
        author = self.post.get("author", {})
        form.addRow("类型", QLabel(self.post.get("type_str", "未知")))
        form.addRow("发布时间", QLabel(self.post.get("create_time_str", "未知")))
        form.addRow("作者", QLabel(author.get("nickname", "未知")))
        form.addRow("点赞", QLabel(str(stats.get("digg_count", 0))))
        form.addRow("评论", QLabel(str(stats.get("comment_count", 0))))
        form.addRow("分享", QLabel(str(stats.get("share_count", 0))))
        form.addRow("作品 ID", QLabel(self.post.get("aweme_id", "")))
        return widget

    def _load_image(self, url: str, label: QLabel, *, max_width: int) -> None:
        if not url:
            label.setText("没有可用图片地址")
            return
        reply = self.network.get(QNetworkRequest(QUrl(url)))
        self._pending_replies.add(reply)

        def finished() -> None:
            self._pending_replies.discard(reply)
            if reply.error():
                label.setText(f"图片加载失败：{reply.errorString()}")
                reply.deleteLater()
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(bytes(reply.readAll())):
                label.setText("图片数据无法解析")
                reply.deleteLater()
                return
            label.setPixmap(pixmap.scaledToWidth(max_width, Qt.SmoothTransformation))
            label.setMinimumHeight(0)
            reply.deleteLater()

        reply.finished.connect(finished)

    def _toggle_playback(self) -> None:
        if not self.player:
            return
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_button.setText("播放")
        else:
            self.player.play()
            self.play_button.setText("暂停")

    def _open_in_browser(self) -> None:
        url = self.post.get("web_url") or self.post.get("video_url")
        if url:
            QDesktopServices.openUrl(QUrl(url))
