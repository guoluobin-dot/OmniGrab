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
        /* 科技感 - 深空霓虹 */
        QMainWindow { background-color: #0a0f1e; }
        QWidget { font-family: "Microsoft YaHei"; color: #cbd5e1; }
        QGroupBox {
            font-weight: bold;
            font-size: 10.5pt;
            color: #00e5ff;
            border: 1px solid #1e3a5f;
            border-radius: 12px;
            margin-top: 16px;
            padding-top: 16px;
            background-color: rgba(15, 23, 42, 0.85);
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 14px;
            padding: 0 8px;
            background-color: #0a0f1e;
            color: #00e5ff;
        }
        QLabel { color: #94a3b8; }
        QLineEdit {
            padding: 9px 12px;
            border: 1px solid #1e3a5f;
            border-radius: 8px;
            background-color: #0f172a;
            color: #e2e8f0;
            selection-background-color: #0ea5e9;
            selection-color: #ffffff;
        }
        QLineEdit:focus { border: 1px solid #00e5ff; background-color: #13203a; }
        QLineEdit::placeholder { color: #475569; }
        QPushButton {
            padding: 7px 16px;
            border-radius: 8px;
            border: 1px solid #0ea5e9;
            background-color: #0f2942;
            color: #e0f2fe;
            font-weight: 600;
            letter-spacing: 0.3px;
        }
        QPushButton:hover {
            background-color: #12365e;
            border-color: #00e5ff;
            color: #ffffff;
        }
        QPushButton:pressed { background-color: #0a1e35; }
        QPushButton:disabled { background-color: #1e293b; color: #475569; border-color: #1e293b; }
        QCheckBox { spacing: 7px; color: #94a3b8; }
        QCheckBox::indicator {
            width: 16px; height: 16px;
            border-radius: 4px;
            border: 1px solid #334155;
            background: #0f172a;
        }
        QCheckBox::indicator:checked {
            background-color: #00e5ff;
            border-color: #00e5ff;
            image: url(none);
        }
        QCheckBox::indicator:hover { border-color: #00e5ff; }
        QSpinBox {
            padding: 6px 8px;
            border: 1px solid #1e3a5f;
            border-radius: 8px;
            background: #0f172a;
            color: #e2e8f0;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            background: #1e293b;
            border: none;
            width: 18px;
        }
        QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #334155; }
        QTableWidget {
            gridline-color: #1e293b;
            background-color: #0f172a;
            border: 1px solid #1e3a5f;
            border-radius: 10px;
            alternate-background-color: #111e36;
            selection-background-color: rgba(14, 165, 233, 0.35);
            selection-color: #ffffff;
            outline: none;
        }
        QHeaderView::section {
            padding: 9px 6px;
            background-color: #0f2942;
            color: #00e5ff;
            border: none;
            border-right: 1px solid #1e3a5f;
            border-bottom: 1px solid #00e5ff;
            font-weight: 700;
            font-size: 9pt;
            letter-spacing: 0.5px;
        }
        QTableWidget::item:selected { background-color: rgba(0, 229, 255, 0.22); color: #ffffff; }
        QProgressBar {
            border: 1px solid #1e3a5f;
            border-radius: 8px;
            background-color: #0f172a;
            text-align: center;
            color: #94a3b8;
            height: 16px;
            font-size: 8pt;
        }
        QProgressBar::chunk {
            background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #00e5ff, stop:0.5 #0ea5e9, stop:1 #8b5cf6);
            border-radius: 6px;
        }
        QTextEdit {
            background-color: #020617;
            border: 1px solid #1e3a5f;
            border-radius: 8px;
            color: #38bdf8;
            selection-background-color: #0ea5e9;
            padding: 6px;
        }
        QStatusBar {
            background-color: #020617;
            color: #64748b;
            border-top: 1px solid #1e3a5f;
        }
        QScrollBar:vertical {
            background: #0f172a;
            width: 10px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background: #1e3a5f;
            border-radius: 5px;
            min-height: 30px;
        }
        QScrollBar::handle:vertical:hover { background: #0ea5e9; }
        QScrollBar::add-line, QScrollBar::sub-line { height: 0px; }
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
