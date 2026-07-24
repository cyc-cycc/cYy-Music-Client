# -*- coding: utf-8 -*-
import os
import sys
import re
import glob
import logging
import traceback
import time
from typing import Dict, List, Optional, Tuple

# ===== 必须先设置运行时路径，再导入可能依赖 VLC 的模块 =====
from utils import get_resource_path
from utils import setup_runtime_paths
setup_runtime_paths()

# ===== 导入常量 =====
from constants import (
    SOURCE_GROUPS, SOURCE_INTERNAL, FILENAME_FORMATS, PLAYLIST_SOURCE_MAP,
    APP_DIR, DATA_DIR, LOG_DIR, LOG_FILE, DEFAULT_SAVE_DIR,
    PlayerState, PlayerMediaStatus, PlayMode
)

# ===== 导入工具函数 =====
from utils import logger, get_cover_url, sanitize_filepath, download_cover_image

# ===== 导入播放器、可视化、线程、自定义控件 =====
from player import PlayerWrapper
from visualizer import AudioVisualizer
from threads import SearchThread, PlaylistParseThread, DownloadThread, CoverRunnable
from widgets import SongCard, ClickableSlider, MarqueeLabel, SettingsDialog

# ===== 第三方库 =====
from musicdl import musicdl
import requests

# ===== PyQt5 导入 =====
from PyQt5 import QtCore
from PyQt5.QtGui import (
    QIcon, QFont, QPixmap, QColor, QMouseEvent,
    QPainter, QBrush, QPen, QPalette, QDesktopServices
)
from PyQt5.QtCore import (
    QThread, pyqtSignal, Qt, QTimer, QObject,
    QRunnable, QThreadPool, pyqtSlot, QPoint, QRect,
    QRectF, QUrl, QSize
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QCheckBox, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QGridLayout,
    QProgressBar, QMenu, QMessageBox, QAbstractItemView,
    QSpinBox, QHeaderView, QFileDialog, QComboBox, QHBoxLayout,
    QVBoxLayout, QGroupBox, QSizePolicy, QSlider, QListWidget,
    QListWidgetItem, QProgressDialog, QMainWindow, QFrame,
    QSplitter, QDialog, QTabWidget, QFormLayout
)

