#!/usr/bin/env python3
"""
抖音内容下载工具 - 主入口
============================
启动 PyQt5 GUI 界面。

只需输入博主主页链接，即可通过持久化 Chrome 浏览器会话读取、预览并下载。
首次遇到平台登录或验证码时，在打开的浏览器窗口中完成验证即可；无需手动配置 Cookie。

使用方法:
    python main.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

from src.gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("抖音内容下载工具")
    app.setOrganizationName("DouyinDownloader")

    font = app.font()
    font.setFamily("Microsoft YaHei")
    font.setPointSize(10)
    app.setFont(font)

    app.setStyleSheet("""
        QMainWindow { background-color: #f5f5f5; }
        QGroupBox {
            font-weight: bold;
            border: 1px solid #d0d0d0;
            border-radius: 4px;
            margin-top: 10px;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }
        QPushButton {
            padding: 6px 16px;
            border-radius: 4px;
        }
        QPushButton:pressed { background-color: #d0d0d0; }
        QLineEdit { padding: 5px; }
        QTableWidget { gridline-color: #e0e0e0; }
        QHeaderView::section { padding: 5px; }
    """)

    if not ensure_chrome_available(app):
        sys.exit(1)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


def ensure_chrome_available(app: QApplication) -> bool:
    """启动前检测 Chrome；缺失时给出下载指引而不是让 Selenium 报错。"""
    from PyQt5.QtGui import QDesktopServices
    from PyQt5.QtCore import QUrl
    from PyQt5.QtWidgets import QMessageBox

    from src.gui.chrome_check import CHROME_DOWNLOAD_URL, chrome_missing_message, find_chrome

    if find_chrome():
        return True
    box = QMessageBox()
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("需要 Chrome 浏览器")
    box.setText(chrome_missing_message())
    open_button = box.addButton("打开下载页", QMessageBox.AcceptRole)
    continue_button = box.addButton("仍然继续", QMessageBox.DestructiveRole)
    box.addButton("退出", QMessageBox.RejectRole)
    box.exec_()
    clicked = box.clickedButton()
    if clicked is open_button:
        QDesktopServices.openUrl(QUrl(CHROME_DOWNLOAD_URL))
        return False
    return clicked is continue_button


if __name__ == "__main__":
    main()
