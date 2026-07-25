"""
GUI 主窗口模块
==============
基于 PyQt5 的图形界面，提供两种模式：
1. 全自动模式（推荐）— 用户只需输入博主主页链接，自动获取 Cookie、解析、下载
2. 手动模式 — 用户自行提供 Cookie
"""

import os
import sys
import threading
from typing import Optional

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTextEdit, QTableWidget, QTableWidgetItem,
    QProgressBar, QCheckBox, QHeaderView, QMessageBox, QStatusBar,
    QFileDialog, QGroupBox, QButtonGroup, QRadioButton, QSpinBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont

from src.core.douyin_api import DouyinAPI
from src.core.downloader import Downloader
from src.core.auto_pipeline import AutoPipeline
from src.utils.logger import get_logger
from src.utils.cookie_helper import load_cookie, save_cookie, get_cookie_guide

logger = get_logger(__name__)


class AutoPipelineThread(QThread):
    """自动化流程后台线程"""
    status = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)  # current, total, message
    finished_result = pyqtSignal(dict)
    posts_fetched = pyqtSignal(list, dict)  # posts, user_info

    def __init__(self, url: str, download_dir: str, max_posts: int = 0, auto_download: bool = True):
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.max_posts = max_posts
        self.auto_download = auto_download

    def run(self):
        try:
            pipeline = AutoPipeline(
                download_dir=self.download_dir,
                headless=True,
            )

            def status_cb(msg):
                self.status.emit(msg)

            def progress_cb(current, total, msg):
                self.progress.emit(current, total, msg)

            result = pipeline.run(
                profile_url=self.url,
                max_posts=self.max_posts,
                auto_download=self.auto_download,
                progress_callback=progress_cb,
                status_callback=status_cb,
            )

            if result.get("posts") and result.get("user_info"):
                self.posts_fetched.emit(result["posts"], result["user_info"])

            self.finished_result.emit(result)

        except Exception as e:
            self.finished_result.emit({
                "success": False,
                "error": f"自动化流程异常: {e}",
                "user_info": None,
                "posts": [],
                "download_result": None,
            })