# ==================== 主窗口 ====================
class MusicdlGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setObjectName("musicdlGUI")
        self.setWindowTitle('🎵 音乐下载器 cYy edit')
        self.setMinimumSize(1450, 900)
        self.resize(1450, 900)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(self.get_style_sheet())

        self.playlist = []
        self.current_play_index = -1
        self.play_mode = PlayMode.ListRepeat

        self.setup_title_bar()
        self._init_ui()
        self._signals_inited = False
        self._init_signals()
        self._init_state()
        self._init_player()

        self._requests_session = requests.Session()
        self._requests_session.verify = True

        self._resizing = False
        self._resize_start_pos = QPoint()
        self._resize_start_geo = QRect()

        self._download_cancelled = False

        self.settings = {
            'sources': [
                    '酷我音乐(普通无损,推荐)'
                ],
            'limit': 10,
            'dedup': False,
            'save_dir': DEFAULT_SAVE_DIR,
            'filename_format': '歌手-歌曲名',
            'custom_format': '',
            'download_lyric': True,
            'download_cover': True
        }

    def setup_title_bar(self):
        self.title_bar = QWidget(self)
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(50)

        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(10, 0, 10, 0)
        title_layout.setSpacing(8)

        icon_label = QLabel()
        try:
            # 直接使用统一函数
            icon_path = get_resource_path('icon.ico')
            if os.path.exists(icon_path):
                icon = QIcon(icon_path)
                self.setWindowIcon(icon)
                pixmap = QPixmap(icon_path).scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                icon_label.setPixmap(pixmap)
                app = QApplication.instance()
                if app:
                    app.setWindowIcon(icon)
            else:
                # 如果找不到，尝试备用路径（根据打包环境）
                pass
        except Exception:
            pass
        icon_label.setFixedSize(24, 24)
        title_layout.addWidget(icon_label)

        title_label = QLabel("⚡️ cY Mic Cli")
        title_label.setStyleSheet("color: #2C3E50; font-weight: bold; font-size: 14px;")
        title_layout.addWidget(title_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索歌曲、歌手...")
        self.search_input.setFixedWidth(250)
        self.search_input.returnPressed.connect(self.on_search_or_stop)
        title_layout.addWidget(self.search_input)

        self.btn_search_title = QPushButton("🔍")
        self.btn_search_title.setObjectName("titleSearchButton")
        self.btn_search_title.setFixedSize(36, 28)
        self.btn_search_title.clicked.connect(self.on_search_or_stop)
        title_layout.addWidget(self.btn_search_title)

        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setObjectName("titleSettingsButton")
        self.btn_settings.setFixedSize(36, 28)
        self.btn_settings.clicked.connect(self.open_settings)
        title_layout.addWidget(self.btn_settings)

        self.btn_about = QPushButton("i")
        self.btn_about.setObjectName("titleAboutButton")
        self.btn_about.setFixedSize(36, 28)
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

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addWidget(self.title_bar)

        content_widget = QWidget()
        content_widget.setObjectName("contentWidget")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(15, 10, 15, 15)
        content_layout.setSpacing(10)

        playlist_layout = QHBoxLayout()
        playlist_layout.addWidget(QLabel("歌单链接:"))
        self.lineedit_playlist = QLineEdit()
        self.lineedit_playlist.setPlaceholderText("粘贴歌单链接，如 https://music.163.com/#/playlist?id=xxx")
        playlist_layout.addWidget(self.lineedit_playlist, 1)
        playlist_layout.addWidget(QLabel("平台:"))
        self.combo_playlist_source = QComboBox()
        self.combo_playlist_source.addItems(list(PLAYLIST_SOURCE_MAP.keys()))
        playlist_layout.addWidget(self.combo_playlist_source)
        self.button_parse_playlist = QPushButton("📋 解析歌单(较慢！)")
        self.button_parse_playlist.setObjectName("parsePlaylistButton")
        playlist_layout.addWidget(self.button_parse_playlist)
        content_layout.addLayout(playlist_layout)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(3)

        self.result_list = QListWidget()
        self.result_list.setObjectName("resultList")
        self.result_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.result_list.setSpacing(5)
        self.result_list.setStyleSheet("""
            QListWidget#resultList {
                background: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item {
                background: transparent;
                border: none;
            }
            QListWidget#resultList QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0px;
            }
            QListWidget#resultList QScrollBar::handle:vertical {
                background: rgba(160, 160, 160, 180);
                border-radius: 3px;
                min-height: 20px;
            }
            QListWidget#resultList QScrollBar::add-line:vertical,
            QListWidget#resultList QScrollBar::sub-line:vertical {
                height: 0px;
                background: transparent;
            }
            QListWidget#resultList QScrollBar::add-page:vertical,
            QListWidget#resultList QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)
        self.result_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_list.customContextMenuRequested.connect(self.show_context_menu)
        self.result_list.itemSelectionChanged.connect(self.on_selection_changed)
        splitter.addWidget(self.result_list)

        play_group = QGroupBox("播放控制")
        play_group.setObjectName("playGroup")
        play_layout = QVBoxLayout(play_group)

        top_layout = QHBoxLayout()
        top_layout.setSpacing(10)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(120, 120)
        self.cover_label.setStyleSheet("border: 1px solid #BDC3C7; border-radius: 4px; background-color: #E8EDF2;")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setText("🎵")
        self.cover_label.mousePressEvent = self.cover_click
        top_layout.addWidget(self.cover_label)

        self.now_playing_label = MarqueeLabel(self)
        self.now_playing_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.now_playing_label.setStyleSheet("font-weight: bold; color: #1E88E5; font-size: 16px;")
        self.now_playing_label.setObjectName("nowPlayingLabel")
        self.now_playing_label.setFixedHeight(30)
        top_layout.addWidget(self.now_playing_label, 1)

        play_layout.addLayout(top_layout)

        self.lyric_display = QListWidget()
        self.lyric_display.itemClicked.connect(self.on_lyric_clicked)
        self.lyric_display.setSelectionMode(QAbstractItemView.NoSelection)
        self.lyric_display.setWordWrap(True)
        lyric_font = QFont("Microsoft YaHei", 10)
        self.lyric_display.setFont(lyric_font)
        self.lyric_display.setMinimumHeight(80)
        self.lyric_display.setStyleSheet("""
            QListWidget {
                background: rgba(255,255,255,0.8);
                border: 1px solid rgba(74,144,217,0.3);
                border-radius: 10px;
                padding: 6px;
                font-family: "Microsoft YaHei";
                font-size: 13px;
            }
            QListWidget::item {
                padding: 6px 10px;
                margin: 2px 0;
                border-radius: 6px;
                background: transparent;
                color: #2C3E50;
            }
            QListWidget::item:hover {
                background: rgba(74,144,217,0.15);  /* 鼠标悬停效果 */
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(74,144,217,0.5);
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        play_layout.addWidget(self.lyric_display, 1)

        progress_layout = QHBoxLayout()
        self.slider_position = ClickableSlider(Qt.Horizontal, self)
        self.slider_position.setRange(0, 0)
        self.slider_position.setTracking(True)
        self.label_time = QLabel("00:00 / 00:00")
        self.label_time.setMinimumWidth(120)
        self.label_time.setStyleSheet("background-color: transparent; color: #2C3E50;")
        progress_layout.addWidget(self.slider_position, 1)
        progress_layout.addWidget(self.label_time)
        play_layout.addLayout(progress_layout)

        controls_row1 = QHBoxLayout()
        self.btn_prev = QPushButton("⏪")
        self.btn_prev.setObjectName("prevButton")
        self.btn_prev.setFixedWidth(50)
        self.btn_play = QPushButton("▶")
        self.btn_play.setObjectName("playButton")
        self.btn_play.setFixedWidth(60)
        self.btn_next = QPushButton("⏩")
        self.btn_next.setObjectName("nextButton")
        self.btn_next.setFixedWidth(50)
        self.btn_stop = QPushButton("⏹")
        self.btn_stop.setObjectName("stopButton")
        self.btn_stop.setFixedWidth(50)

        controls_row1.addWidget(self.btn_prev)
        controls_row1.addWidget(self.btn_play)
        controls_row1.addWidget(self.btn_next)
        controls_row1.addWidget(self.btn_stop)
        controls_row1.addStretch()

        play_layout.addLayout(controls_row1)

        controls_row2 = QHBoxLayout()
        self.btn_visualize = QPushButton("🎨")
        self.btn_visualize.setObjectName("visualizeButton")
        self.btn_visualize.setFixedWidth(50)
        self.btn_visualize.setToolTip("打开可视化窗口")
        controls_row2.addWidget(self.btn_visualize)
        controls_row2.addStretch()

        controls_row2.addWidget(QLabel("🔊"))
        self.slider_volume = QSlider(Qt.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(60)
        self.slider_volume.setFixedWidth(80)
        controls_row2.addWidget(self.slider_volume)

        controls_row2.addStretch()
        controls_row2.addWidget(QLabel("模式:"))
        self.combo_playmode = QComboBox()
        self.combo_playmode.addItems(["单曲循环", "单曲暂停", "列表循环", "列表暂停"])
        self.combo_playmode.setCurrentIndex(2)
        self.combo_playmode.currentIndexChanged.connect(self.on_playmode_changed)
        controls_row2.addWidget(self.combo_playmode)

        play_layout.addLayout(controls_row2)
        splitter.addWidget(play_group)
        play_group.setMinimumWidth(280)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        content_layout.addWidget(splitter, 1)

        progress_layout2 = QHBoxLayout()
        progress_layout2.addWidget(QLabel("单曲进度:"))
        self.bar_download = QProgressBar()
        self.bar_download.setObjectName('progressBar')
        progress_layout2.addWidget(self.bar_download)
        progress_layout2.addWidget(QLabel("总进度:"))
        self.bar_overall = QProgressBar()
        self.bar_overall.setObjectName('overallProgressBar')
        progress_layout2.addWidget(self.bar_overall)
        content_layout.addLayout(progress_layout2)

        self.label_stats = QLabel('就绪')
        self.label_stats.setObjectName('statsLabel')
        self.label_stats.setAlignment(Qt.AlignCenter)
        content_layout.addWidget(self.label_stats)

        self.context_menu = QMenu(self)
        self.action_download = self.context_menu.addAction('⬇️ 下载选中')
        self.action_download.setObjectName('downloadAction')
        self.action_download.triggered.connect(self.download_selected)

        main_layout.addWidget(content_widget)

    def get_style_sheet(self):
        return """
        QWidget {
            font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", "Segoe UI", sans-serif;
            font-size: 12px;
        }
        QWidget#musicdlGUI {
            background-color: #F5F7FA;
            border-radius: 8px;
        }
        #titleBar {
            background-color: #E8F0FE;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            border-bottom: 1px solid #BDC3C7;
        }
        #titleBar QLabel {
            background: transparent;
            font-size: 14px;
        }
        #titleSearchButton, #titleSettingsButton, #titleAboutButton {
            background-color: transparent;
            border: none;
            border-radius: 4px;
            font-size: 16px;
            color: #2C3E50;
        }
        #titleSearchButton:hover, #titleSettingsButton:hover, #titleAboutButton:hover {
            background-color: #D5D8DC;
        }
        #titleMinButton, #titleMaxButton, #titleCloseButton {
            background-color: transparent;
            border: none;
            border-radius: 4px;
            font-size: 16px;
            font-weight: bold;
            color: #2C3E50;
        }
        #titleMinButton:hover { background-color: #D5D8DC; }
        #titleMaxButton:hover { background-color: #D5D8DC; }
        #titleCloseButton:hover { background-color: #E74C3C; color: white; }
        #contentWidget {
            background-color: rgba(200, 225, 245, 240);
            border-bottom-left-radius: 8px;
            border-bottom-right-radius: 8px;
        }
        QGroupBox {
            font-weight: bold;
            border: 1px solid #BDC3C7;
            border-radius: 5px;
            margin-top: 10px;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }
        QGroupBox#playGroup {
            background-color: #E8F0FE;
            border-color: #4A90D9;
        }
        QLabel { color: #2C3E50; }
        QCheckBox { color: #2C3E50; spacing: 5px; }
        QCheckBox::indicator { width: 16px; height: 16px; }
        QLineEdit, QSpinBox, QComboBox {
            background-color: white;
            border: 1px solid #BDC3C7;
            border-radius: 5px;
            padding: 5px;
            color: #2C3E50;
        }
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
            border: 1px solid #4A90D9;
        }
        QPushButton {
            background-color: #E8EDF2;
            color: #2C3E50;
            border: 1px solid #BDC3C7;
            border-radius: 4px;
            padding: 4px 10px;
        }
        QPushButton:hover { background-color: #D5D8DC; }
        QPushButton#playButton {
            background-color: #4A90D9;
            color: white;
            font-weight: bold;
            border: none;
        }
        QPushButton#playButton:hover { background-color: #357ABD; }
        QPushButton#stopButton {
            background-color: #E67E22;
            color: white;
            font-weight: bold;
            border: none;
        }
        QPushButton#stopButton:hover { background-color: #D35400; }
        QPushButton#prevButton, QPushButton#nextButton {
            background-color: #5DADE2;
            color: white;
            font-weight: bold;
            border: none;
            border-radius: 4px;
        }
        QPushButton#prevButton:hover, QPushButton#nextButton:hover {
            background-color: #3498DB;
        }
        QPushButton#visualizeButton {
            background-color: #8E44AD;
            color: white;
            font-weight: bold;
            border: none;
            border-radius: 4px;
        }
        QPushButton#visualizeButton:hover { background-color: #6C3483; }
        QPushButton#parsePlaylistButton {
            background-color: #8E44AD;
            color: white;
            font-weight: bold;
            border: none;
            border-radius: 4px;
        }
        QPushButton#parsePlaylistButton:hover { background-color: #6C3483; }
        QTableWidget#resultTable {
            background-color: white;
            alternate-background-color: #ECF0F1;
            border: 1px solid #BDC3C7;
            border-radius: 5px;
            gridline-color: #D5D8DC;
        }
        QTableWidget::item { padding: 4px; color: #2C3E50; }
        QTableWidget::item:selected { background-color: #4A90D9; color: white; }
        QHeaderView::section {
            background-color: #4A90D9;
            color: white;
            padding: 5px;
            border: none;
        }
        QProgressBar {
            border: 1px solid #BDC3C7;
            border-radius: 5px;
            background-color: white;
            text-align: center;
            color: #2C3E50;
            font-weight: bold;
        }
        QProgressBar::chunk {
            background-color: #4A90D9;
            border-radius: 5px;
        }
        QLabel#statsLabel {
            color: #1E88E5;
            font-weight: bold;
            font-size: 13px;
            background-color: rgba(74, 144, 217, 0.1);
            border-radius: 5px;
            padding: 4px;
        }
        QMenu {
            background-color: white;
            border: 1px solid #BDC3C7;
            border-radius: 5px;
        }
        QMenu::item {
            padding: 6px 20px;
            color: #2C3E50;
        }
        QMenu::item:selected {
            background-color: #4A90D9;
            color: white;
        }
        QSlider::groove:horizontal {
            height: 6px;
            background: #D5D8DC;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: #4A90D9;
            width: 14px;
            height: 14px;
            margin: -4px 0;
            border-radius: 7px;
        }
        QSlider::sub-page:horizontal {
            background: #4A90D9;
            border-radius: 3px;
        }
        QListWidget {
            background-color: transparent;
            border: none;
            outline: none;
        }
        QListWidget::item {
            padding: 2px 5px;
        }
        QListWidget::item:selected {
            background: transparent;
        }
        """

    def _init_signals(self):
        self.button_parse_playlist.clicked.connect(self.parse_playlist)
        self.btn_play.clicked.connect(self.toggle_playback)
        self.btn_stop.clicked.connect(self.stop_playback)
        self.slider_position.sliderMoved.connect(self.set_position)
        self.slider_volume.valueChanged.connect(self.set_volume)
        self.btn_prev.clicked.connect(self.play_prev)
        self.btn_next.clicked.connect(self.play_next)
        self.btn_visualize.clicked.connect(self.show_visualization)
        self.result_list.itemDoubleClicked.connect(self.on_list_double_click)

    def _init_state(self):
        self.search_in_progress = False
        self.is_downloading = False
        self.is_parsing = False
        self._parse_ignore_signals = False
        self.search_thread = None
        self.download_thread = None
        self.parse_thread = None
        self.music_records = {}
        self.music_client = None
        self._source_counts = {}
        self._download_queue = []
        self._download_current_index = 0
        self._total_to_download = 0
        self._downloaded_files = []
        self._adjusting = False
        self._cover_task_id = 0
        self._last_cover_runnable = None
        self.current_lyrics = []
        self.current_lyric_index = -1

        self.drag_pos = QPoint()
        self.dragging = False
        self._vis_download_thread = None

        self.search_task_counter = 0
        self.current_search_task_id = 0
        self.parse_task_counter = 0
        self.current_parse_task_id = 0

    def _init_player(self):
        self.player = PlayerWrapper()
        self.player.setVolume(self.slider_volume.value())
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(self.update_duration)
        self.player.stateChanged.connect(self.update_play_button)
        self.player.mediaStatusChanged.connect(self.handle_media_status)
        self.player.positionChanged.connect(self.update_lyric_display)

    def open_settings(self):
        dlg = SettingsDialog(self)
        dlg.spin_limit.setValue(self.settings['limit'])
        dlg.check_dedup.setChecked(self.settings['dedup'])
        dlg.path_edit.setText(self.settings['save_dir'])
        dlg.format_combo.setCurrentText(self.settings['filename_format'])
        dlg.format_custom_edit.setText(self.settings['custom_format'])
        dlg.check_lyric.setChecked(self.settings['download_lyric'])
        dlg.check_cover.setChecked(self.settings['download_cover'])
        for cb in dlg.source_checkboxes:
            cb.setChecked(cb.text() in self.settings['sources'])

        if dlg.exec_() == QDialog.Accepted:
            new_settings = dlg.get_settings()
            self.settings.update(new_settings)
            self.label_stats.setText("设置已更新")

    def show_about(self):
        QMessageBox.about(self, "关于",
            "🎵 cYy Music Client\n"
            "基于 PyQt5 + musicdl\n"
            "版本 4.2.0\n"
            "本程序遵循 GNU 3.0 开源协议\n"
            "© 2026 cYy"
        )

    def cover_click(self, event):
        self.show_visualization()

    def on_search_or_stop(self):
        if self.is_parsing:
            self._show_warning('提示', '正在解析歌单，请稍后再试')
            return
        if not self.search_in_progress:
            self.start_search()
        else:
            self.stop_search()

    def start_search(self):
        selected_display = self.settings.get('sources', [])
        if not selected_display:
            QMessageBox.warning(self, '警告', '请先在设置中选择搜索源')
            return
        selected_sources = []
        for display in selected_display:
            internal = SOURCE_INTERNAL.get(display)
            if internal:
                selected_sources.append(internal)
        if not selected_sources:
            QMessageBox.warning(self, '警告', '未找到有效的搜索源')
            return

        keyword = self.search_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, '警告', '请输入关键词')
            return

        limit = self.settings.get('limit', 5)
        dedup = self.settings.get('dedup', False)

        self.clear_results()

        init_cfg = {}
        for src in selected_sources:
            init_cfg[src] = {'search_size_per_source': limit}

        try:
            self.music_client = musicdl.MusicClient(
                music_sources=selected_sources,
                init_music_clients_cfg=init_cfg
            )
        except Exception as e:
            QMessageBox.critical(self, '初始化失败', f'无法创建音乐客户端：{str(e)}')
            return

        self.search_task_counter += 1
        self.current_search_task_id = self.search_task_counter

        self.label_stats.setText(f'⏳ 搜索中 (0/{len(selected_sources)}) ...')
        self._source_counts = {src: -1 for src in selected_sources}

        self._set_ui_enabled(False)
        self.btn_search_title.setText('⏹')
        self.btn_search_title.setToolTip('停止搜索')
        self.search_in_progress = True

        self.search_thread = SearchThread(
            self.music_client,
            selected_sources,
            keyword,
            limit,
            task_id=self.current_search_task_id,
            threadings_per_source=5,
        )
        self.search_thread.source_started.connect(self.on_source_started)
        self.search_thread.source_finished.connect(self.on_source_finished)
        self.search_thread.finished.connect(self.on_search_finished)
        self.search_thread.error.connect(self.on_search_error)
        self.search_thread.start()

    def stop_search(self):
        if self.search_in_progress:
            if self.search_thread and self.search_thread.isRunning():
                self.search_thread.stop()
                for sig in ['source_started', 'source_finished', 'error']:
                    try:
                        getattr(self.search_thread, sig).disconnect()
                    except TypeError:
                        pass
                try:
                    self.search_thread.finished.disconnect()
                except TypeError:
                    pass
                self.search_thread.finished.connect(self._on_search_thread_finished_cleanup)
            self.search_in_progress = False
            self._set_ui_enabled(True)
            self.btn_search_title.setEnabled(True)
            self.btn_search_title.setText('🔍')
            self.btn_search_title.setToolTip('搜索')
            self.label_stats.setText('⏹ 已停止搜索')
        else:
            self.finish_search()

    def _on_search_thread_finished_cleanup(self):
        if self.search_thread is None:
            return
        try:
            self.search_thread.finished.disconnect(self._on_search_thread_finished_cleanup)
        except TypeError:
            pass
        if self.search_thread and self.search_thread.isRunning():
            self.search_thread.wait()
        if self.search_thread:
            self.search_thread.deleteLater()
            self.search_thread = None
        self.search_in_progress = False
        self._set_ui_enabled(True)
        self.btn_search_title.setEnabled(True)
        self.btn_search_title.setText('🔍')
        self.btn_search_title.setToolTip('搜索')
        if self.result_list.count() == 0:
            self.label_stats.setText('已停止搜索')

    def _on_parse_thread_finished_cleanup(self):
        if self.parse_thread is None:
            return
        try:
            self.parse_thread.finished.disconnect(self._on_parse_thread_finished_cleanup)
        except TypeError:
            pass
        if self.parse_thread and self.parse_thread.isRunning():
            self.parse_thread.wait()
        if self.parse_thread:
            self.parse_thread.deleteLater()
            self.parse_thread = None
        self.is_parsing = False
        self._set_ui_enabled(True)
        self.button_parse_playlist.setEnabled(True)
        self.button_parse_playlist.setText('📋 解析歌单')
        if self.label_stats.text().startswith('⏹ 正在停止解析'):
            self.label_stats.setText('已停止解析')

    def _set_ui_enabled(self, enabled: bool):
        self.search_input.setEnabled(enabled)
        self.btn_search_title.setEnabled(True)
        self.button_parse_playlist.setEnabled(enabled)
        self.lineedit_playlist.setEnabled(enabled)
        self.combo_playlist_source.setEnabled(enabled)
        self.btn_settings.setEnabled(enabled)
        self.btn_about.setEnabled(enabled)
        self.action_download.setEnabled(enabled and not self.is_downloading)

    def finish_search(self):
        self.search_in_progress = False
        self.btn_search_title.setEnabled(True)
        self.btn_search_title.setText('🔍')
        self.btn_search_title.setToolTip('搜索')
        self._set_ui_enabled(True)
        if self.result_list.count() == 0:
            if not self.label_stats.text().startswith('❌'):
                self.label_stats.setText('❌ 未找到任何结果')

    def on_source_started(self, task_id: int, source_internal: str):
        if task_id != self.current_search_task_id:
            return
        display = self._internal_to_display(source_internal)
        self.label_stats.setText(f'⏳ 正在搜索 {display} ...')

    def on_source_finished(self, task_id, source_internal, results):
        if task_id != self.current_search_task_id:
            return
        display = self._internal_to_display(source_internal)
        count = len(results)
        self._source_counts[source_internal] = count

        dedup = self.settings.get('dedup', False)
        existing = set()
        if dedup:
            for i in range(self.result_list.count()):
                info = self.get_song_info_by_row(i)
                if info:
                    key = (info.get('singers', ''), info.get('song_name', ''))
                    existing.add(key)

        added = 0
        for info in results:
            if dedup:
                key = (info.get('singers', ''), info.get('song_name', ''))
                if key in existing:
                    continue
                existing.add(key)
            self.add_song_card(info, display)
            added += 1

        total = self.result_list.count()
        done = sum(1 for v in self._source_counts.values() if v >= 0)
        total_sources = len(self._source_counts)
        self.label_stats.setText(
            f'⏳ 已搜索 {done}/{total_sources} 个源，共 {total} 条结果（新增{added}条）'
        )

    def on_search_finished(self, task_id: int):
        if task_id != self.current_search_task_id:
            return
        self.search_in_progress = False
        self.finish_search()
        total = self.result_list.count()
        if total > 0:
            self.label_stats.setText(f'✅ 搜索完成，共 {total} 条结果')
        else:
            self.label_stats.setText('❌ 未搜索到任何结果')
        if self.search_thread:
            try:
                self.search_thread.finished.disconnect()
            except TypeError:
                pass
            self.search_thread.deleteLater()
            self.search_thread = None

    def on_search_error(self, task_id: int, error_msg: str):
        if task_id != self.current_search_task_id:
            return
        QMessageBox.warning(self, '搜索警告', error_msg)

    def _internal_to_display(self, internal: str) -> str:
        for k, v in SOURCE_INTERNAL.items():
            if v == internal:
                return k
        return internal

    def parse_playlist(self):
        if self.search_in_progress:
            self._show_warning('提示', '正在搜索中，请稍后再试')
            return
        if self.is_downloading:
            self._show_warning('提示', '正在下载中，请稍后再试')
            return
        if self.is_parsing:
            self.stop_parse()
            return

        playlist_url = self.lineedit_playlist.text().strip()
        if not playlist_url:
            QMessageBox.warning(self, '警告', '请先输入歌单链接')
            return

        source_display = self.combo_playlist_source.currentText()
        source_internal = PLAYLIST_SOURCE_MAP.get(source_display)
        if not source_internal:
            QMessageBox.warning(self, '警告', '请选择有效的歌单平台')
            return

        self.parse_task_counter += 1
        self.current_parse_task_id = self.parse_task_counter

        self._set_ui_enabled(False)
        self.button_parse_playlist.setEnabled(True)
        self.button_parse_playlist.setText('⏹ 停止')
        self.is_parsing = True
        self.label_stats.setText('⏳ 正在解析歌单...')

        self.parse_thread = PlaylistParseThread(
            playlist_url,
            source_internal,
            source_display,
            task_id=self.current_parse_task_id
        )
        self.parse_thread.parse_started.connect(self._on_parse_started)
        self.parse_thread.parse_finished.connect(self._on_parse_finished)
        self.parse_thread.parse_error.connect(self._on_parse_error)
        self.parse_thread.start()

    def stop_parse(self):
        if self.is_parsing:
            if self.parse_thread and self.parse_thread.isRunning():
                self.parse_thread.stop()
                for sig in ['parse_started', 'parse_finished', 'parse_error']:
                    try:
                        getattr(self.parse_thread, sig).disconnect()
                    except TypeError:
                        pass
                try:
                    self.parse_thread.finished.disconnect()
                except TypeError:
                    pass
                self.parse_thread.finished.connect(self._on_parse_thread_finished_cleanup)
            self.is_parsing = False
            self._set_ui_enabled(True)
            self.button_parse_playlist.setEnabled(True)
            self.button_parse_playlist.setText('📋 解析歌单')
            self.label_stats.setText('⏹ 已停止解析')
        else:
            self._restore_parse_ui()

    def _restore_parse_ui(self):
        self.is_parsing = False
        self._set_ui_enabled(True)
        self.button_parse_playlist.setEnabled(True)
        self.button_parse_playlist.setText('📋 解析歌单')
        if self.label_stats.text().startswith('⏹ 正在停止解析'):
            self.label_stats.setText('已停止解析')

    def _on_parse_started(self, task_id: int):
        if task_id != self.current_parse_task_id:
            return
        self.label_stats.setText('⏳ 正在解析歌单...')

    def _on_parse_finished(self, task_id, song_infos, source_display):
        if task_id != self.current_parse_task_id:
            return
        for info in song_infos:
            self.add_song_card(info, source_display)
        self.label_stats.setText(f'✅ 歌单解析成功，共 {len(song_infos)} 首歌曲')
        self._restore_parse_ui()
        if self.parse_thread:
            self.parse_thread.deleteLater()
            self.parse_thread = None

    def _on_parse_error(self, task_id: int, error_msg):
        if task_id != self.current_parse_task_id:
            return
        logger.error(f"歌单解析错误: {error_msg}")
        QMessageBox.critical(self, '解析失败', f'歌单解析出错：{error_msg}\n\n请确认链接格式正确且平台支持。')
        self.label_stats.setText('❌ 歌单解析失败')
        self._restore_parse_ui()
        if self.parse_thread:
            try:
                self.parse_thread.finished.disconnect()
            except TypeError:
                pass
            self.parse_thread.deleteLater()
            self.parse_thread = None

    def add_song_card(self, song_info, source_display=None):
        row = self.result_list.count()
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 110))
        card = SongCard(song_info, source_display, self.result_list)
        self.result_list.addItem(item)
        self.result_list.setItemWidget(item, card)
        self.music_records[str(row)] = song_info
        return row

    def get_selected_rows(self):
        rows = []
        for item in self.result_list.selectedItems():
            rows.append(self.result_list.row(item))
        return rows

    def get_song_info_by_row(self, row):
        return self.music_records.get(str(row))

    def on_selection_changed(self):
        for i in range(self.result_list.count()):
            item = self.result_list.item(i)
            card = self.result_list.itemWidget(item)
            if card:
                card.set_selected(item.isSelected())

    def clear_results(self):
        self.result_list.clear()
        self.music_records.clear()
        self.label_stats.setText('已清空')
        self._source_counts.clear()
        self.playlist = []
        self.current_play_index = -1
        self.stop_playback()

    def on_list_double_click(self, item):
        row = self.result_list.row(item)
        self.play_song_at_row(row)

    def show_context_menu(self, pos):
        if not self.is_downloading and self.result_list.count() > 0 and self.result_list.selectedItems():
            self.context_menu.exec_(self.result_list.mapToGlobal(pos))

    def download_selected(self):
        if self.is_downloading:
            QMessageBox.information(self, '提示', '正在下载中，请稍候...')
            return

        selected_rows = set(self.get_selected_rows())
        if not selected_rows:
            QMessageBox.warning(self, '警告', '请先选择至少一首歌曲')
            return

        songs_to_download = []
        for row in sorted(selected_rows):
            row_key = str(row)
            info = self.music_records.get(row_key)
            if info and info.get('download_url'):
                songs_to_download.append(info)
            else:
                QMessageBox.warning(self, '警告', f'第 {row+1} 首歌曲无有效下载链接，已跳过')

        if not songs_to_download:
            return

        save_dir = self.settings['save_dir']
        if not save_dir:
            QMessageBox.warning(self, '警告', '请选择有效的保存路径')
            return
        if not os.path.exists(save_dir):
            try:
                os.makedirs(save_dir)
            except Exception as e:
                QMessageBox.critical(self, '错误', f'无法创建目录：{str(e)}')
                return
        self._download_cancelled = False

        fmt = self._get_filename_template()
        dl_lyric = self.settings['download_lyric']
        dl_cover = self.settings['download_cover']

        self.is_downloading = True
        self._set_ui_enabled(False)
        self.result_list.setEnabled(False)
        self.action_download.setEnabled(False)

        self._download_queue = songs_to_download.copy()
        self._download_current_index = 0
        self._total_to_download = len(songs_to_download)
        self._downloaded_files.clear()

        self.bar_overall.setMaximum(self._total_to_download)
        self.bar_overall.setValue(0)
        self.bar_download.setValue(0)

        self._start_next_download()

    def cancel_all_downloads(self):
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.stop()
            try:
                self.download_thread.progress.disconnect()
                self.download_thread.finished.disconnect()
                self.download_thread.error.disconnect()
            except TypeError:
                pass

        self._download_cancelled = True
        self._download_queue.clear()
        files_to_delete = self._downloaded_files.copy()

        def do_cleanup():
            for f in files_to_delete:
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except Exception as e:
                    logger.error(f"删除文件失败 {f}: {e}")
            for f in files_to_delete:
                base = os.path.splitext(f)[0]
                try:
                    for pattern in [base + '.lrc'] + glob.glob(base + '_cover.*'):
                        try:
                            if os.path.exists(pattern):
                                os.remove(pattern)
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(f"清理关联文件时出错: {e}")
            self._downloaded_files.clear()
            self._on_all_downloads_finished(cancelled=True)

        QTimer.singleShot(500, do_cleanup)

    def _get_request_kwargs_for_source(self, source: str) -> Dict:
        kwargs = {
            'headers': {},
            'cookies': {},
            'proxies': {},
            'timeout': 30,
            'verify': True
        }
        if self.music_client:
            client = self.music_client.music_clients.get(source)
            if client:
                for attr in ('default_download_headers', 'default_headers', 'default_search_headers', 'default_parse_headers'):
                    if hasattr(client, attr):
                        kwargs['headers'].update(getattr(client, attr) or {})
                for attr in ('default_download_cookies', 'default_cookies', 'default_search_cookies', 'default_parse_cookies'):
                    if hasattr(client, attr):
                        kwargs['cookies'].update(getattr(client, attr) or {})
        return kwargs

    def _start_next_download(self):
        if self._download_cancelled:
            return
        if self._download_current_index >= len(self._download_queue):
            self._on_all_downloads_finished()
            return

        song_info = self._download_queue[self._download_current_index]
        self.bar_download.setValue(0)
        self.label_stats.setText(
            f'⏳ 下载中 ({self._download_current_index+1}/{self._total_to_download}) ...'
        )

        self.download_thread = DownloadThread(
            song_info,
            self._get_request_kwargs_for_source,
            self.settings['save_dir'],
            self._get_filename_template(),
            self.settings['download_lyric'],
            self.settings['download_cover']
        )
        self.download_thread.progress.connect(self.bar_download.setValue)
        self.download_thread.finished.connect(self._on_single_download_finished)
        self.download_thread.error.connect(self._on_single_download_error)
        self.download_thread.start()

    def _on_single_download_finished(self, song_name, singers, file_path):
        self._download_current_index += 1
        self.bar_overall.setValue(self._download_current_index)
        self.label_stats.setText(
            f'✅ 已完成 {self._download_current_index}/{self._total_to_download}: {song_name} - {singers}'
        )
        logger.info(f"下载成功: {song_name} - {singers} -> {file_path}")

        self._downloaded_files.append(file_path)
        base = os.path.splitext(file_path)[0]
        for pattern in [base + '.lrc'] + glob.glob(base + '_cover.*'):
            if os.path.exists(pattern):
                self._downloaded_files.append(pattern)

        self._start_next_download()

    def _on_single_download_error(self, error_msg):
        if self._download_cancelled:
            return
        QMessageBox.critical(self, '下载错误', f'下载失败：{error_msg}')
        self._download_current_index += 1
        self.bar_overall.setValue(self._download_current_index)
        self._start_next_download()

    def _on_all_downloads_finished(self, cancelled=False):
        self.is_downloading = False
        self._download_cancelled = False
        self._set_ui_enabled(True)
        self.result_list.setEnabled(True)
        self.action_download.setEnabled(True)
        if not cancelled:
            self.bar_download.setValue(0)
            self.bar_overall.setValue(self._total_to_download)
            self.label_stats.setText(f'✅ 所有下载任务已完成 ({self._total_to_download} 首)')
            QMessageBox.information(self, '下载完成',
                                    f'全部 {self._total_to_download} 首歌曲下载完毕。')
        else:
            self.bar_download.setValue(0)
            self.bar_overall.setValue(0)
            self.label_stats.setText('❌ 下载已取消')
            QMessageBox.information(self, '取消', '所有下载任务已取消。')
        if self.download_thread:
            try:
                self.download_thread.deleteLater()
            except Exception:
                pass
            self.download_thread = None
        self._download_queue = []
        self._downloaded_files.clear()
        self._adjusting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.pos()
            if pos.x() >= self.width() - 15 and pos.y() >= self.height() - 15:
                self._resizing = True
                self._resize_start_pos = event.globalPos()
                self._resize_start_geo = self.geometry()
                event.accept()
                return
            if hasattr(self, 'title_bar') and self.title_bar.geometry().contains(pos):
                self.drag_pos = event.globalPos()
                self.dragging = True
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            delta = event.globalPos() - self._resize_start_pos
            new_width = max(self.minimumWidth(), self._resize_start_geo.width() + delta.x())
            new_height = max(self.minimumHeight(), self._resize_start_geo.height() + delta.y())
            self.resize(new_width, new_height)
            event.accept()
            return
        if hasattr(self, 'dragging') and self.dragging:
            delta = event.globalPos() - self.drag_pos
            self.move(self.pos() + delta)
            self.drag_pos = event.globalPos()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resizing = False
            event.accept()
            return
        if hasattr(self, 'dragging') and self.dragging:
            self.dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if hasattr(self, 'title_bar') and self.title_bar.geometry().contains(event.pos()):
            self.toggle_maximize()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            self.btn_maximize.setText("□")
        else:
            self.showMaximized()
            self.btn_maximize.setText("❐")

    def closeEvent(self, event):
        if hasattr(self, '_vis_download_thread') and self._vis_download_thread is not None:
            if self._vis_download_thread.isRunning():
                self._vis_download_thread.stop()
                self._vis_download_thread.wait()
            self._vis_download_thread = None

        if self.search_thread is not None:
            try:
                if self.search_thread.isRunning():
                    self.search_thread.stop()
            except RuntimeError:
                pass

        if self.download_thread is not None:
            try:
                if self.download_thread.isRunning():
                    self.download_thread.stop()
            except RuntimeError:
                pass

        if self.parse_thread is not None:
            try:
                if self.parse_thread.isRunning():
                    self.parse_thread.stop()
            except RuntimeError:
                pass

        if self.player.state() != PlayerState.StoppedState:
            self.player.stop()
        self._cover_task_id += 1
        try:
            if hasattr(self, '_requests_session') and self._requests_session:
                self._requests_session.close()
        except Exception:
            pass
        event.accept()

    def _show_warning(self, title: str, text: str):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.NoIcon)
        msg.setWindowTitle(title)
        msg.setText(text)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec_()

    def show_visualization(self):
        if self.current_play_index < 0 or not self.playlist:
            QMessageBox.information(self, "提示", "请先播放一首歌曲")
            return

        song_info = self.playlist[self.current_play_index]
        base_name = self._get_base_name_for_song(song_info, "{歌手}-{歌曲名}")
        ext = song_info.get('ext', 'mp3')
        save_dir = self.settings['save_dir']
        pattern = os.path.join(save_dir, f"{base_name}*{ext}")
        matches = glob.glob(pattern)
        audio_file = None
        for f in matches:
            if '_cover' not in f and '.lrc' not in f:
                audio_file = f
                break
        if not audio_file and matches:
            audio_file = matches[0]

        if audio_file and os.path.exists(audio_file):
            self._open_visualization(audio_file, song_info)
        else:
            reply = QMessageBox.question(self, "文件未下载",
                                         "当前歌曲尚未下载到本地，是否立即下载？",
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self._download_and_visualize(song_info)

    def _open_visualization(self, audio_file, song_info):
        if hasattr(self, 'vis_window') and self.vis_window is not None:
            try:
                self.vis_window.close()
            except RuntimeError:
                pass
            self.vis_window = None

        if self.player.state() == PlayerState.PlayingState:
            self.player.pause()

        base, _ = os.path.splitext(audio_file)
        lyric_file = base + '.lrc'
        cover_file = None
        cover_pattern = base + '_cover.*'
        covers = glob.glob(cover_pattern)
        if covers:
            cover_file = covers[0]

        self.vis_window = AudioVisualizer(
            audio_file,
            lyric_file if os.path.exists(lyric_file) else None,
            cover_file if cover_file and os.path.exists(cover_file) else None,
            parent=self
        )
        self.vis_window.destroyed.connect(self._on_vis_window_destroyed)
        self.vis_window.show()

    def _on_vis_window_destroyed(self):
        self.vis_window = None

    def _download_and_visualize(self, song_info):
        save_dir = self.settings['save_dir']
        fmt = "{歌手}-{歌曲名}"
        dl_lyric = True
        dl_cover = True

        if hasattr(self, '_vis_download_thread') and self._vis_download_thread is not None:
            if self._vis_download_thread.isRunning():
                self._vis_download_thread.stop()
                self._vis_download_thread.wait()
            self._vis_download_thread = None

        progress = QProgressDialog("正在下载歌曲...", "取消", 0, 100, self)
        progress.setWindowTitle("下载进度")
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        self._vis_download_thread = DownloadThread(
            song_info,
            self._get_request_kwargs_for_source,
            save_dir,
            fmt,
            dl_lyric,
            dl_cover
        )
        self._vis_download_thread.progress.connect(progress.setValue)
        self._vis_download_thread.finished.connect(
            lambda name, singer, path: self._on_vis_download_finished(name, singer, path, progress)
        )
        self._vis_download_thread.error.connect(
            lambda err: self._on_vis_download_error(err, progress)
        )
        progress.canceled.connect(self._vis_download_thread.stop)

        self._vis_download_thread.start()
        self.label_stats.setText("⏳ 正在下载当前歌曲以用于可视化...")
        self.btn_visualize.setEnabled(False)

    def _on_vis_download_finished(self, song_name, singers, file_path, progress):
        progress.setValue(100)
        progress.close()
        self.btn_visualize.setEnabled(True)
        self.label_stats.setText(f"下载完成：{song_name} - {singers}")
        song_info = self.playlist[self.current_play_index] if self.current_play_index >= 0 else None
        if song_info:
            self._open_visualization(file_path, song_info)
        else:
            QMessageBox.warning(self, "错误", "无法获取歌曲信息")
        self._vis_download_thread = None

    def _on_vis_download_error(self, error_msg, progress):
        progress.close()
        self.btn_visualize.setEnabled(True)
        QMessageBox.critical(self, "下载失败", f"下载可视化所需文件失败：{error_msg}")
        self._vis_download_thread = None

    def _on_cover_loaded(self, payload):
        try:
            if isinstance(payload, tuple) and len(payload) == 2:
                img_data, task_id = payload
                if task_id != self._cover_task_id:
                    logger.debug("收到过期的封面任务结果，忽略")
                    return
            else:
                img_data = payload
            if not img_data:
                self.cover_label.setText("🎵")
                self.cover_label.setPixmap(QPixmap())
                return
            pixmap = QPixmap()
            if pixmap.loadFromData(img_data):
                scaled = pixmap.scaled(120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.cover_label.setPixmap(scaled)
            else:
                self.cover_label.setText("🎵")
                self.cover_label.setPixmap(QPixmap())
        except Exception as e:
            logger.error(f"加载封面失败: {e}", exc_info=True)
            self.cover_label.setText("🎵")
            self.cover_label.setPixmap(QPixmap())

    def _fetch_cover_async(self, url: str, request_kwargs: Dict):
        self._cover_task_id += 1
        task_id = self._cover_task_id
        self._last_cover_runnable = CoverRunnable(url, request_kwargs, task_id, session=self._requests_session)
        self._last_cover_runnable.signals.finished.connect(self._on_cover_loaded)
        QThreadPool.globalInstance().start(self._last_cover_runnable)

    def toggle_playback(self):
        if not self.playlist and self.player.state() == PlayerState.StoppedState:
            QMessageBox.information(self, "提示", "播放列表为空，请先选择歌曲播放。")
            return

        state = self.player.state()
        if state == PlayerState.PlayingState:
            self.player.pause()
        elif state == PlayerState.PausedState:
            self.player.play(volume=self.slider_volume.value())
        else:
            if not self.playlist:
                return
            self.player.setPosition(0)
            self.player.play(volume=self.slider_volume.value())

    def stop_playback(self):
        self.player.stop()
        self.player.setPosition(0)
        self.slider_position.setValue(0)
        self.label_time.setText("00:00 / 00:00")
        self.now_playing_label.setText("未播放")
        self.clear_lyric_display()
        self.cover_label.setText("🎵")
        self.cover_label.setPixmap(QPixmap())
        self._cover_task_id += 1
        self._last_cover_runnable = None
        self.playlist.clear()
        self.current_play_index = -1

    def set_position(self, pos):
        self.player.setPosition(pos)

    def set_volume(self, vol):
        self.player.setVolume(vol)

    def update_position(self, pos):
        self.slider_position.setValue(pos)
        total = self.player.duration()
        if total > 0:
            self.label_time.setText(f"{self._format_time(pos)} / {self._format_time(total)}")
        else:
            self.label_time.setText(f"{self._format_time(pos)} / 00:00")

    def update_duration(self, duration):
        self.slider_position.setRange(0, duration)

    def update_play_button(self, state: PlayerState):
        if state == PlayerState.PlayingState:
            self.btn_play.setText("⏸")
        else:
            self.btn_play.setText("▶")

    def handle_media_status(self, status: PlayerMediaStatus):
        if status == PlayerMediaStatus.InvalidMedia:
            QMessageBox.warning(
                self, "播放失败",
                "无法播放该歌曲，可能原因：\n"
                "• 格式不被系统解码器支持（如 FLAC）\n"
                "• 链接需要特定的 HTTP 请求头（如 Referer）\n\n"
                "建议使用「下载」功能保存到本地后播放。"
            )
            self.now_playing_label.setText("播放失败")
            self.player.reset()
        elif status == PlayerMediaStatus.EndOfMedia:
            self._on_playback_ended()

    def _format_time(self, ms):
        s = ms // 1000
        m, s = divmod(s, 60)
        return f"{m:02d}:{s:02d}"

    def on_playmode_changed(self, index):
        self.play_mode = PlayMode(index)

    def _on_playback_ended(self):
        if not self.playlist or self.current_play_index < 0 or self.current_play_index >= len(self.playlist):
            return
        mode = self.play_mode
        if mode == PlayMode.SingleRepeat:
            self.play_current()
        elif mode == PlayMode.SingleStop:
            self.stop_playback()
            self.now_playing_label.setText("播放结束")
        elif mode == PlayMode.ListRepeat:
            next_idx = self.current_play_index + 1
            if next_idx >= len(self.playlist):
                next_idx = 0
            self.current_play_index = next_idx
            self.play_current()
        elif mode == PlayMode.ListStop:
            next_idx = self.current_play_index + 1
            if next_idx >= len(self.playlist):
                self.stop_playback()
                self.now_playing_label.setText("列表播放结束")
            else:
                self.current_play_index = next_idx
                self.play_current()

    def play_song_at_row(self, row):
        playlist = []
        for i in range(self.result_list.count()):
            info = self.get_song_info_by_row(i)
            if info:
                playlist.append(info)
        if not playlist:
            return
        self.playlist = playlist
        if row < 0 or row >= len(playlist):
            row = 0
        self.current_play_index = row
        self.play_current()

    def play_current(self):
        if not self.playlist or self.current_play_index < 0 or self.current_play_index >= len(self.playlist):
            return
        song_info = self.playlist[self.current_play_index]
        url = song_info.get('download_url')
        if not url:
            QMessageBox.warning(self, "无法播放", "该歌曲没有可用的播放链接。")
            return

        self.player.stop()
        source = song_info.get('source', '')
        req_kwargs = self._get_request_kwargs_for_source(source)
        headers = req_kwargs.get('headers') or {}
        self.player.setMedia(url, headers=headers)

        singer = song_info.get('singers', '')
        name = song_info.get('song_name', '')
        self.now_playing_label.setText(f"🎵 {singer} - {name}")

        lyric_text = song_info.get('lyric') or song_info.get('lyrics', '')
        if lyric_text:
            self.current_lyrics = self.parse_lrc(lyric_text)
        else:
            self.current_lyrics = []
        self.current_lyric_index = -1
        self.lyric_display.clear()
        
        self.update_lyric_display(0)

        self.player.play(volume=self.slider_volume.value())

        cover_url = get_cover_url(song_info)
        if cover_url:
            req_kwargs = self._get_request_kwargs_for_source(source)
            QTimer.singleShot(300, lambda: self._fetch_cover_async(cover_url, req_kwargs))
        else:
            self.cover_label.setText("🎵")
            self.cover_label.setPixmap(QPixmap())

    def play_prev(self):
        if not self.playlist:
            return
        if self.current_play_index <= 0:
            self.current_play_index = len(self.playlist) - 1
        else:
            self.current_play_index -= 1
        self.play_current()

    def play_next(self):
        if not self.playlist:
            return
        if self.current_play_index >= len(self.playlist) - 1:
            self.current_play_index = 0
        else:
            self.current_play_index += 1
        self.play_current()

    def on_table_double_click(self, index):
        row = index.row()
        self.play_song_at_row(row)

    def parse_lrc(self, text: str) -> List[Tuple[int, str]]:
        lyrics = []
        pattern = r'\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)'
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            match = re.match(pattern, line)
            if match:
                min_val = int(match.group(1))
                sec_val = int(match.group(2))
                ms_str = match.group(3)
                ms = int(ms_str) * 10 if len(ms_str) == 2 else int(ms_str)
                time_ms = min_val * 60000 + sec_val * 1000 + ms
                content = match.group(4).strip()
                lyrics.append((time_ms, content))
        lyrics.sort(key=lambda x: x[0])
        return lyrics

    def on_lyric_clicked(self, item):
        """点击歌词跳转到对应时间"""
        if not self.current_lyrics:
            return
        row = self.lyric_display.row(item)
        if row < 0 or row >= len(self.current_lyrics):
            return
        time_ms, _ = self.current_lyrics[row]
        
        state = self.player.state()
        if state == PlayerState.StoppedState:
            return
        
        # 设置播放位置（毫秒）
        self.player.setPosition(time_ms)
        if state == PlayerState.PausedState:
            self.player.play(volume=self.slider_volume.value())

    def update_lyric_display(self, pos_ms: int):
        if not self.current_lyrics:
            if self.lyric_display.count() == 0 or self.lyric_display.item(0).text() != "暂无歌词":
                self.lyric_display.clear()
                self.lyric_display.addItem("暂无歌词")
                self.current_lyric_index = -1
            return

        # 首次填充歌词
        if self.lyric_display.count() == 0:
            self.lyric_display.clear()
            for _, text in self.current_lyrics:
                self.lyric_display.addItem(text)
            self.current_lyric_index = -1

        # 查找当前时间对应的歌词索引
        new_idx = -1
        for i, (t, _) in enumerate(self.current_lyrics):
            if t <= pos_ms:
                new_idx = i
            else:
                break

        if new_idx == self.current_lyric_index:
            return

        # 重置旧高亮
        if self.current_lyric_index != -1 and self.current_lyric_index < self.lyric_display.count():
            old_item = self.lyric_display.item(self.current_lyric_index)
            old_item.setBackground(QColor(0, 0, 0, 0))
            old_item.setForeground(QColor(44, 62, 80))
            f = old_item.font()
            f.setBold(False)
            f.setPointSize(10)
            old_item.setFont(f)

        self.current_lyric_index = new_idx
        if new_idx != -1 and new_idx < self.lyric_display.count():
            new_item = self.lyric_display.item(new_idx)
            new_item.setBackground(QColor(74, 144, 217, 80))
            new_item.setForeground(QColor(0, 0, 0))
            f = new_item.font()
            f.setBold(True)
            f.setPointSize(14)
            new_item.setFont(f)

            QTimer.singleShot(10, lambda: self.lyric_display.scrollToItem(
                new_item, QAbstractItemView.PositionAtCenter
            ))

    def clear_lyric_display(self):
        self.current_lyrics = []
        self.current_lyric_index = -1
        self.lyric_display.clear()
        self.lyric_display.addItem("停止播放")

    def _get_filename_template(self) -> str:
        fmt = self.settings['filename_format']
        if fmt == '自定义':
            template = self.settings.get('custom_format', '').strip()
            if not template:
                template = '{歌手}-{歌曲名}'
            return template
        return fmt

    def _get_base_name_for_song(self, song_info: Dict, fmt: str = None) -> str:
        if fmt is None:
            fmt = self._get_filename_template()
        song_name = song_info.get('song_name', '')
        singers = song_info.get('singers', '')
        if fmt == "歌曲名":
            base = song_name
        elif fmt == "歌手-歌曲名":
            base = f"{singers}-{song_name}"
        elif fmt == "歌曲名-歌手":
            base = f"{song_name}-{singers}"
        else:
            base = fmt
            base = base.replace("{歌手}", singers)
            base = base.replace("{歌曲名}", song_name)
            base = base.replace("{专辑}", song_info.get('album', ''))
            base = base.replace("{时长}", song_info.get('duration', ''))
        return sanitize_filepath(base)


# ==================== 主入口 ====================
if __name__ == '__main__':
    if sys.platform == 'darwin':
        try:
            test_file = os.path.join(os.getcwd(), '.write_test')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
        except OSError:
            os.chdir(os.path.expanduser("~"))
    
    app = QApplication(sys.argv)
    font = QFont("Microsoft YaHei", 10)
    if sys.platform == 'darwin':
        font.setFamily("PingFang SC")
    app.setFont(font)
    gui = MusicdlGUI()
    gui.show()
    sys.exit(app.exec_())
