#!/usr/bin/env python3
"""
抖音内容下载工具 - 主入口
============================
启动 PyQt5 GUI 界面。

全自动模式：只需输入博主主页链接，自动获取 Cookie、解析、下载。
手动模式：自行提供 Cookie。

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

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