class FetchPostsThread(QThread):
    """手动模式：获取作品列表的后台线程"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(list, dict)
    error = pyqtSignal(str)

    def __init__(self, url: str, cookie: str):
        super().__init__()
        self.url = url
        self.cookie = cookie

    def run(self):
        try:
            api = DouyinAPI(cookie=self.cookie)

            self.progress.emit("正在解析博主链接...")
            sec_uid = DouyinAPI.extract_sec_uid(self.url)
            if not sec_uid:
                self.error.emit("无法从链接中提取博主信息，请检查链接是否正确")
                return

            self.progress.emit("正在获取博主信息...")
            user_info = api.get_user_info(sec_uid)
            if not user_info:
                self.error.emit("获取博主信息失败，可能需要更新 Cookie")
                return

            self.progress.emit(
                f"博主: {user_info['nickname']} | 作品数: {user_info['aweme_count']} | 粉丝: {user_info['follower_count']}"
            )

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
    progress = pyqtSignal(int, int, int, bool)
    finished = pyqtSignal(dict)

    def __init__(self, posts: list, download_dir: str):
        super().__init__()
        self.posts = posts
        self.download_dir = download_dir

    def run(self):
        try:
            downloader = Downloader(self.download_dir)

            def progress_cb(current, total, success_count, is_success):
                self.progress.emit(current, total, success_count, is_success)

            result = downloader.download_batch(self.posts, progress_callback=progress_cb)
            self.finished.emit(result)

        except Exception as e:
            self.finished.emit({"success": 0, "failed": len(self.posts), "error": str(e)})


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.posts = []
        self.user_info = {}
        self.auto_thread = None
        self.fetch_thread = None
        self.download_thread = None
        self.download_dir = os.path.join(os.getcwd(), "downloads")

        self.init_ui()
        self.load_saved_cookie()

    def init_ui(self):
        """初始化 UI"""
        self.setWindowTitle("抖音内容下载工具 - 全自动模式")
        self.setMinimumSize(900, 700)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # ===== 模式选择 =====
        mode_group = QGroupBox("操作模式")
        mode_layout = QHBoxLayout(mode_group)

        self.mode_group = QButtonGroup()
        self.auto_radio = QRadioButton("全自动模式（推荐 - 自动获取 Cookie）")
        self.auto_radio.setChecked(True)
        self.manual_radio = QRadioButton("手动模式（自行提供 Cookie）")

        self.mode_group.addButton(self.auto_radio, 0)
        self.mode_group.addButton(self.manual_radio, 1)
        self.mode_group.buttonClicked.connect(self.on_mode_changed)

        mode_layout.addWidget(self.auto_radio)
        mode_layout.addWidget(self.manual_radio)
        mode_layout.addStretch()
        layout.addWidget(mode_group)

        # ===== 输入区域 =====
        input_group = QGroupBox("博主主页链接")
        input_layout = QVBoxLayout(input_group)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("请输入抖音博主主页链接，如 https://www.douyin.com/user/MS4w...")
        self.url_input.returnPressed.connect(self.start_action)

        self.action_btn = QPushButton("一键下载")
        self.action_btn.clicked.connect(self.start_action)

        url_row.addWidget(self.url_input, 4)
        url_row.addWidget(self.action_btn, 1)
        input_layout.addLayout(url_row)

        # 自动模式选项
        self.auto_options = QHBoxLayout()
        self.auto_options.addWidget(QLabel("最大下载数:"))
        self.max_posts_spin = QSpinBox()
        self.max_posts_spin.setRange(0, 9999)
        self.max_posts_spin.setValue(0)
        self.max_posts_spin.setSpecialValueText("全部")
        self.max_options_label = QLabel("(0=下载全部)")
        self.auto_options.addWidget(self.max_posts_spin)
        self.auto_options.addWidget(self.max_options_label)
        self.auto_options.addStretch()
        input_layout.addLayout(self.auto_options)

        layout.addWidget(input_group)

        # ===== Cookie 配置区域（手动模式可见） =====
        self.cookie_group = QGroupBox("Cookie 配置（手动模式）")
        cookie_layout = QHBoxLayout(self.cookie_group)

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
        layout.addWidget(self.cookie_group)

        # ===== 博主信息区域 =====
        self.user_info_label = QLabel("博主信息: 请输入链接并点击一键下载")
        self.user_info_label.setStyleSheet("color: #666; padding: 4px; font-size: 13px;")
        layout.addWidget(self.user_info_label)

        # ===== 作品列表 =====
        table_group = QGroupBox("作品列表")
        table_layout = QVBoxLayout(table_group)

        btn_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.clicked.connect(self.select_all)
        self.deselect_all_btn = QPushButton("取消全选")
        self.deselect_all_btn.clicked.connect(self.deselect_all)
        self.download_selected_btn = QPushButton("下载选中")
        self.download_selected_btn.clicked.connect(self.download_selected)
        self.change_dir_btn = QPushButton("更改保存目录")
        self.change_dir_btn.clicked.connect(self.change_download_dir)

        self.download_dir_label = QLabel(f"保存目录: {self.download_dir}")
        self.download_dir_label.setStyleSheet("color: #888; font-size: 11px;")

        btn_layout.addWidget(self.select_all_btn)
        btn_layout.addWidget(self.deselect_all_btn)
        btn_layout.addWidget(self.download_selected_btn)
        btn_layout.addWidget(self.change_dir_btn)
        btn_layout.addStretch()
        table_layout.addLayout(btn_layout)
        table_layout.addWidget(self.download_dir_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["选择", "序号", "类型", "标题", "发布时间", "互动数据"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 50)
        self.table.setColumnWidth(2, 60)
        table_layout.addWidget(self.table)

        layout.addWidget(table_group, 3)

        # ===== 进度条 =====
        progress_group = QGroupBox("进度")
        progress_layout = QVBoxLayout(progress_group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("就绪 - 全自动模式下只需输入链接即可")
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
        self.statusBar().showMessage("就绪 - 全自动模式")

        # 初始模式
        self.on_mode_changed()

    def on_mode_changed(self):
        """模式切换"""
        is_auto = self.auto_radio.isChecked()
        if is_auto:
            self.cookie_group.hide()
            self.max_posts_spin.setVisible(True)
            self.max_options_label.setVisible(True)
            self.action_btn.setText("一键下载")
            self.statusBar().showMessage("全自动模式 - 自动获取 Cookie、解析、下载")
        else:
            self.cookie_group.show()
            self.max_posts_spin.setVisible(False)
            self.max_options_label.setVisible(False)
            self.action_btn.setText("解析作品")
            self.statusBar().showMessage("手动模式 - 需自行提供 Cookie")

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
            QMessageBox.information(self, "成功", "Cookie 已保存")
        else:
            QMessageBox.critical(self, "错误", "Cookie 保存失败")

    def show_cookie_guide(self):
        """显示 Cookie 获取指南"""
        QMessageBox.information(self, "Cookie 获取指南", get_cookie_guide())

    def start_action(self):
        """开始操作（根据模式自动选择）"""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入抖音博主主页链接")
            return

        if self.auto_radio.isChecked():
            self.start_auto_pipeline(url)
        else:
            self.fetch_posts_manual(url)

    def start_auto_pipeline(self, url: str):
        """启动全自动流程"""
        max_posts = self.max_posts_spin.value()

        self.action_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log(f"开始全自动流程: {url}")

        self.auto_thread = AutoPipelineThread(url, self.download_dir, max_posts, auto_download=True)
        self.auto_thread.status.connect(self.log)
        self.auto_thread.progress.connect(self.on_auto_progress)
        self.auto_thread.posts_fetched.connect(self.on_posts_fetched)
        self.auto_thread.finished_result.connect(self.on_auto_finished)
        self.auto_thread.start()

    def on_auto_progress(self, current, total, msg):
        """全自动进度更新"""
        if total > 0:
            percent = int(current / total * 100)
            self.progress_bar.setValue(percent)
        self.progress_label.setText(msg)
        self.statusBar().showMessage(msg)

    def on_auto_finished(self, result: dict):
        """全自动完成"""
        self.action_btn.setEnabled(True)
        self.progress_bar.setValue(100)

        if result.get("success"):
            dl = result.get("download_result", {})
            summary = (
                f"完成！成功: {dl.get('success', 0)}, "
                f"失败: {dl.get('failed', 0)}, "
                f"跳过: {dl.get('skipped', 0)}"
            )
            self.progress_label.setText(summary)
            self.log(summary)
            QMessageBox.information(self, "完成", summary)
        else:
            error = result.get("error", "未知错误")
            self.progress_label.setText(f"失败: {error}")
            self.log(f"❌ {error}")
            QMessageBox.critical(self, "失败", error)

    def fetch_posts_manual(self, url: str):
        """手动模式：解析作品"""
        cookie = self.cookie_input.text().strip()
        if not cookie:
            reply = QMessageBox.question(
                self, "Cookie 缺失",
                "未配置 Cookie，可能无法获取作品。是否继续？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                return

        self.action_btn.setEnabled(False)
        self.log(f"开始解析: {url}")

        self.fetch_thread = FetchPostsThread(url, cookie)
        self.fetch_thread.progress.connect(self.log)
        self.fetch_thread.finished.connect(self.on_posts_fetched)
        self.fetch_thread.error.connect(self.on_fetch_error)
        self.fetch_thread.start()

    def on_posts_fetched(self, posts: list, user_info: dict):
        """作品列表获取完成"""
        self.action_btn.setEnabled(True)
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
        """解析失败"""
        self.action_btn.setEnabled(True)
        self.log(f"错误: {msg}")
        QMessageBox.critical(self, "解析失败", msg)

    def select_all(self):
        for i in range(self.table.rowCount()):
            cb = self.table.cellWidget(i, 0)
            if cb:
                cb.setChecked(True)

    def deselect_all(self):
        for i in range(self.table.rowCount()):
            cb = self.table.cellWidget(i, 0)
            if cb:
                cb.setChecked(False)

    def get_selected_posts(self) -> list:
        selected = []
        for i in range(self.table.rowCount()):
            cb = self.table.cellWidget(i, 0)
            if cb and cb.isChecked():
                selected.append(self.posts[i])
        return selected

    def download_selected(self):
        selected = self.get_selected_posts()
        if not selected:
            QMessageBox.warning(self, "提示", "请先选择要下载的作品")
            return
        self.start_download(selected)

    def start_download(self, posts: list):
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"准备下载 {len(posts)} 个作品...")
        self.log(f"开始下载 {len(posts)} 个作品")

        self.download_thread = DownloadThread(posts, self.download_dir)
        self.download_thread.progress.connect(self.on_download_progress)
        self.download_thread.finished.connect(self.on_download_finished)
        self.download_thread.start()

    def on_download_progress(self, current, total, success_count, is_success):
        percent = int(current / total * 100) if total > 0 else 0
        self.progress_bar.setValue(percent)
        self.progress_label.setText(
            f"进度: {current}/{total} | 成功: {success_count} | 当前: {'✓' if is_success else '✗'}"
        )
        self.statusBar().showMessage(f"下载中 {current}/{total}")

    def on_download_finished(self, result: dict):
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
        dir_path = QFileDialog.getExistingDirectory(self, "选择下载保存目录", self.download_dir)
        if dir_path:
            self.download_dir = dir_path
            self.download_dir_label.setText(f"保存目录: {self.download_dir}")
            self.log(f"下载目录已更改为: {self.download_dir}")

    def log(self, msg: str):
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {msg}")

    def _create_checkbox(self):
        cb = QCheckBox()
        cb.setStyleSheet("QCheckBox { margin-left: 12px; }")
        return cb

