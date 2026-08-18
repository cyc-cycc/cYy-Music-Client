# -*- coding: utf-8 -*-
import os
import sys
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QHBoxLayout, QWidget
from .base_mixin import BaseMixin

class TitleBarMixin(BaseMixin):
    """标题栏创建与窗口控制"""

    def setup_title_bar(self):
        self.title_bar = QWidget(self)
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(50)

        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(10, 0, 10, 0)
        title_layout.setSpacing(8)

        icon_label = QLabel()
        try:
            if getattr(sys, 'frozen', False):
                base_dir = sys._MEIPASS
            else:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app 目录
            icon_path = os.path.join(base_dir, 'icon.ico')
            if os.path.exists(icon_path):
                icon = QIcon(icon_path)
                self.setWindowIcon(icon)
                pixmap = QPixmap(icon_path).scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                icon_label.setPixmap(pixmap)
                app = QApplication.instance()
                if app:
                    app.setWindowIcon(icon)
            else:
                if getattr(sys, 'frozen', False):
                    alt_path = os.path.join(os.path.dirname(sys.executable), 'icon.ico')
                    if os.path.exists(alt_path):
                        icon = QIcon(alt_path)
                        self.setWindowIcon(icon)
                        app = QApplication.instance()
                        if app:
                            app.setWindowIcon(icon)
        except Exception:
            pass
        icon_label.setFixedSize(24, 24)
        title_layout.addWidget(icon_label)

        title_label = QLabel("⚡️ cY Mic Cli")
        title_layout.addWidget(title_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索歌曲、歌手...")
        self.search_input.setFixedWidth(250)
        self.search_input.returnPressed.connect(self.on_search_or_stop)
        title_layout.addWidget(self.search_input)

        self.btn_search_title = QPushButton("🔍")
        self.btn_search_title.setObjectName("titleSearchButton")
        self.btn_search_title.setFixedSize(38, 28)
        self.btn_search_title.clicked.connect(self.on_search_or_stop)
        title_layout.addWidget(self.btn_search_title)

        self.btn_settings = QPushButton("⚙ 设置")
        self.btn_settings.setObjectName("titleSettingsButton")
        self.btn_settings.setFixedSize(80, 28)
        self.btn_settings.clicked.connect(self.open_settings)
        title_layout.addWidget(self.btn_settings)

        self.btn_about = QPushButton("❕️ 关于")
        self.btn_about.setObjectName("titleAboutButton")
        self.btn_about.setFixedSize(80, 28)
        self.btn_about.clicked.connect(self.show_about)
        title_layout.addWidget(self.btn_about)

        title_layout.addStretch()

        self.btn_minimize = QPushButton("—")
        self.btn_minimize.setObjectName("titleMinButton")
        self.btn_minimize.setFixedSize(32, 32)
        self.btn_minimize.clicked.connect(self.showMinimized)
        title_layout.addWidget(self.btn_minimize)

        self.btn_maximize = QPushButton("□")
        self.btn_maximize.setObjectName("titleMaxButton")
        self.btn_maximize.setFixedSize(32, 32)
        self.btn_maximize.clicked.connect(self.toggle_maximize)
        title_layout.addWidget(self.btn_maximize)

        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("titleCloseButton")
        self.btn_close.setFixedSize(32, 32)
        self.btn_close.clicked.connect(self.close)
        title_layout.addWidget(self.btn_close)

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            self.btn_maximize.setText("□")
        else:
            self.showMaximized()
            self.btn_maximize.setText("❐")

    # 窗口拖动事件（已在主窗口实现 mousePress/Move/Release，这里可留空）
