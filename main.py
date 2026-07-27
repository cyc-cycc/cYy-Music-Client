# -*- coding: utf-8 -*-
import os
import sys
import re
import glob
import logging
import traceback
import time
import json
import base64
import hashlib
from cryptography.fernet import Fernet
from typing import Dict, List, Optional, Tuple
from pathlib import Path

REFRESH_SEARCH_SIZE = 2
# ===== 加密常量（固定密钥，可配置） =====
ENCRYPTION_PASSWORD = "cYy4_Music3_Client0_playlist_PASSWORD"

# ===== 先设置运行时路径，再导入可能依赖 VLC 的模块 =====
from utils import setup_runtime_paths
from utils import get_global_stylesheet
setup_runtime_paths()

# ===== 导入常量 =====
from constants import (
    SOURCE_GROUPS, SOURCE_INTERNAL, FILENAME_FORMATS, PLAYLIST_SOURCE_MAP,
    APP_DIR, DATA_DIR, LOG_DIR, LOG_FILE, DEFAULT_SAVE_DIR,
    PlayerState, PlayerMediaStatus, PlayMode
)

# ===== 导入工具函数 =====
from utils import logger, get_cover_url, sanitize_filepath, download_cover_image, build_filename, safe_stop_thread

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
    QRectF, QUrl, QSize, QThreadPool
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

# ==================== 配置管理 ====================
def get_config_dir():
    """获取适合当前平台的用户配置目录"""
    if sys.platform == 'win32':
        return Path(DATA_DIR) / '.CMC'
    elif sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / '.CMC'
    else:
        return Path.home() / '.config' / '.CMC'

CONFIG_DIR = get_config_dir()
CONFIG_FILE = CONFIG_DIR / 'config.json'
DEFAULT_SETTINGS = {
    'sources': ['酷我音乐(普通无损,推荐)'],
    'limit': 10,
    'dedup': False,
    'save_dir': DEFAULT_SAVE_DIR,
    'filename_format': '歌手-歌曲名',
    'custom_format': '',
    'download_lyric': True,
    'download_cover': True,
    'volume': 60,
    'play_mode': 2,  # ListRepeat
    'convert_enabled': False,
    'convert_format': 'mp3',
    'convert_bitrate': '320k',
}

def load_settings() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 合并默认值，防止新增字段缺失
                for k, v in DEFAULT_SETTINGS.items():
                    if k not in data:
                        data[k] = v
                return data
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
    return DEFAULT_SETTINGS.copy()

