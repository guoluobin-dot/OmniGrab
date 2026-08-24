"""抖音内容下载工具的桌面主窗口。"""

from __future__ import annotations

import os
from threading import Event
from typing import Optional

from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from douyin_core import AutoPipeline, Downloader
from douyin_core.refresh import refresh_posts
from src.gui.app_settings import load_download_dir, save_download_dir
from src.gui.preview_dialog import PreviewDialog
from douyin_core import get_cookie_guide, load_cookie, save_cookie
from douyin_core.logger import get_logger


logger = get_logger(__name__)


class ReadPostsThread(QThread):
    """在后台浏览器会话中读取作品，避免阻塞桌面界面。"""

    status = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    login_required = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        cookie: str,
        max_posts: int,
        show_browser: bool,
    ) -> None:
        super().__init__()
        self.url = url
        self.cookie = cookie
        self.max_posts = max_posts
        self.show_browser = show_browser
        self.continue_event = Event()
        self.cancel_event = Event()

    def confirm_login(self) -> None:
        """用户在界面上点击“我已登录，继续”后调用。"""
        self.continue_event.set()

    def cancel_read(self) -> None:
        """用户放弃等待登录后调用；浏览器会被安全关闭。"""
        self.cancel_event.set()

    def run(self) -> None:
        try:
            pipeline = AutoPipeline(headless=not self.show_browser)
            result = pipeline.run(
                self.url,
                max_posts=self.max_posts,
                auto_download=False,
                cookie=self.cookie,
                status_callback=self.status.emit,
                progress_callback=self.progress.emit,
                continue_event=self.continue_event,
                cancel_event=self.cancel_event,
                login_wait_callback=lambda: self.login_required.emit(
                    "请在弹出的浏览器窗口中完成登录或验证"
                ),
            )
            if result.get("success"):
                self.completed.emit(result)
            else:
                self.failed.emit(result.get("error") or "读取作品失败")
        except Exception as exc:
            logger.exception("读取线程异常")
            self.failed.emit(f"读取作品异常: {exc}")


class DownloadThread(QThread):
    """下载选中作品的后台线程。"""

    progress = pyqtSignal(int, int, int, bool)
    completed = pyqtSignal(object)

    def __init__(
        self,
        posts: list,
        download_dir: str,
        cookie: str = "",
        user_agent: str = "",
    ) -> None:
        super().__init__()
        self.posts = posts
        self.download_dir = download_dir
        self.cookie = cookie
        self.user_agent = user_agent
        self.cancel_event = Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            downloader = Downloader(
                self.download_dir,
                cookie=self.cookie,
                headers={"User-Agent": self.user_agent} if self.user_agent else None,
                cancel_event=self.cancel_event,
            )
            result = downloader.download_batch(self.posts, progress_callback=self.progress.emit)
            result = self._retry_expired(downloader, result)
        except Exception as exc:
            logger.exception("下载线程异常")
            result = {
                "success": 0,
                "failed": len(self.posts),
                "skipped": 0,
                "total": len(self.posts),
                "error": str(exc),
            }
        self.completed.emit(result)

    def _retry_expired(self, downloader: Downloader, result: dict) -> dict:
        """TikTok 链接几分钟后过期：403 时自动重读主页换新链接并重试一次。"""
        failed_posts = self._posts_failed_with_expired_links(result)
        if not failed_posts or self.cancel_event.is_set():
            return result
        self.progress.emit(0, 0, "媒体链接已过期，正在自动刷新后重试…")
        fresh_by_id = refresh_posts(failed_posts)
        if self.cancel_event.is_set():
            return result
        retried = [fresh_by_id[p.get("aweme_id")] for p in failed_posts if p.get("aweme_id") in fresh_by_id]
        if not retried:
            return result
        retry_result = downloader.download_batch(retried, progress_callback=self.progress.emit)
        return self._merge_results(result, retry_result, replaced=len(retried))

    def _posts_failed_with_expired_links(self, result: dict) -> list:
        if not result.get("failed"):
            return []
        failed_ids = {
            str(detail.get("aweme_id"))
            for detail in result.get("failed_details", [])
            if "过期" in str(detail.get("reason", "")) or "403" in str(detail.get("reason", ""))
        }
        return [post for post in self.posts if str(post.get("aweme_id")) in failed_ids]

    def _merge_results(self, result: dict, retry_result: dict, replaced: int) -> dict:
        merged = dict(result)
        merged["success"] = result.get("success", 0) + retry_result.get("success", 0)
        merged["failed"] = retry_result.get("failed", 0)
        merged["failed_details"] = retry_result.get("failed_details", [])
        merged["refreshed"] = replaced
        merged["refresh_summary"] = (
            f"已自动刷新 {replaced} 个过期链接后重试："
            f"成功 {merged['success']}，失败 {merged['failed']}"
        )
        return merged


