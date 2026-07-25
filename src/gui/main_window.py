"""
GUI 主窗口模块
==============
基于 PyQt5 的图形界面，提供：
- 输入博主主页链接
- 解析并展示作品列表
- 单独/批量下载
- 下载进度显示
"""

import os
import sys
import threading
from typing import Optional

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTextEdit, QTableWidget, QTableWidgetItem,
    QProgressBar, QCheckBox, QHeaderView, QMessageBox, QStatusBar,
    QFileDialog, QGroupBox, QSplitter,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QFont

from src.core.douyin_api import DouyinAPI
from src.core.downloader import Downloader
from src.utils.logger import get_logger
from src.utils.cookie_helper import load_cookie, save_cookie, get_cookie_guide

logger = get_logger(__name__)


class FetchPostsThread(QThread):
    """获取作品列表的后台线程"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(list, dict)  # posts, user_info
    error = pyqtSignal(str)

    def __init__(self, url: str, cookie: str):
        super().__init__()
        self.url = url
        self.cookie = cookie

    def run(self):
        try:
            api = DouyinAPI(cookie=self.cookie)

            # 提取 sec_uid
            self.progress.emit("正在解析博主链接...")
            sec_uid = DouyinAPI.extract_sec_uid(self.url)
            if not sec_uid:
                self.error.emit("无法从链接中提取博主信息，请检查链接是否正确")
                return

            # 获取用户信息
            self.progress.emit("正在获取博主信息...")
            user_info = api.get_user_info(sec_uid)
            if not user_info:
                self.error.emit("获取博主信息失败，可能需要更新 Cookie")
                return

            self.progress.emit(
                f"博主: {user_info['nickname']} | 作品数: {user_info['aweme_count']} | 粉丝: {user_info['follower_count']}"
            )

            # 获取作品列表
            def fetch_callback(count, total, has_more):
                self.progress.emit(f"已获取 {count} 个作品...")

            self.progress.emit("正在获取作品列表，请稍候...")
            posts = api.get_user_posts(sec_uid, callback=fetch_callback)

            self.progress.emit(f"获取完成，共 {len(posts)} 个作品")
            self.finished.emit(posts, user_info)

        except Exception as e:
            self.error.emit(f"获取作品列表异常: {e}")


class DownloadThread(QThread):
    """下载作品的后台线程"""
    progress = pyqtSignal(int, int, int, bool)  # current, total, success_count, is_success
    single_progress = pyqtSignal(int, int)  # downloaded, total_size
    finished = pyqtSignal(dict)  # result stats
    log = pyqtSignal(str)

    def __init__(self, posts: list, download_dir: str):
        super().__init__()
        self.posts = posts
        self.download_dir = download_dir

    def run(self):
        try:
            downloader = Downloader(self.download_dir)

            def progress_cb(current, total, success_count, is_success):
                self.progress.emit(current, total, success_count, is_success)

            def single_cb(downloaded, total_size):
                self.single_progress.emit(downloaded, total_size)

            result = downloader.download_batch(
                self.posts,
                progress_callback=progress_cb,
            )
            self.finished.emit(result)

        except Exception as e:
            self.log.emit(f"下载异常: {e}")
            self.finished.emit({"success": 0, "failed": len(self.posts), "error": str(e)})


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.posts = []
        self.user_info = {}
        self.fetch_thread = None
        self.download_thread = None
        self.download_dir = os.path.join(os.getcwd(), "downloads")

        self.init_ui()
        self.load_saved_cookie()

    def init_ui(self):
        """初始化 UI"""
        self.setWindowTitle("抖音内容下载工具")
        self.setMinimumSize(900, 650)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # ===== 输入区域 =====
        input_group = QGroupBox("博主主页链接")
        input_layout = QHBoxLayout(input_group)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("请输入抖音博主主页链接，如 https://www.douyin.com/user/MS4w...")
        self.url_input.returnPressed.connect(self.fetch_posts)

        self.fetch_btn = QPushButton("解析作品")
        self.fetch_btn.clicked.connect(self.fetch_posts)

        input_layout.addWidget(self.url_input, 4)
        input_layout.addWidget(self.fetch_btn, 1)
        layout.addWidget(input_group)

        # ===== Cookie 配置区域 =====
        cookie_group = QGroupBox("Cookie 配置（获取作品需要登录态）")
        cookie_layout = QHBoxLayout(cookie_group)

        self.cookie_input = QLineEdit()
        self.cookie_input.setPlaceholderText("粘贴抖音网页版 Cookie（包含 ttwid 和 msToken）...")
        self.cookie_input.setEchoMode(QLineEdit.Password)

        self.save_cookie_btn = QPushButton("保存 Cookie")
        self.save_cookie_btn.clicked.connect(self.save_cookie)

        self.cookie_guide_btn = QPushButton("获取指南")
        self.cookie_guide_btn.clicked.connect(self.show_cookie_guide)

        cookie_layout.addWidget(self.cookie_input, 4)
        cookie_layout.addWidget(self.save_cookie_btn, 1)
        cookie_layout.addWidget(self.cookie_guide_btn, 1)
        layout.addWidget(cookie_group)

        # ===== 博主信息区域 =====
        self.user_info_label = QLabel("博主信息: 请先解析博主链接")
        self.user_info_label.setStyleSheet("color: #666; padding: 4px;")
        layout.addWidget(self.user_info_label)

        # ===== 作品列表 + 操作按钮 =====
        table_group = QGroupBox("作品列表")
        table_layout = QVBoxLayout(table_group)

        # 操作按钮行
        btn_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.clicked.connect(self.select_all)
        self.deselect_all_btn = QPushButton("取消全选")
        self.deselect_all_btn.clicked.connect(self.deselect_all)
        self.download_selected_btn = QPushButton("下载选中")
        self.download_selected_btn.clicked.connect(self.download_selected)
        self.download_all_btn = QPushButton("一键下载全部")
        self.download_all_btn.clicked.connect(self.download_all)
        self.change_dir_btn = QPushButton("更改保存目录")
        self.change_dir_btn.clicked.connect(self.change_download_dir)

        self.download_dir_label = QLabel(f"保存目录: {self.download_dir}")
        self.download_dir_label.setStyleSheet("color: #888; font-size: 11px;")

        btn_layout.addWidget(self.select_all_btn)
        btn_layout.addWidget(self.deselect_all_btn)
        btn_layout.addWidget(self.download_selected_btn)
        btn_layout.addWidget(self.download_all_btn)
        btn_layout.addWidget(self.change_dir_btn)
        btn_layout.addStretch()
        table_layout.addLayout(btn_layout)
        table_layout.addWidget(self.download_dir_label)

        # 作品表格
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["选择", "序号", "类型", "标题", "发布时间", "互动数据"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 50)
        self.table.setColumnWidth(2, 60)
        table_layout.addWidget(self.table)

        layout.addWidget(table_group, 3)

        # ===== 进度条 =====
        progress_group = QGroupBox("下载进度")
        progress_layout = QVBoxLayout(progress_group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("就绪")
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_label)
        layout.addWidget(progress_group)

        # ===== 日志区域 =====
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4; font-family: Consolas;")
        layout.addWidget(self.log_text)

        # ===== 状态栏 =====
        self.statusBar().showMessage("就绪")

    # ===== 功能方法 =====

    def load_saved_cookie(self):
        """加载已保存的 Cookie"""
        cookie = load_cookie()
        if cookie:
            self.cookie_input.setText(cookie)
            self.log("已加载保存的 Cookie")

    def save_cookie(self):
        """保存 Cookie"""
        cookie = self.cookie_input.text().strip()
        if not cookie:
            QMessageBox.warning(self, "提示", "请先输入 Cookie")
            return
        if save_cookie(cookie):
            self.log("Cookie 已保存")
            QMessageBox.information(self, "成功", "Cookie 已保存到配置文件")
        else:
            QMessageBox.critical(self, "错误", "Cookie 保存失败")

    def show_cookie_guide(self):
        """显示 Cookie 获取指南"""
        QMessageBox.information(self, "Cookie 获取指南", get_cookie_guide())

    def fetch_posts(self):
        """解析博主作品"""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入抖音博主主页链接")
            return

        cookie = self.cookie_input.text().strip()
        if not cookie:
            reply = QMessageBox.question(
                self, "Cookie 缺失",
                "未配置 Cookie，可能无法获取作品列表。是否继续？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                return

        self.fetch_btn.setEnabled(False)
        self.log(f"开始解析: {url}")

        self.fetch_thread = FetchPostsThread(url, cookie)
        self.fetch_thread.progress.connect(self.log)
        self.fetch_thread.finished.connect(self.on_posts_fetched)
        self.fetch_thread.error.connect(self.on_fetch_error)
        self.fetch_thread.start()

    def on_posts_fetched(self, posts: list, user_info: dict):
        """作品列表获取完成回调"""
        self.fetch_btn.setEnabled(True)
        self.posts = posts
        self.user_info = user_info

        self.user_info_label.setText(
            f"博主: {user_info.get('nickname', '未知')} | "
            f"作品数: {user_info.get('aweme_count', 0)} | "
            f"粉丝: {user_info.get('follower_count', 0)} | "
            f"已获取: {len(posts)} 个作品"
        )

        self.table.setRowCount(0)
        for i, post in enumerate(posts):
            self.table.insertRow(i)
            self.table.setCellWidget(i, 0, self._create_checkbox())
            self.table.setItem(i, 1, QTableWidgetItem(str(i + 1)))
            self.table.setItem(i, 2, QTableWidgetItem(post.get("type_str", "视频")))
            self.table.setItem(i, 3, QTableWidgetItem(post.get("desc", "无标题")[:40]))
            self.table.setItem(i, 4, QTableWidgetItem(post.get("create_time_str", "")))
            stats = post.get("stats", {})
            self.table.setItem(
                i, 5,
                QTableWidgetItem(
                    f"❤{stats.get('digg_count', 0)} 💬{stats.get('comment_count', 0)} "
                    f"🔁{stats.get('share_count', 0)}"
                ),
            )

        self.log(f"解析完成，共 {len(posts)} 个作品")
        self.statusBar().showMessage(f"已加载 {len(posts)} 个作品")

    def on_fetch_error(self, msg: str):
        """解析失败回调"""
        self.fetch_btn.setEnabled(True)
        self.log(f"错误: {msg}")
        QMessageBox.critical(self, "解析失败", msg)

    def select_all(self):
        """全选"""
        for i in range(self.table.rowCount()):
            cb = self.table.cellWidget(i, 0)
            if cb:
                cb.setChecked(True)

    def deselect_all(self):
        """取消全选"""
        for i in range(self.table.rowCount()):
            cb = self.table.cellWidget(i, 0)
            if cb:
                cb.setChecked(False)

    def get_selected_posts(self) -> list:
        """获取选中的作品列表"""
        selected = []
        for i in range(self.table.rowCount()):
            cb = self.table.cellWidget(i, 0)
            if cb and cb.isChecked():
                selected.append(self.posts[i])
        return selected

    def download_selected(self):
        """下载选中的作品"""
        selected = self.get_selected_posts()
        if not selected:
            QMessageBox.warning(self, "提示", "请先选择要下载的作品")
            return
        self.start_download(selected)

    def download_all(self):
        """下载全部作品"""
        if not self.posts:
            QMessageBox.warning(self, "提示", "请先解析博主作品")
            return
        self.start_download(self.posts)

    def start_download(self, posts: list):
        """开始下载"""
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"准备下载 {len(posts)} 个作品...")
        self.log(f"开始下载 {len(posts)} 个作品")

        self.download_thread = DownloadThread(posts, self.download_dir)
        self.download_thread.progress.connect(self.on_download_progress)
        self.download_thread.single_progress.connect(self.on_single_progress)
        self.download_thread.finished.connect(self.on_download_finished)
        self.download_thread.log.connect(self.log)
        self.download_thread.start()

    def on_download_progress(self, current, total, success_count, is_success):
        """下载进度回调"""
        percent = int(current / total * 100) if total > 0 else 0
        self.progress_bar.setValue(percent)
        self.progress_label.setText(
            f"进度: {current}/{total} | 成功: {success_count} | "
            f"当前: {'✓' if is_success else '✗'}"
        )
        self.statusBar().showMessage(f"下载中 {current}/{total}")

    def on_single_progress(self, downloaded, total_size):
        """单个文件下载进度"""
        if total_size > 0:
            mb_down = downloaded / 1024 / 1024
            mb_total = total_size / 1024 / 1024
            self.log(f"  下载中: {mb_down:.1f}MB / {mb_total:.1f}MB")

    def on_download_finished(self, result: dict):
        """下载完成回调"""
        self.progress_bar.setValue(100)
        summary = (
            f"下载完成！成功: {result.get('success', 0)}, "
            f"失败: {result.get('failed', 0)}, "
            f"跳过: {result.get('skipped', 0)}"
        )
        self.progress_label.setText(summary)
        self.log(summary)
        self.statusBar().showMessage(summary)
        QMessageBox.information(self, "下载完成", summary)

    def change_download_dir(self):
        """更改下载目录"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择下载保存目录", self.download_dir)
        if dir_path:
            self.download_dir = dir_path
            self.download_dir_label.setText(f"保存目录: {self.download_dir}")
            self.log(f"下载目录已更改为: {self.download_dir}")

    def log(self, msg: str):
        """添加日志"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {msg}")

    def _create_checkbox(self):
        """创建表格中的复选框"""
        cb = QCheckBox()
        cb.setStyleSheet("QCheckBox { margin-left: 12px; }")
        return cb