def save_settings(settings: dict):
    try:
        CONFIG_DIR.mkdir(exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存配置失败: {e}")

# ==================== 主窗口 ====================
class MusicdlGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setObjectName("musicdlGUI")
        self.setWindowTitle('🎵 音乐下载器 cYy edit')
        self.setMinimumSize(1200, 800)
        self.resize(1200, 800)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # ---- 加载配置 ----
        self.settings = load_settings()
        self._apply_volume_from_settings()

        self.playlist = []
        self.current_play_index = -1
        self.play_mode = PlayMode(self.settings.get('play_mode', 2))

        self.setup_title_bar()
        self._init_ui()
        self._signals_inited = False
        self._init_signals()
        self._init_state()
        self._init_player()
        self.update_playlist_widget()

        self._requests_session = requests.Session()
        self._requests_session.verify = True

        self._resizing = False
        self._resize_start_pos = QPoint()
        self._resize_start_geo = QRect()

        self._download_cancelled = False

        # ---- 初始化 MusicClient（单例） ----
        self.music_client = None
        self._init_music_client()

        self.parse_progress = None
        QTimer.singleShot(100, self._check_deps)

        self._url_cache = {}  # identifier -> (url, timestamp)
        # ---- 封面线程池（改进3） ----
        self.cover_pool = QThreadPool.globalInstance()
        self.cover_pool.setMaxThreadCount(10)

        # ---- 下载并发控制（改进5 & 12） ----
        self.download_concurrency = 3          # 可配置
        self.active_downloads = []             # 活跃下载线程
        self.download_queue = []               # 待下载列表
        self.download_completed = 0            # 已完成数
        self.download_start_time = None
        self.download_total_bytes = 0
        self.download_done_bytes = 0
        self.download_eta_label = None         # 可后续添加到状态栏
        self._cache_ttl = 300  # 5分钟

    def _apply_volume_from_settings(self):
        vol = self.settings.get('volume', 60)
        if hasattr(self, 'slider_volume'):
            self.slider_volume.setValue(vol)

    def _check_deps(self):
        from utils import check_dependencies
        deps = check_dependencies()
        if not deps['vlc']:
            msg = (
                "未检测到 VLC 媒体播放器。\n\n"
                "播放功能需要 VLC。\n"
                "建议使用 Homebrew 安装：\n"
                "   brew install vlc\n"
                "或者从官网下载:\n"
                "   https://www.videolan.org/vlc/download-macosx.html\n\n"
                "安装后请重启应用。\n\n"
                "是否继续？（播放相关功能将被禁用）"
            )
            reply = QMessageBox.question(
                self, "VLC 缺失",
                msg,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.No:
                self.close()
                return

            self.btn_play.setEnabled(False)
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.slider_position.setEnabled(False)
            self.label_time.setText("VLC 不可用")

    # ---------- 标题栏 ----------
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
                base_dir = os.path.dirname(os.path.abspath(__file__))
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
        title_label.setStyleSheet("color: #2C3E50; font-weight: bold; font-size: 14px;")
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

    # ---------- UI 初始化 ----------
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

        # 歌单解析行
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

        # 结果工具栏
        result_toolbar = QHBoxLayout()
        result_toolbar.setContentsMargins(0, 0, 0, 0)
        self.btn_clear_results = QPushButton("🗑 清空结果")
        self.btn_clear_results.clicked.connect(self.clear_results)
        result_toolbar.addStretch()
        result_toolbar.addWidget(self.btn_clear_results)
        content_layout.addLayout(result_toolbar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(3)

        self.result_list = QListWidget()
        self.result_list.setObjectName("resultList")
        self.result_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.result_list.setSpacing(5)
        self.result_list.setMinimumWidth(400)
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

        # 右侧播放控制组
        play_group = QGroupBox("播放控制")
        play_group.setObjectName("playGroup")
        play_layout = QVBoxLayout(play_group)

        # ---- 播放列表面板 ----
        playlist_container = QWidget()
        playlist_container.setObjectName("playlistContainer")
        playlist_container.setMinimumHeight(120)
        playlist_container.setStyleSheet("background: transparent; border: none;")

        playlist_vbox = QVBoxLayout(playlist_container)
        playlist_vbox.setContentsMargins(5, 5, 5, 5)
        playlist_vbox.setSpacing(3)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        self.playlist_title = QLabel("🎵 播放列表 (0)")
        self.playlist_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #2C3E50;")
        header_layout.addWidget(self.playlist_title)
        header_layout.addStretch()

        button_style = """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                color: #2C3E50;
                padding: 2px;
            }
            QPushButton:hover {
                background: rgba(74, 144, 217, 0.2);
            }
            QPushButton:pressed {
                background: rgba(74, 144, 217, 0.4);
            }
        """

        self.btn_add_playlist = QPushButton("➕")
        self.btn_add_playlist.setToolTip("将选中的搜索结果添加到歌单")
        self.btn_add_playlist.setFixedSize(28, 28)
        self.btn_add_playlist.setStyleSheet(button_style)
        self.btn_add_playlist.clicked.connect(self.add_selected_to_playlist)
        header_layout.addWidget(self.btn_add_playlist)

        self.btn_save_playlist = QPushButton("💾")
        self.btn_save_playlist.setToolTip("保存歌单为 JSON")
        self.btn_save_playlist.setFixedSize(28, 28)
        self.btn_save_playlist.setStyleSheet(button_style)
        self.btn_save_playlist.clicked.connect(self.save_playlist)
        header_layout.addWidget(self.btn_save_playlist)

        self.btn_load_playlist = QPushButton("📂")
        self.btn_load_playlist.setToolTip("从 JSON 加载歌单")
        self.btn_load_playlist.setFixedSize(28, 28)
        self.btn_load_playlist.setStyleSheet(button_style)
        self.btn_load_playlist.clicked.connect(self.load_playlist)
        header_layout.addWidget(self.btn_load_playlist)

        playlist_vbox.addLayout(header_layout)

        self.playlist_widget = QListWidget()
        self.playlist_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.playlist_widget.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: 1px solid rgba(0,0,0,0.1);
                border-radius: 4px;
                outline: none;
            }
            QListWidget::item {
                padding: 4px;
                border-bottom: 1px solid rgba(0,0,0,0.05);
                color: #2C3E50;
            }
            QListWidget::item:selected {
                background: rgba(74, 144, 217, 0.3);
                color: #2C3E50;
            }
            QListWidget::item:hover {
                background: rgba(74, 144, 217, 0.1);
            }
        """)
        self.playlist_widget.itemDoubleClicked.connect(self.play_playlist_item)
        self.playlist_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.playlist_widget.customContextMenuRequested.connect(self.show_playlist_context_menu)
        playlist_vbox.addWidget(self.playlist_widget)

        self.playlist_widget.setDragDropMode(QListWidget.InternalMove)
        self.playlist_widget.setDefaultDropAction(Qt.MoveAction)
        self.playlist_widget.model().rowsMoved.connect(self._on_playlist_rows_moved)

        # ---- 顶部：封面 + 当前歌曲 ----
        top_layout = QHBoxLayout()
        top_layout.setSpacing(10)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(80, 80)
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

        # ---- 歌词 + 播放列表（垂直分割） ----
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
                background: rgba(74,144,217,0.15);
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

        splitter_vertical = QSplitter(Qt.Vertical)
        splitter_vertical.setHandleWidth(3)
        splitter_vertical.setStyleSheet("""
            QSplitter::handle {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                            stop:0 #D5D8DC, stop:1 #BDC3C7);
                border: none;
                height: 5px;
            }
            QSplitter::handle:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                            stop:0 #4A90D9, stop:1 #357ABD);
            }
        """)
        splitter_vertical.addWidget(self.lyric_display)
        splitter_vertical.addWidget(playlist_container)
        splitter_vertical.setStretchFactor(0, 3)
        splitter_vertical.setStretchFactor(1, 1)
        play_layout.addWidget(splitter_vertical)

        # ---- 进度条 ----
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

        # ---- 播放控制按钮 ----
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

        # ---- 第二行控制 ----
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
        self.slider_volume.setValue(self.settings.get('volume', 60))
        self.slider_volume.setFixedWidth(80)
        controls_row2.addWidget(self.slider_volume)

        controls_row2.addStretch()
        controls_row2.addWidget(QLabel("模式:"))
        self.combo_playmode = QComboBox()
        self.combo_playmode.addItems(["单曲循环", "单曲暂停", "列表循环", "列表暂停"])
        self.combo_playmode.setCurrentIndex(self.settings.get('play_mode', 2))
        self.combo_playmode.currentIndexChanged.connect(self.on_playmode_changed)
        controls_row2.addWidget(self.combo_playmode)

        play_layout.addLayout(controls_row2)
        splitter.addWidget(play_group)
        play_group.setMinimumWidth(300)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        content_layout.addWidget(splitter, 1)

        # ---- 进度条（下载） ----
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

        # ---- 右键菜单 ----
        self.context_menu = QMenu(self)
        self.action_download = self.context_menu.addAction('⬇️ 下载选中')
        self.action_download.setObjectName('downloadAction')
        self.action_download.triggered.connect(self.download_selected)
        self.action_add_to_playlist = self.context_menu.addAction('➕ 添加到歌单')
        self.action_add_to_playlist.triggered.connect(self.add_selected_to_playlist)

        main_layout.addWidget(content_widget)

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

    # ---------- MusicClient 单例管理 ----------
    def _init_music_client(self):
        """根据当前设置初始化 MusicClient"""
        selected_display = self.settings.get('sources', [])
        selected_sources = []
        for display in selected_display:
            internal = SOURCE_INTERNAL.get(display)
            if internal:
                selected_sources.append(internal)
        if not selected_sources:
            self.music_client = None
            return

        # 为每个源配置参数（开启会话复用）
        init_cfg = {}
        for src in selected_sources:
            init_cfg[src] = {
                'search_size_per_source': self.settings.get('limit', 10),
                'maintain_session': True,
                'disable_print': True,
                # 可继续添加: search_size_per_page, max_retries 等
            }

        try:
            self.music_client = musicdl.MusicClient(
                music_sources=selected_sources,
                init_music_clients_cfg=init_cfg,
                # 统一线程数
                clients_threadings={src: 5 for src in selected_sources}
            )
            logger.info(f"MusicClient 初始化成功，源: {selected_sources}")
        except Exception as e:
            logger.error(f"MusicClient 初始化失败: {e}", exc_info=True)
            QMessageBox.critical(self, "初始化失败", f"无法创建音乐客户端：{str(e)}")
            self.music_client = None

    # ---------- 刷新链接（核心） ----------
    def refresh_song_url(self, song_info: Dict) -> Optional[Dict]:
        """
        检查并刷新歌曲的 download_url。
        使用独立的临时客户端和固定条数（REFRESH_SEARCH_SIZE），
        完全不受全局设置控制。
        """
        identifier = song_info.get('identifier') or song_info.get('song_id')
        if not identifier:
            logger.warning("缺少 identifier，无法刷新链接")
            return None

        # 1. 检查缓存（5分钟有效）
        cached = self._url_cache.get(identifier)
        if cached:
            cached_url, cache_time = cached
            if time.time() - cache_time < self._cache_ttl:
                if cached_url != song_info.get('download_url'):
                    song_info['download_url'] = cached_url
                    logger.debug(f"使用缓存的链接: {identifier}")
                return song_info

        url = song_info.get('download_url', '')
        # 2. 快速 HEAD 验证（仅当链接不含明显过期关键词时）
        if url and 'expires' not in url and 'sign' not in url:
            try:
                head_resp = requests.head(url, timeout=3, allow_redirects=False)
                if head_resp.status_code < 400:
                    self._url_cache[identifier] = (url, time.time())
                    return song_info
            except Exception:
                pass  # 继续刷新

        # 3. 获取源并准备搜索
        source = song_info.get('source')
        if not source:
            logger.warning(f"缺少 source，无法刷新: {identifier}")
            return None

        keyword = f"{song_info.get('singers', '')} {song_info.get('song_name', '')}".strip()
        if not keyword:
            logger.warning(f"无关键词，无法刷新: {identifier}")
            return None

        # ===== 核心改动：始终创建临时客户端，使用 REFRESH_SEARCH_SIZE =====
        try:
            from musicdl import musicdl
            temp_client = musicdl.MusicClient(
                music_sources=[source],
                init_music_clients_cfg={
                    source: {
                        'search_size_per_source': REFRESH_SEARCH_SIZE,
                        'disable_print': True,
                        'maintain_session': False,  # 用完即弃
                    }
                }
            )
            client = temp_client.music_clients.get(source)
        except Exception as e:
            logger.error(f"创建临时客户端失败: {e}")
            return None

        if not client:
            return None

        try:
            results = client.search(keyword, num_threadings=1)
        except Exception as e:
            logger.error(f"刷新搜索失败: {e}")
            return None
        finally:
            # 清理临时客户端引用（帮助垃圾回收）
            temp_client = None

        # 匹配 identifier（只取前 REFRESH_SEARCH_SIZE 条，但实际搜索已限制）
        matched = None
        for item in results[:REFRESH_SEARCH_SIZE]:
            if item.get('identifier') == identifier:
                matched = item
                break
        if not matched and results:
            matched = results[0]  # 降级取第一条

        if not matched:
            logger.warning(f"未找到匹配歌曲: {identifier}")
            return None

        new_url = matched.get('download_url') or matched.get('url')
        if not new_url:
            return None

        # 更新 song_info 和缓存
        song_info['download_url'] = new_url
        if matched.get('cover_url'):
            song_info['cover_url'] = matched['cover_url']
        if matched.get('duration'):
            song_info['duration'] = matched['duration']
        if matched.get('lyric'):
            song_info['lyric'] = matched['lyric']
        if matched.get('ext'):
            song_info['ext'] = matched['ext']

        self._url_cache[identifier] = (new_url, time.time())
        logger.info(f"链接刷新成功: {identifier}")
        return song_info

    # ---------- 搜索功能 ----------
    def on_search_or_stop(self):
        if self.is_parsing:
            self._show_warning('提示', '正在解析歌单，请稍后再试')
            return
        if not self.search_in_progress:
            self.start_search()
        else:
            self.stop_search()

    def start_search(self):
        if not self.music_client:
            QMessageBox.warning(self, '警告', '请先在设置中选择搜索源')
            return

        keyword = self.search_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, '警告', '请输入关键词')
            return

        self.clear_results()

        self.search_task_counter += 1
        self.current_search_task_id = self.search_task_counter

        self.label_stats.setText('⏳ 搜索中...')
        self._source_counts = {}

        self._set_ui_enabled(False)
        self.btn_search_title.setText('⏹')
        self.btn_search_title.setToolTip('停止搜索')
        self.search_in_progress = True

        self.search_thread = SearchThread(
            self.music_client,
            keyword,
            task_id=self.current_search_task_id
        )
        self.search_thread.result_ready.connect(self._on_result_ready)   # 新增
        self.search_thread.source_done.connect(self._on_source_done)     # 替换原来的 source_finished
        self.search_thread.source_error.connect(self.on_search_error)
        self.search_thread.all_done.connect(self._on_search_all_done)    # 替换原来的 finished
        self.search_thread.start()

    def _on_result_ready(self, task_id, source, song_info):
        if task_id != self.current_search_task_id:
            return
        # 直接添加卡片，无需等待源完成
        display = self._internal_to_display(source)
        self.add_song_card(song_info, display)
        total = self.result_list.count()
        self.label_stats.setText(f'⏳ 搜索中... 已找到 {total} 首')

    def _on_source_done(self, task_id, source, count):
        if task_id != self.current_search_task_id:
            return
        display = self._internal_to_display(source)
        self.label_stats.setText(f'✅ {display} 完成，共 {count} 首')

    def _on_search_all_done(self, task_id):
        if task_id != self.current_search_task_id:
            return
        self.search_in_progress = False
        self.finish_search()
        total = self.result_list.count()
        self.label_stats.setText(f'✅ 搜索完成，共 {total} 条结果')
        if self.search_thread:
            self.search_thread.deleteLater()
            self.search_thread = None

    def stop_search(self):
        if self.search_in_progress:
            safe_stop_thread(self.search_thread, 
                             ['result_ready', 'source_done', 'source_error', 'all_done'],
                             self._on_search_thread_finished_cleanup)
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
            # 确保 info 包含 source 和 identifier
            if not info.get('source'):
                info['source'] = source_internal
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

    # ---------- 歌单解析 ----------
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

        if not self.music_client:
            QMessageBox.warning(self, '警告', '音乐客户端未初始化，请先设置搜索源')
            return

        # 检查该源是否已初始化
        if source_internal not in self.music_client.music_clients:
            reply = QMessageBox.question(
                self, "源未启用",
                f"当前设置未启用 {source_display}，是否临时启用并解析？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                # 临时加入源
                self._add_source_temp(source_internal)
            else:
                return

        self.parse_progress = QProgressDialog("正在解析歌单，请稍候...", "取消", 0, 0, self)
        self.parse_progress.setWindowTitle("歌单解析")
        self.parse_progress.setModal(True)
        self.parse_progress.setMinimumDuration(0)
        self.parse_progress.setAutoClose(False)
        self.parse_progress.setAutoReset(False)
        self.parse_progress.canceled.connect(self.stop_parse)

        self.parse_task_counter += 1
        self.current_parse_task_id = self.parse_task_counter

        self._set_ui_enabled(False)
        self.button_parse_playlist.setEnabled(True)
        self.button_parse_playlist.setText('⏹ 停止')
        self.is_parsing = True
        self.label_stats.setText('⏳ 正在解析歌单...')

        self.parse_thread = PlaylistParseThread(
            self.music_client,
            playlist_url,
            source_internal,
            source_display,
            task_id=self.current_parse_task_id
        )
        self.parse_thread.parse_started.connect(self._on_parse_started)
        self.parse_thread.parse_finished.connect(self._on_parse_finished)
        self.parse_thread.parse_error.connect(self._on_parse_error)
        self.parse_thread.start()

    def _add_source_temp(self, source_internal: str):
        """临时添加一个源到现有客户端（需要重建）"""
        current_sources = list(self.music_client.music_clients.keys())
        if source_internal not in current_sources:
            current_sources.append(source_internal)
            # 重建客户端
            display = self._internal_to_display(source_internal)
            if display not in self.settings['sources']:
                self.settings['sources'].append(display)
                save_settings(self.settings)
            self._init_music_client()

    def stop_parse(self):
        if self.is_parsing:
            if self.parse_progress:
                self.parse_progress.close()
                self.parse_progress = None
            safe_stop_thread(self.parse_thread, ['parse_started', 'parse_finished', 'parse_error'],
                             self._on_parse_thread_finished_cleanup)
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
        if self.parse_progress:
            self.parse_progress.close()
            self.parse_progress = None
        if self.label_stats.text().startswith('⏹ 正在停止解析'):
            self.label_stats.setText('已停止解析')

    def _on_parse_started(self, task_id: int):
        if task_id != self.current_parse_task_id:
            return
        self.label_stats.setText('⏳ 正在解析歌单...')

    def _on_parse_finished(self, task_id, song_infos, source_display):
        if task_id != self.current_parse_task_id:
            return
        if self.parse_progress:
            self.parse_progress.close()
            self.parse_progress = None
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
        if self.parse_progress:
            self.parse_progress.close()
            self.parse_progress = None
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
        if self.parse_progress:
            self.parse_progress.close()
            self.parse_progress = None
        if self.label_stats.text().startswith('⏹ 正在停止解析'):
            self.label_stats.setText('已停止解析')

    # ---------- 结果列表管理 ----------
    def add_song_card(self, song_info, source_display=None):
        # 确保 source 字段存在
        if not song_info.get('source') and source_display:
            song_info['source'] = SOURCE_INTERNAL.get(source_display)
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

    def on_list_double_click(self, item):
        row = self.result_list.row(item)
        info = self.get_song_info_by_row(row)
        if info:
            self.add_to_playlist(info, play=True)

    def show_context_menu(self, pos):
        if not self.is_downloading and self.result_list.count() > 0 and self.result_list.selectedItems():
            self.context_menu.exec_(self.result_list.mapToGlobal(pos))

    # ---------- 歌单管理 ----------
    def add_selected_to_playlist(self):
        rows = self.get_selected_rows()
        if not rows:
            QMessageBox.information(self, "提示", "请先在搜索结果中选择歌曲")
            return
        for row in rows:
            info = self.get_song_info_by_row(row)
            if info:
                self.add_to_playlist(info, play=False)
        self.update_playlist_widget()
        self.label_stats.setText(f"已添加 {len(rows)} 首歌曲到歌单")

    def add_to_playlist(self, song_info, play=False):
        if not song_info:
            return
        # 确保有 identifier（若无则用 song_id）
        if 'identifier' not in song_info and 'song_id' in song_info:
            song_info['identifier'] = song_info['song_id']
        self.playlist.append(song_info)
        if play:
            self.current_play_index = len(self.playlist) - 1
            self.play_current()
        self.update_playlist_widget()

    def update_playlist_widget(self):
        self.playlist_widget.clear()
        for idx, info in enumerate(self.playlist):
            name = info.get('song_name', '未知歌曲')
            singer = info.get('singers', '未知歌手')
            text = f"{idx+1}. {singer} - {name}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, idx)
            self.playlist_widget.addItem(item)
        count = len(self.playlist)
        self.playlist_title.setText(f"🎵 播放列表 ({count})")
        self.playlist_title.setToolTip(f"共 {count} 首歌曲")

    def play_playlist_item(self, item):
        idx = item.data(Qt.UserRole)
        if idx is not None and 0 <= idx < len(self.playlist):
            self.current_play_index = idx
            self.play_current()

    def show_playlist_context_menu(self, pos):
        menu = QMenu(self)
        play_action = menu.addAction("▶ 播放")
        remove_action = menu.addAction("❌ 移除选中")
        clear_action = menu.addAction("🗑 清空")
        menu.addSeparator()
        move_up_action = menu.addAction("⬆ 上移")
        move_down_action = menu.addAction("⬇ 下移")
        action = menu.exec_(self.playlist_widget.mapToGlobal(pos))
        if not action:
            return
        selected_items = self.playlist_widget.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "提示", "请先选择歌单中的歌曲")
            return
        indices = [item.data(Qt.UserRole) for item in selected_items if item.data(Qt.UserRole) is not None]
        if action == play_action:
            if indices:
                self.current_play_index = indices[0]
                self.play_current()
        elif action == remove_action:
            for idx in sorted(indices, reverse=True):
                if 0 <= idx < len(self.playlist):
                    del self.playlist[idx]
            self.current_play_index = -1
            self.update_playlist_widget()
            self.stop_playback()
        elif action == clear_action:
            self.playlist.clear()
            self.current_play_index = -1
            self.update_playlist_widget()
            self.stop_playback()
        elif action == move_up_action:
            idx = indices[0]
            if idx > 0 and idx < len(self.playlist):
                self.playlist[idx], self.playlist[idx-1] = self.playlist[idx-1], self.playlist[idx]
                if self.current_play_index == idx:
                    self.current_play_index = idx - 1
                elif self.current_play_index == idx - 1:
                    self.current_play_index = idx
                self.update_playlist_widget()
        elif action == move_down_action:
            idx = indices[0]
            if 0 <= idx < len(self.playlist) - 1:
                self.playlist[idx], self.playlist[idx+1] = self.playlist[idx+1], self.playlist[idx]
                if self.current_play_index == idx:
                    self.current_play_index = idx + 1
                elif self.current_play_index == idx + 1:
                    self.current_play_index = idx
                self.update_playlist_widget()

    def _on_playlist_rows_moved(self, source_parent, source_start, source_end, dest_parent, dest_row):
        """当用户拖拽播放列表项后，同步 playlist 列表顺序"""
        # 重建 playlist 根据当前视图顺序
        new_playlist = []
        for i in range(self.playlist_widget.count()):
            item = self.playlist_widget.item(i)
            idx = item.data(Qt.UserRole)
            if idx is not None and 0 <= idx < len(self.playlist):
                new_playlist.append(self.playlist[idx])
        self.playlist = new_playlist
        # 更新 current_play_index（若当前歌曲存在，重新定位）
        if self.current_play_index != -1 and self.current_play_index < len(self.playlist):
            current_song = self.playlist[self.current_play_index]
            new_idx = -1
            for i, song in enumerate(self.playlist):
                if song.get('identifier') == current_song.get('identifier'):
                    new_idx = i
                    break
            if new_idx != -1:
                self.current_play_index = new_idx
            else:
                self.current_play_index = -1
        # 刷新标题计数
        self.update_playlist_widget()

    # ---------- 歌单保存/加载（加密+明文） ----------
    def _sanitize_song_info(self, song_info):
        """将 song_info 转换为轻量级可序列化字典，保留 identifier 和 source"""
        if not isinstance(song_info, dict):
            if hasattr(song_info, '__dict__'):
                song_info = {k: v for k, v in vars(song_info).items() if not k.startswith('_')}
            else:
                song_info = {}

        keep_keys = [
            'song_name', 'singers', 'album', 'ext', 'duration', 'duration_s',
            'cover_url', 'lyric', 'download_url', 'source',
            'identifier', 'song_id', 'file_size', 'file_size_bytes'
        ]
        clean = {}
        for key in keep_keys:
            if key in song_info:
                val = song_info[key]
                if isinstance(val, (str, int, float, bool, list, dict, type(None))):
                    clean[key] = val
                else:
                    clean[key] = str(val)

        clean.setdefault('song_name', song_info.get('song_name', '未知歌曲'))
        clean.setdefault('singers', song_info.get('singers', '未知歌手'))
        clean.setdefault('download_url', song_info.get('download_url', ''))
        # 确保 identifier 存在
        if 'identifier' not in clean and 'song_id' in clean:
            clean['identifier'] = clean['song_id']
        return clean

    def _encrypt_playlist_data(self, data: bytes) -> str:
        try:
            key = base64.urlsafe_b64encode(hashlib.sha256(ENCRYPTION_PASSWORD.encode()).digest())
            f = Fernet(key)
            encrypted = f.encrypt(data)
            return base64.b64encode(encrypted).decode('ascii')
        except Exception as e:
            QMessageBox.critical(self, "加密错误", f"加密失败: {str(e)}")
            return None

    def _decrypt_playlist_data(self, encrypted_b64: str) -> bytes:
        try:
            key = base64.urlsafe_b64encode(hashlib.sha256(ENCRYPTION_PASSWORD.encode()).digest())
            f = Fernet(key)
            encrypted = base64.b64decode(encrypted_b64)
            return f.decrypt(encrypted)
        except Exception as e:
            raise Exception("解密失败，数据可能已损坏或密钥不匹配") from e

    def save_playlist(self):
        if not self.playlist:
            QMessageBox.information(self, "提示", "歌单为空，无需保存")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存歌单", "",
            "JSON Files (*.json);;All Files (*.*)"
        )
        if not file_path:
            return

        serializable_list = [self._sanitize_song_info(item) for item in self.playlist]
        json_str = json.dumps(serializable_list, ensure_ascii=False, indent=2)

        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.ShiftModifier:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(json_str)
                self.label_stats.setText(f"歌单已明文保存至 {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", f"写入文件失败: {str(e)}")
        else:
            encrypted_b64 = self._encrypt_playlist_data(json_str.encode('utf-8'))
            if encrypted_b64 is None:
                return
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write("ENCRYPTED:" + encrypted_b64)
                self.label_stats.setText(f"歌单已加密保存至 {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", f"写入文件失败: {str(e)}")

    def load_playlist(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "加载歌单", "",
            "JSON Files (*.json);;All Files (*.*)"
        )
        if not file_path:
            return
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            QMessageBox.critical(self, "读取失败", str(e))
            return

        if content.startswith("ENCRYPTED:"):
            encrypted_b64 = content[len("ENCRYPTED:"):]
            try:
                decrypted_bytes = self._decrypt_playlist_data(encrypted_b64)
                data = json.loads(decrypted_bytes.decode('utf-8'))
            except Exception as e:
                QMessageBox.critical(self, "解密失败", str(e))
                return
        else:
            try:
                data = json.loads(content)
            except Exception as e:
                QMessageBox.critical(self, "解析失败", f"无效的JSON格式: {str(e)}")
                return

        if not isinstance(data, list):
            QMessageBox.critical(self, "加载失败", "无效的歌单格式，应为数组")
            return

        # 加载时，对每个 song_info 补充缺失字段，并尝试刷新链接（可选）
        self.playlist = data
        self.current_play_index = -1
        self.update_playlist_widget()
        self.label_stats.setText(f"已加载歌单，共 {len(self.playlist)} 首歌曲")
        # 可选项：在加载后自动刷新所有链接（耗时，不宜自动做），用户点击播放时再刷新。

    # ---------- 设置对话框 ----------
    def open_settings(self):
        dlg = SettingsDialog(None)
        dlg.setWindowModality(Qt.ApplicationModal)

        # 原有设置
        dlg.spin_limit.setValue(self.settings.get('limit', 10))
        dlg.check_dedup.setChecked(self.settings.get('dedup', False))
        dlg.path_edit.setText(self.settings.get('save_dir', DEFAULT_SAVE_DIR))
        dlg.format_combo.setCurrentText(self.settings.get('filename_format', '歌手-歌曲名'))
        dlg.format_custom_edit.setText(self.settings.get('custom_format', ''))
        dlg.check_lyric.setChecked(self.settings.get('download_lyric', True))
        dlg.check_cover.setChecked(self.settings.get('download_cover', True))

        # 搜索源复选框
        for cb in dlg.source_checkboxes:
            cb.setChecked(cb.text() in self.settings.get('sources', []))

        # ===== 新增：加载格式转换设置 =====
        dlg.convert_check.setChecked(self.settings.get('convert_enabled', False))
        dlg.convert_combo.setCurrentText(self.settings.get('convert_format', ''))
        dlg.bitrate_combo.setCurrentText(self.settings.get('convert_bitrate', ''))
        # 根据启用状态更新控件启用/禁用（因为 toggled 信号只在用户交互时触发）
        dlg.convert_combo.setEnabled(dlg.convert_check.isChecked())
        dlg.bitrate_combo.setEnabled(dlg.convert_check.isChecked())

        parent_geo = self.geometry()
        dlg.move(
            parent_geo.x() + (parent_geo.width() - 620) // 2,
            parent_geo.y() + (parent_geo.height() - 330) // 2
        )

        if dlg.exec_() == QDialog.Accepted:
            new_settings = dlg.get_settings()
            self.settings.update(new_settings)
            save_settings(self.settings)
            self._init_music_client()
            self._apply_volume_from_settings()
            self.label_stats.setText("设置已更新并保存")

    # ---------- 其他UI ----------
    def show_about(self):
        QMessageBox.about(self, "关于",
            "🎵 cYy Music Client\n"
            "基于 PyQt5 + musicdl\n"
            "版本 4.4.1\n"
            "本程序遵循 GNU 3.0 开源协议\n"
            "© 2026 cYy"
        )

    def cover_click(self, event):
        self.show_visualization()

    # ---------- 下载功能 ----------
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
            if info:
                refreshed = self.refresh_song_url(info)
                if refreshed:
                    songs_to_download.append(refreshed)
                else:
                    QMessageBox.warning(self, '警告', f'第 {row+1} 首歌曲链接失效且无法刷新，已跳过')
            else:
                QMessageBox.warning(self, '警告', f'第 {row+1} 首歌曲信息缺失，已跳过')

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

        # 重置下载队列和状态
        self.download_queue = songs_to_download.copy()
        self.active_downloads.clear()
        self.download_completed = 0
        self.download_start_time = time.time()
        self.download_done_bytes = 0
        # 估算总大小（若没有 file_size_bytes，则用 5MB 估算）
        self.download_total_bytes = sum(
            int(info.get('file_size_bytes', 0)) or 0 for info in self.download_queue
        )
        if self.download_total_bytes == 0:
            self.download_total_bytes = len(self.download_queue) * 5 * 1024 * 1024

        self.is_downloading = True
        self._set_ui_enabled(False)
        self.result_list.setEnabled(False)
        self.action_download.setEnabled(False)

        self.bar_overall.setMaximum(len(songs_to_download))
        self.bar_overall.setValue(0)
        self.bar_download.setValue(0)
        self.label_stats.setText(f"准备下载 {len(songs_to_download)} 首...")

        self._start_downloads()

    def _start_downloads(self):
        """启动并发下载，直至达到并发数或队列为空"""
        while len(self.active_downloads) < self.download_concurrency and self.download_queue:
            song_info = self.download_queue.pop(0)
            thread = self._create_download_thread(song_info)
            self.active_downloads.append(thread)
            thread.start()

    def _create_download_thread(self, song_info):
        """创建单个下载线程，携带转换设置"""
        thread = DownloadThread(
            song_info,
            self._get_request_kwargs_for_source,
            self.settings['save_dir'],
            self._get_filename_template(),
            self.settings['download_lyric'],
            self.settings['download_cover'],
            convert_format=self.settings.get('convert_format', ''),
            convert_bitrate=self.settings.get('convert_bitrate', '')
        )
        thread.progress.connect(self._on_single_progress)
        thread.finished.connect(self._on_single_download_finished)
        thread.error.connect(self._on_single_download_error)
        return thread

    def _on_single_progress(self, percent):
        """单个下载进度（可更新单曲进度条）"""
        self.bar_download.setValue(percent)

    def _update_eta(self):
        """更新 ETA 显示"""
        if self.download_start_time is None:
            return
        elapsed = time.time() - self.download_start_time
        if elapsed < 1:
            return
        total = self.bar_overall.maximum()
        done = self.bar_overall.value()
        if done == 0 or total == 0:
            return
        progress = done / total
        eta_seconds = (elapsed / progress) - elapsed
        eta_str = self._format_time(int(eta_seconds)) if eta_seconds > 0 else "即将完成"
        self.label_stats.setText(f"已完成 {done}/{total}  剩余: {eta_str}")

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
        # 在下载前再次刷新（以防长时间停留）
        refreshed = self.refresh_song_url(song_info)
        if not refreshed:
            self._download_current_index += 1
            self.bar_overall.setValue(self._download_current_index)
            self.label_stats.setText(f"⚠️ 跳过失效歌曲: {song_info.get('song_name')}")
            QTimer.singleShot(100, self._start_next_download)
            return
        song_info = refreshed

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
        self.download_completed += 1
        self.bar_overall.setValue(self.download_completed)
        # 移除该线程
        thread = self.sender()
        if thread in self.active_downloads:
            self.active_downloads.remove(thread)
        self._update_eta()
        self._start_downloads()
        # 检查是否全部完成
        if self.download_completed >= len(self.download_queue) + len(self.active_downloads):
            self._on_all_downloads_finished()

    def _on_single_download_error(self, error_msg):
        if self._download_cancelled:
            return
        QMessageBox.critical(self, '下载错误', f'下载失败：{error_msg}')
        # 同样完成计数，但标记为错误
        self.download_completed += 1
        self.bar_overall.setValue(self.download_completed)
        thread = self.sender()
        if thread in self.active_downloads:
            self.active_downloads.remove(thread)
        self._start_downloads()
        # 检查是否全部完成
        if self.download_completed >= len(self.download_queue) + len(self.active_downloads):
            self._on_all_downloads_finished()

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
        self.download_queue = []
        self.active_downloads.clear()
        self.download_completed = 0
        self.download_start_time = None
        self._downloaded_files.clear()
        self._adjusting = False

    # ---------- 窗口控制 ----------
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
        self._url_cache.clear()
        # 保存当前设置（音量、播放模式等）
        self.settings['volume'] = self.slider_volume.value()
        self.settings['play_mode'] = self.combo_playmode.currentIndex()
        save_settings(self.settings)

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
        self.cover_pool.waitForDone(3000)
        event.accept()

    def _show_warning(self, title: str, text: str):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.NoIcon)
        msg.setWindowTitle(title)
        msg.setText(text)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec_()

    # ---------- 可视化 ----------
    def show_visualization(self):
        if self.current_play_index < 0 or not self.playlist:
            if hasattr(self, 'vis_window') and self.vis_window is not None:
                try:
                    self.vis_window.close()
                except RuntimeError:
                    pass
                self.vis_window = None
            self.vis_window = AudioVisualizer(parent=self, initial_volume=self.slider_volume.value())
            self.vis_window.destroyed.connect(self._on_vis_window_destroyed)
            self.vis_window.show()
            return

        song_info = self.playlist[self.current_play_index]
        # 移除 refresh_song_url 调用，因为若文件已存在则无需刷新
        # 直接检查本地文件是否存在
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
            # 文件不存在，需要下载，下载前会调用 refresh_song_url
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
            parent=self,
            initial_volume=self.slider_volume.value()
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

        # 刷新链接
        refreshed = self.refresh_song_url(song_info)
        if not refreshed:
            QMessageBox.warning(self, "链接失效", "无法获取有效链接，请稍后重试。")
            return
        song_info = refreshed

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

    # ---------- 封面加载 ----------
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
                scaled = pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
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
        runnable = CoverRunnable(url, request_kwargs, task_id, session=self._requests_session)
        runnable.signals.finished.connect(self._on_cover_loaded)
        self.cover_pool.start(runnable)

    # ---------- 播放控制 ----------
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
        self.settings['play_mode'] = index
        save_settings(self.settings)

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

    def play_current(self):
        if not self.playlist or self.current_play_index < 0 or self.current_play_index >= len(self.playlist):
            self.stop_playback()
            return

        song_info = self.playlist[self.current_play_index]

        # 🔥 刷新链接
        refreshed = self.refresh_song_url(song_info)
        if not refreshed:
            QMessageBox.warning(self, "播放失败", "无法获取有效的播放链接，请检查网络或重新搜索。")
            self.stop_playback()
            return
        song_info = refreshed

        url = song_info.get('download_url')
        if not url:
            QMessageBox.warning(self, "无法播放", "该歌曲没有可用的播放链接。")
            return

        # 重置播放器
        self.player.reset()

        source = song_info.get('source', '')
        req_kwargs = self._get_request_kwargs_for_source(source)
        headers = req_kwargs.get('headers') or {}
        self.player.setMedia(url, headers=headers)

        singer = song_info.get('singers', '')
        name = song_info.get('song_name', '')
        self.now_playing_label.setText(f"🎵 {singer} - {name}")

        # 加载歌词
        lyric_text = song_info.get('lyric') or song_info.get('lyrics', '')
        if lyric_text:
            self.current_lyrics = self.parse_lrc(lyric_text)
        else:
            self.current_lyrics = []
        self.current_lyric_index = -1
        self.lyric_display.clear()
        self.update_lyric_display(0)

        volume = self.slider_volume.value()
        QTimer.singleShot(200, lambda: self.player.play(volume=volume))

        # 加载封面
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
        info = self.get_song_info_by_row(row)
        if info:
            self.add_to_playlist(info, play=True)

    # ---------- 歌词解析与显示 ----------
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
        if not self.current_lyrics:
            return
        row = self.lyric_display.row(item)
        if row < 0 or row >= len(self.current_lyrics):
            return
        time_ms, _ = self.current_lyrics[row]

        state = self.player.state()
        if state == PlayerState.StoppedState:
            return

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

        if self.lyric_display.count() == 0:
            self.lyric_display.clear()
            for _, text in self.current_lyrics:
                self.lyric_display.addItem(text)
            self.current_lyric_index = -1

        new_idx = -1
        for i, (t, _) in enumerate(self.current_lyrics):
            if t <= pos_ms:
                new_idx = i
            else:
                break

        if new_idx == self.current_lyric_index:
            return

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

    # ---------- 文件名工具 ----------
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
        base = build_filename(song_info, fmt)
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

    app.setStyleSheet(get_global_stylesheet())

    gui = MusicdlGUI()
    gui.show()
    sys.exit(app.exec_())