class MainWindow(QMainWindow):
    """提供作品读取、窗口内预览、选择下载和批量下载的主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.posts: list[dict] = []
        self.user_info: dict = {}
        self.request_cookie = ""
        self.request_user_agent = ""
        self.manual_cookie = load_cookie() or ""
        self.read_thread: Optional[ReadPostsThread] = None
        self.download_thread: Optional[DownloadThread] = None
        self._active_download_posts: list[dict] = []
        self._resume_posts: list[dict] = []
        self.download_dir = load_download_dir()

        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("抖音内容下载工具")
        self.setMinimumSize(1020, 720)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)

        layout.addWidget(self._build_read_group())
        layout.addWidget(self._build_login_banner())

        self.user_info_label = QLabel("博主信息：粘贴主页链接后点击“读取作品”")
        self.user_info_label.setStyleSheet("color: #555; padding: 4px; font-size: 13px;")
        self.user_info_label.setWordWrap(True)
        layout.addWidget(self.user_info_label)

        layout.addWidget(self._build_post_group(), 3)
        layout.addWidget(self._build_progress_group())

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(125)
        self.log_text.setStyleSheet(
            "background-color: #1e1e1e; color: #d4d4d4; font-family: Consolas;"
        )
        layout.addWidget(self.log_text)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("就绪")

    def _build_read_group(self) -> QGroupBox:
        group = QGroupBox("博主主页链接")
        layout = QVBoxLayout(group)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "粘贴抖音或 TikTok 博主主页链接，例如 https://www.douyin.com/user/MS4w… 或 https://www.tiktok.com/@user"
        )
        self.url_input.returnPressed.connect(self.start_read)
        action_column = QVBoxLayout()
        self.read_button = QPushButton("读取作品")
        self.read_button.setDefault(True)
        self.read_button.clicked.connect(self.start_read)
        action_column.addWidget(self.read_button)
        cookie_actions = QHBoxLayout()
        self.save_cookie_button = QPushButton("保存 Cookie")
        self.save_cookie_button.setToolTip("需要手动 Cookie 时点击后再粘贴；正常使用无需设置")
        self.save_cookie_button.clicked.connect(self.save_cookie)
        guide_button = QPushButton("获取指南")
        guide_button.clicked.connect(self.show_cookie_guide)
        cookie_actions.addWidget(self.save_cookie_button)
        cookie_actions.addWidget(guide_button)
        action_column.addLayout(cookie_actions)
        url_row.addWidget(self.url_input, 5)
        url_row.addLayout(action_column, 1)
        layout.addLayout(url_row)

        options = QHBoxLayout()
        options.addWidget(QLabel("最多读取："))
        self.max_posts_spin = QSpinBox()
        self.max_posts_spin.setRange(0, 9999)
        self.max_posts_spin.setValue(0)
        self.max_posts_spin.setSpecialValueText("全部")
        self.max_posts_spin.setToolTip("0 表示持续读取到页面没有更多公开作品")
        options.addWidget(self.max_posts_spin)
        options.addWidget(QLabel("(0 = 全部公开作品)"))
        self.show_browser_checkbox = QCheckBox("显示浏览器窗口（推荐；首次登录一次即可）")
        self.show_browser_checkbox.setChecked(True)
        self.show_browser_checkbox.setToolTip(
            "抖音需要验证码或登录时，可直接在打开的浏览器中完成验证；会话会自动保存在本机。"
        )
        options.addWidget(self.show_browser_checkbox)
        options.addStretch()
        layout.addLayout(options)
        return group

    def _build_login_banner(self) -> QFrame:
        """等待人工登录时显示的非阻塞横幅。"""
        self.login_banner = QFrame()
        self.login_banner.setStyleSheet(
            "QFrame { background-color: #fff7e0; border: 1px solid #f0c36d; border-radius: 4px; }"
        )
        banner_layout = QHBoxLayout(self.login_banner)
        banner_layout.setContentsMargins(10, 6, 10, 6)
        self.login_banner_label = QLabel("请在浏览器窗口中完成登录或验证，完成后将自动继续")
        self.login_banner_label.setStyleSheet("border: none; color: #8a6100;")
        banner_layout.addWidget(self.login_banner_label, 1)
        self.login_continue_button = QPushButton("我已登录，继续")
        self.login_continue_button.setStyleSheet("border: none;")
        self.login_continue_button.clicked.connect(self.confirm_login)
        self.login_cancel_button = QPushButton("取消读取")
        self.login_cancel_button.setStyleSheet("border: none;")
        self.login_cancel_button.clicked.connect(self.cancel_read)
        banner_layout.addWidget(self.login_continue_button)
        banner_layout.addWidget(self.login_cancel_button)
        self.login_banner.hide()
        return self.login_banner

    def show_login_banner(self, message: str) -> None:
        self.login_banner_label.setText(f"{message}；完成后将自动继续，也可点击“我已登录，继续”")
        self.login_banner.show()
        self.progress_bar.setRange(0, 0)
        self.progress_label.setText("等待人工登录或验证…")
        self.statusBar().showMessage("等待人工登录或验证")
        self.log(message)

    def hide_login_banner(self) -> None:
        self.login_banner.hide()

    def confirm_login(self) -> None:
        if not (self.read_thread and self.read_thread.isRunning()):
            return
        self.hide_login_banner()
        self.progress_label.setText("正在重新读取作品…")
        self.log("已确认登录，正在继续读取")
        self.read_thread.confirm_login()

    def cancel_read(self) -> None:
        if not (self.read_thread and self.read_thread.isRunning()):
            return
        self.hide_login_banner()
        self.progress_label.setText("正在取消读取…")
        self.log("已取消登录等待；浏览器将关闭")
        self.read_thread.cancel_read()

    def _build_post_group(self) -> QGroupBox:
        group = QGroupBox("作品列表")
        layout = QVBoxLayout(group)

        buttons = QHBoxLayout()
        preview_button = QPushButton("预览当前")
        preview_button.clicked.connect(self.preview_current)
        download_images_button = QPushButton("下载所有图文")
        download_images_button.clicked.connect(self.download_all_images)
        download_selected_button = QPushButton("下载选中")
        download_selected_button.clicked.connect(self.download_selected)
        download_videos_button = QPushButton("下载所有视频")
        download_videos_button.clicked.connect(self.download_all_videos)
        self.cancel_download_button = QPushButton("中断下载")
        self.cancel_download_button.setEnabled(False)
        self.cancel_download_button.setToolTip("会停止后续下载；正在写入的当前文件会在安全结束后删除临时文件")
        self.cancel_download_button.clicked.connect(self.cancel_download)
        self.resume_download_button = QPushButton("继续下载")
        self.resume_download_button.setEnabled(False)
        self.resume_download_button.setToolTip("继续未完成的下载；已完成作品会自动跳过")
        self.resume_download_button.clicked.connect(self.resume_download)
        select_all_button = QPushButton("全选")
        select_all_button.clicked.connect(self.select_all)
        deselect_all_button = QPushButton("取消全选")
        deselect_all_button.clicked.connect(self.deselect_all)
        change_dir_button = QPushButton("设置保存目录")
        change_dir_button.setToolTip("选择后会自动保存，下次启动时继续使用")
        change_dir_button.clicked.connect(self.change_download_dir)
        for button in (
            preview_button,
            download_images_button,
            download_selected_button,
            download_videos_button,
            self.cancel_download_button,
            self.resume_download_button,
            select_all_button,
            deselect_all_button,
            change_dir_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.download_dir_label = QLabel(f"保存目录：{self.download_dir}")
        self.download_dir_label.setStyleSheet("color: #777; font-size: 11px;")
        layout.addWidget(self.download_dir_label)
        image_naming_label = QLabel(
            "图文图片会统一保存到 images 文件夹，命名为 YYYYMMDD_序号（例如 20260721_1.jpg）"
        )
        image_naming_label.setStyleSheet("color: #777; font-size: 11px;")
        layout.addWidget(image_naming_label)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["选择", "序号", "类型", "标题", "发布时间", "互动数据", "预览"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda row, _column: self.preview_post(row))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        for column in (0, 1, 2, 4, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        return group

    def _build_progress_group(self) -> QGroupBox:
        group = QGroupBox("进度")
        layout = QVBoxLayout(group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("就绪")
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_label)
        return group

    def save_cookie(self) -> None:
        cookie, accepted = QInputDialog.getText(
            self,
            "保存 Cookie",
            "粘贴抖音网页版 Cookie：",
            QLineEdit.Password,
            self.manual_cookie,
        )
        if not accepted:
            return
        cookie = cookie.strip()
        if not cookie:
            QMessageBox.warning(self, "提示", "请先输入 Cookie")
            return
        if save_cookie(cookie):
            self.manual_cookie = cookie
            self.log("Cookie 已安全保存到本机配置文件")
            QMessageBox.information(self, "成功", "Cookie 已保存到本机")
        else:
            QMessageBox.critical(self, "错误", "Cookie 保存失败")

    def show_cookie_guide(self) -> None:
        QMessageBox.information(self, "Cookie 获取指南", get_cookie_guide())

    def start_read(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请先粘贴抖音博主主页链接")
            return
        if self.read_thread and self.read_thread.isRunning():
            QMessageBox.information(self, "提示", "正在读取作品，请稍候")
            return

        self.read_button.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_label.setText("正在启动浏览器读取作品…")
        self.log(f"开始读取：{url}")

        self.read_thread = ReadPostsThread(
            url=url,
            cookie=self.manual_cookie,
            max_posts=self.max_posts_spin.value(),
            show_browser=self.show_browser_checkbox.isChecked(),
        )
        self.read_thread.status.connect(self.log)
        self.read_thread.progress.connect(self.on_read_progress)
        self.read_thread.completed.connect(self.on_read_completed)
        self.read_thread.failed.connect(self.on_read_failed)
        self.read_thread.login_required.connect(self.show_login_banner)
        self.read_thread.start()

    def on_read_progress(self, current: int, total: int, message: str) -> None:
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(min(current, total))
        self.progress_label.setText(message)
        self.statusBar().showMessage(message)

    def on_read_completed(self, result: dict) -> None:
        self.read_button.setEnabled(True)
        self.hide_login_banner()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.posts = result.get("posts", [])
        self.user_info = result.get("user_info", {})
        self.request_cookie = result.get("cookie", "")
        self.request_user_agent = result.get("user_agent", "")

        self._populate_posts()
        nickname = self.user_info.get("nickname", "未知")
        self.user_info_label.setText(
            f"博主：{nickname}  |  作品总数：{self.user_info.get('aweme_count', 0)}"
            f"  |  本次读取：{len(self.posts)} 个作品"
        )
        summary = f"读取完成，共 {len(self.posts)} 个作品；可双击列表行进行预览。"
        self.progress_label.setText(summary)
        self.log(summary)
        self.statusBar().showMessage(summary)

    def on_read_failed(self, message: str) -> None:
        self.read_button.setEnabled(True)
        self.hide_login_banner()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"读取失败：{message}")
        self.log(f"读取失败：{message}")
        self.statusBar().showMessage("读取失败")
        QMessageBox.critical(self, "读取作品失败", message)

    def _populate_posts(self) -> None:
        self.table.setRowCount(0)
        for index, post in enumerate(self.posts):
            self.table.insertRow(index)
            self.table.setCellWidget(index, 0, self._create_checkbox())
            self.table.setItem(index, 1, QTableWidgetItem(str(index + 1)))
            self.table.setItem(index, 2, QTableWidgetItem(post.get("type_str", "作品")))
            self.table.setItem(index, 3, QTableWidgetItem((post.get("desc") or "无标题")[:70]))
            self.table.setItem(index, 4, QTableWidgetItem(post.get("create_time_str", "")))
            stats = post.get("stats", {})
            self.table.setItem(
                index,
                5,
                QTableWidgetItem(
                    f"❤ {stats.get('digg_count', 0)}  "
                    f"💬 {stats.get('comment_count', 0)}  "
                    f"↗ {stats.get('share_count', 0)}"
                ),
            )
            preview_button = QPushButton("预览")
            preview_button.clicked.connect(lambda _checked=False, row=index: self.preview_post(row))
            self.table.setCellWidget(index, 6, preview_button)

    def preview_current(self) -> None:
        self.preview_post(self.table.currentRow())

    def preview_post(self, row: int) -> None:
        if row < 0 or row >= len(self.posts):
            QMessageBox.warning(self, "提示", "请先在作品列表中选择一项")
            return
        dialog = PreviewDialog(
            self.posts[row],
            self,
            cookie=self.request_cookie or self.manual_cookie,
            user_agent=self.request_user_agent,
        )
        dialog.download_requested.connect(lambda post: self.start_download([post]))
        dialog.exec_()

    def download_all_images(self) -> None:
        self._download_posts_of_type("image", "图文")

    def download_all_videos(self) -> None:
        self._download_posts_of_type("video", "视频")

    def _download_posts_of_type(self, post_type: str, label: str) -> None:
        posts = [post for post in self.posts if post.get("type") == post_type]
        if not posts:
            QMessageBox.information(self, "提示", f"当前列表没有可下载的{label}作品")
            return
        self.start_download(posts)

    def select_all(self) -> None:
        for row in range(self.table.rowCount()):
            checkbox = self.table.cellWidget(row, 0)
            if checkbox:
                checkbox.setChecked(True)

    def deselect_all(self) -> None:
        for row in range(self.table.rowCount()):
            checkbox = self.table.cellWidget(row, 0)
            if checkbox:
                checkbox.setChecked(False)

    def get_selected_posts(self) -> list:
        selected = []
        for row, post in enumerate(self.posts):
            checkbox = self.table.cellWidget(row, 0)
            if checkbox and checkbox.isChecked():
                selected.append(post)
        return selected

    def download_selected(self) -> None:
        selected = self.get_selected_posts()
        if not selected:
            QMessageBox.warning(self, "提示", "请先勾选要下载的作品")
            return
        self.start_download(selected)

    def start_download(self, posts: list, *, is_resume: bool = False) -> None:
        if not posts:
            QMessageBox.warning(self, "提示", "没有可下载的作品")
            return
        if self.download_thread and self.download_thread.isRunning():
            QMessageBox.information(self, "提示", "已有下载任务正在进行")
            return

        self.progress_bar.setRange(0, max(len(posts), 1))
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"准备下载 {len(posts)} 个作品…")
        self.log(f"开始下载 {len(posts)} 个作品")
        self._active_download_posts = list(posts)
        if not is_resume:
            self._resume_posts = []
        self.cancel_download_button.setEnabled(True)
        self.resume_download_button.setEnabled(False)
        self.download_thread = DownloadThread(
            posts=posts,
            download_dir=self.download_dir,
            cookie=self.request_cookie or self.manual_cookie,
            user_agent=self.request_user_agent,
        )
        self.download_thread.progress.connect(self.on_download_progress)
        self.download_thread.completed.connect(self.on_download_completed)
        self.download_thread.start()

    def cancel_download(self) -> None:
        if not self.download_thread or not self.download_thread.isRunning():
            return
        self.download_thread.request_cancel()
        self.cancel_download_button.setEnabled(False)
        self.progress_label.setText("正在安全中断下载任务…")
        self.statusBar().showMessage("正在中断下载")
        self.log("已请求中断下载；当前文件完成或停止接收后将结束任务")

    def resume_download(self) -> None:
        if not self._resume_posts:
            return
        self.log(f"继续下载剩余任务：重新检查 {len(self._resume_posts)} 个作品")
        self.start_download(self._resume_posts, is_resume=True)

    def on_download_progress(
        self, current: int, total: int, success_count: int, is_success: bool
    ) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)
        state = "成功" if is_success else "失败"
        message = f"下载 {current}/{total}，当前{state}，已成功 {success_count} 个"
        self.progress_label.setText(message)
        self.statusBar().showMessage(message)

    def on_download_completed(self, result: dict) -> None:
        self.cancel_download_button.setEnabled(False)
        total = result.get("total", 0)
        self.progress_bar.setRange(0, max(total, 1))
        processed = result.get("success", 0) + result.get("failed", 0) + result.get("skipped", 0)
        self.progress_bar.setValue(processed if result.get("cancelled") else total)
        if result.get("cancelled"):
            self._resume_posts = list(self._active_download_posts)
            self.resume_download_button.setEnabled(bool(self._resume_posts))
            summary = (
                f"下载已中断：成功 {result.get('success', 0)}，失败 {result.get('failed', 0)}，"
                f"跳过 {result.get('skipped', 0)}，未处理 {result.get('remaining', 0)}"
            )
        else:
            self._resume_posts = []
            self.resume_download_button.setEnabled(False)
            summary = (
                f"下载完成：成功 {result.get('success', 0)}，失败 {result.get('failed', 0)}，"
                f"跳过 {result.get('skipped', 0)}"
            )
        if result.get("error"):
            summary += f"\n错误：{result['error']}"
        if result.get("refresh_summary"):
            summary = result["refresh_summary"] + "\n" + summary
        detail_lines = self._format_download_details(result)
        if detail_lines:
            summary += "\n" + "\n".join(detail_lines)
        self.progress_label.setText(summary.replace("\n", "  "))
        self.log(summary)
        self.statusBar().showMessage(summary.split("\n", 1)[0])
        QMessageBox.information(self, "下载已中断" if result.get("cancelled") else "下载完成", summary)

    @staticmethod
    def _format_download_details(result: dict) -> list[str]:
        """Group per-item failure/skip reasons for a readable completion dialog."""
        lines: list[str] = []
        for detail_key, heading in (
            ("failed_details", "下载失败原因"),
            ("skipped_details", "跳过原因"),
        ):
            counts: dict[str, int] = {}
            for detail in result.get(detail_key, []):
                reason = str(detail.get("reason") or "未提供原因")
                counts[reason] = counts.get(reason, 0) + 1
            for reason, count in list(counts.items())[:3]:
                lines.append(f"{heading}：{reason}（{count} 个）")
            if len(counts) > 3:
                lines.append(f"{heading}：另有 {len(counts) - 3} 种原因，请查看日志")
        return lines

    def change_download_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择下载保存目录", self.download_dir)
        if directory:
            try:
                self.download_dir = save_download_dir(directory)
            except OSError as exc:
                self.download_dir = os.path.abspath(directory)
                logger.warning("保存下载目录设置失败: %s", exc)
                QMessageBox.warning(
                    self,
                    "设置保存失败",
                    "本次会话仍会使用该目录，但无法保存到下次启动。\n\n"
                    f"原因：{exc}",
                )
            self.download_dir_label.setText(f"保存目录：{self.download_dir}")
            self.log(f"下载目录已更改并保存为：{self.download_dir}")

    def log(self, message: str) -> None:
        from datetime import datetime

        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")

    @staticmethod
    def _create_checkbox() -> QCheckBox:
        checkbox = QCheckBox()
        checkbox.setStyleSheet("QCheckBox { margin-left: 12px; }")
        return checkbox
