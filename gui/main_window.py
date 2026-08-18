# -*- coding: utf-8 -*-
import os
import sys
import threading
from typing import Dict   # 新增
from PyQt5.QtCore import Qt, QTimer, QPoint, QRect, QSize, QThreadPool
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QWidget, QApplication, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QListWidget, QSplitter,
    QProgressBar, QSlider, QComboBox, QMenu, QMessageBox,
    QAbstractItemView, QSizePolicy
)
from cachetools import TTLCache

from constants import PLAYLIST_SOURCE_MAP, DEFAULT_SAVE_DIR, PlayerState, THEMES, PlayMode, SOURCE_INTERNAL, REFRESH_SEARCH_SIZE
from config import load_settings, save_settings
from utils import get_global_stylesheet, logger, safe_stop_thread
from threads import PlaylistParseThread
from widgets import MarqueeLabel, ClickableSlider, SpectrumWidget

from .mixins import (
    TitleBarMixin, SearchMixin, PlaylistMixin,
    PlaybackMixin, DownloadMixin, RefreshMixin,
    VisualizationMixin, SettingsMixin, BaseMixin
)

class MusicdlGUI(
    QWidget,
    TitleBarMixin,
    SearchMixin,
    PlaylistMixin,
    PlaybackMixin,
    DownloadMixin,
    RefreshMixin,
    VisualizationMixin,
    SettingsMixin,
    BaseMixin
):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setObjectName("musicdlGUI")
        self.setWindowTitle('cYy Music Client')
        self.setMinimumSize(1200, 800)
        self.resize(1200, 800)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.settings = load_settings()
        # 音量设置延后到 UI 创建后

        self._url_cache = TTLCache(maxsize=500, ttl=300)
        self._refresh_lock = threading.Lock()

        self.playlist = []
        self.current_play_index = -1
        self.play_mode = PlayMode(self.settings.get('play_mode', 2))

        # 延迟对象
        self.player = None
        self.music_client = None
        self.refresh_client = None
        self._requests_session = None
        self._cover_task_id = 0

        # 状态变量
        self.search_in_progress = False
        self.is_downloading = False
        self.is_parsing = False
        self._download_cancelled = False
        self._all_done_emitted = False
        self.music_records = {}
        self._source_counts = {}
        self.current_lyrics = []
        self.current_lyric_index = -1
        self.drag_pos = QPoint()
        self.dragging = False
        self._resizing = False
        self._resize_start_pos = QPoint()
        self._resize_start_geo = QRect()
        self.search_thread = None
        self.download_thread = None
        self.parse_thread = None
        self.refresh_thread = None
        self.search_task_counter = 0
        self.current_search_task_id = 0
        self.parse_task_counter = 0
        self.current_parse_task_id = 0
        self.download_concurrency = 3
        self.active_downloads = []
        self.download_queue = []
        self.download_completed = 0
        self.download_start_time = None
        self._total_to_download = 0
        self.parse_progress = None
        self.vis_window = None

        # 创建 UI（此时 slider_volume 会被创建）
        self.setup_title_bar()
        self._init_ui()
        self._init_signals()

        # 现在设置音量
        self._apply_volume_from_settings()

        self.update_playlist_widget()

        self.cover_pool = QThreadPool.globalInstance()
        self.cover_pool.setMaxThreadCount(10)

        QTimer.singleShot(100, self._check_deps)
        self.apply_theme(self.settings.get('theme', 'light'))

    # ---------- 延迟初始化方法 ----------
    def _ensure_music_client(self):
        """确保音乐客户端已初始化（懒加载）"""
        if self.music_client is None:
            self._init_music_client()
        return self.music_client

    def _ensure_refresh_client(self):
        """确保刷新客户端已初始化（懒加载）"""
        if self.refresh_client is None:
            self._init_refresh_client()
        return self.refresh_client

    def _ensure_requests_session(self):
        """确保 requests Session 已创建（懒加载）"""
        if self._requests_session is None:
            import requests
            self._requests_session = requests.Session()
            self._requests_session.verify = True
        return self._requests_session

    def _init_music_client(self):
        """实际的 MusicClient 初始化"""
        from constants import SOURCE_INTERNAL
        selected_display = self.settings.get('sources', [])
        selected_sources = [SOURCE_INTERNAL.get(d) for d in selected_display if SOURCE_INTERNAL.get(d)]
        if not selected_sources:
            self.music_client = None
            return
        init_cfg = {}
        for src in selected_sources:
            init_cfg[src] = {
                'search_size_per_source': self.settings.get('limit', 10),
                'maintain_session': True,
                'disable_print': True,
            }
        try:
            from musicdl import musicdl
            self.music_client = musicdl.MusicClient(
                music_sources=selected_sources,
                init_music_clients_cfg=init_cfg,
                clients_threadings={src: 5 for src in selected_sources}
            )
            logger.info(f"MusicClient 初始化成功，源: {selected_sources}")
        except Exception as e:
            logger.error(f"MusicClient 初始化失败: {e}", exc_info=True)
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, "初始化失败", f"无法创建音乐客户端：{str(e)}")
            self.music_client = None

    def _init_refresh_client(self):
        """实际的 RefreshClient 初始化"""
        if self.music_client is None:
            self._ensure_music_client()
        if self.music_client is None:
            self.refresh_client = None
            return
        from constants import REFRESH_SEARCH_SIZE
        selected_sources = list(self.music_client.music_clients.keys())
        if not selected_sources:
            self.refresh_client = None
            return
        init_cfg = {}
        for src in selected_sources:
            init_cfg[src] = {
                'search_size_per_source': REFRESH_SEARCH_SIZE,
                'maintain_session': True,
                'disable_print': True,
            }
        try:
            from musicdl import musicdl
            self.refresh_client = musicdl.MusicClient(
                music_sources=selected_sources,
                init_music_clients_cfg=init_cfg,
                clients_threadings={src: 2 for src in selected_sources}
            )
            logger.info("RefreshClient 初始化成功")
        except Exception as e:
            logger.error(f"RefreshClient 初始化失败: {e}", exc_info=True)
            self.refresh_client = None

    def _apply_volume_from_settings(self):
        """应用音量设置，仅在 slider_volume 存在时执行"""
        if hasattr(self, 'slider_volume') and self.slider_volume is not None:
            vol = self.settings.get('volume', 60)
            self.slider_volume.setValue(vol)

    def apply_theme(self, theme_name: str = None):
        if theme_name is None:
            theme_name = self.settings.get('theme', 'light')
        bg_opacity = self.settings.get('background_opacity', 0.8)
        stylesheet = get_global_stylesheet(theme_name, bg_opacity)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(stylesheet)
            for widget in app.topLevelWidgets():
                widget.update()
            self.settings['theme'] = theme_name
            save_settings(self.settings)
            self.label_stats.setText(f"已切换主题: {THEMES.get(theme_name, {}).get('display_name', theme_name)}")

    def _init_ui(self):
        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.title_bar)

        content_widget = QWidget()
        content_widget.setObjectName("contentWidget")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(15, 10, 15, 15)
        content_layout.setSpacing(10)

        # 歌单解析栏
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

        # 工具栏
        result_toolbar = QHBoxLayout()
        result_toolbar.setContentsMargins(0, 0, 0, 0)
        self.btn_clear_results = QPushButton("🗑 清空结果")
        self.btn_clear_results.clicked.connect(self.clear_results)
        self.btn_cancel_download = QPushButton("⏹ 取消下载")
        self.btn_cancel_download.setObjectName("cancelDownloadButton")
        self.btn_cancel_download.setEnabled(False)
        self.btn_cancel_download.clicked.connect(self.cancel_all_downloads)
        result_toolbar.addStretch()
        result_toolbar.addWidget(self.btn_clear_results)
        result_toolbar.addWidget(self.btn_cancel_download)
        content_layout.addLayout(result_toolbar)

        # 结果列表
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

        # 歌词显示
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

        # 播放列表容器
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
        self.playlist_title.setObjectName("playlist_title")
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
        self.playlist_widget.itemDoubleClicked.connect(self.play_playlist_item)
        self.playlist_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.playlist_widget.customContextMenuRequested.connect(self.show_playlist_context_menu)
        playlist_vbox.addWidget(self.playlist_widget)
        self.playlist_widget.setDragDropMode(QListWidget.InternalMove)
        self.playlist_widget.setDefaultDropAction(Qt.MoveAction)
        self.playlist_widget.model().rowsMoved.connect(self._on_playlist_rows_moved)

        # 右侧分割布局（歌词 + 播放列表）
        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.setHandleWidth(3)
        right_splitter.setStyleSheet("""
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
        right_splitter.addWidget(self.lyric_display)
        right_splitter.addWidget(playlist_container)
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 1)

        # 主分割（搜索结果 + 右侧）
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setHandleWidth(3)
        main_splitter.addWidget(self.result_list)
        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 3)
        main_splitter.setStretchFactor(1, 1)
        content_layout.addWidget(main_splitter, 1)

        # 底部播放控制栏
        bottom_player = QWidget()
        bottom_player.setObjectName("bottomPlayer")
        bottom_player.setFixedHeight(80)
        bottom_layout = QHBoxLayout(bottom_player)
        bottom_layout.setContentsMargins(10, 5, 10, 5)
        bottom_layout.setSpacing(8)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(60, 60)
        self.cover_label.setStyleSheet("border: 1px solid #BDC3C7; border-radius: 6px; background-color: #E8EDF2;")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setText("🎵")
        self.cover_label.mousePressEvent = self.cover_click
        bottom_layout.addWidget(self.cover_label)

        self.now_playing_label = MarqueeLabel(self)
        self.now_playing_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.now_playing_label.setStyleSheet("font-weight: bold; color: #1E88E5; font-size: 15px;")
        self.now_playing_label.setObjectName("nowPlayingLabel")
        self.now_playing_label.setFixedWidth(200)
        self.now_playing_label.setFixedHeight(30)
        bottom_layout.addWidget(self.now_playing_label)

        # 进度条列：上方为迷你频谱，二者同宽（随窗口缩放保持一致）；
        # 滑块压矮高度以消除频谱与进度条之间的多余空隙
        progress_col = QVBoxLayout()
        progress_col.setContentsMargins(0, 0, 0, 0)
        progress_col.setSpacing(0)
        self.spectrum_widget = SpectrumWidget(self)
        progress_col.addWidget(self.spectrum_widget)
        self.slider_position = ClickableSlider(Qt.Horizontal, self)
        self.slider_position.setRange(0, 0)
        self.slider_position.setTracking(True)
        self.slider_position.setFixedHeight(14)
        progress_col.addWidget(self.slider_position)
        bottom_layout.addLayout(progress_col, 1)

        self.label_time = QLabel("00:00 / 00:00")
        self.label_time.setMinimumWidth(100)
        self.label_time.setAlignment(Qt.AlignCenter)
        bottom_layout.addWidget(self.label_time)

        self.btn_prev = QPushButton("⏪")
        self.btn_prev.setObjectName("prevButton")
        self.btn_prev.setFixedSize(36, 36)
        bottom_layout.addWidget(self.btn_prev)

        self.btn_play = QPushButton("▶")
        self.btn_play.setObjectName("playButton")
        self.btn_play.setFixedSize(44, 40)
        bottom_layout.addWidget(self.btn_play)

        self.btn_next = QPushButton("⏩")
        self.btn_next.setObjectName("nextButton")
        self.btn_next.setFixedSize(36, 36)
        bottom_layout.addWidget(self.btn_next)

        self.btn_stop = QPushButton("⏹")
        self.btn_stop.setObjectName("stopButton")
        self.btn_stop.setFixedSize(36, 36)
        bottom_layout.addWidget(self.btn_stop)

        self.btn_visualize = QPushButton("🎨")
        self.btn_visualize.setObjectName("visualizeButton")
        self.btn_visualize.setFixedSize(36, 36)
        self.btn_visualize.setToolTip("打开可视化窗口")
        bottom_layout.addWidget(self.btn_visualize)

        bottom_layout.addWidget(QLabel("🔊"))
        self.slider_volume = QSlider(Qt.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(self.settings.get('volume', 60))
        self.slider_volume.setFixedWidth(80)
        bottom_layout.addWidget(self.slider_volume)

        bottom_layout.addWidget(QLabel("模式:"))
        self.combo_playmode = QComboBox()
        self.combo_playmode.addItems(["单曲循环", "单曲暂停", "列表循环", "列表暂停"])
        self.combo_playmode.setCurrentIndex(self.settings.get('play_mode', 2))
        self.combo_playmode.currentIndexChanged.connect(self.on_playmode_changed)
        bottom_layout.addWidget(self.combo_playmode)

        content_layout.addWidget(bottom_player, 0)

        # 状态栏
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(8)
        self.label_stats = QLabel('就绪')
        self.label_stats.setObjectName('statsLabel')
        self.label_stats.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.label_stats.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        status_layout.addWidget(self.label_stats, 1)
        status_layout.addWidget(QLabel("单曲:"))
        self.bar_download = QProgressBar()
        self.bar_download.setObjectName('progressBar')
        self.bar_download.setFixedWidth(120)
        status_layout.addWidget(self.bar_download)
        status_layout.addWidget(QLabel("总:"))
        self.bar_overall = QProgressBar()
        self.bar_overall.setObjectName('overallProgressBar')
        self.bar_overall.setFixedWidth(120)
        status_layout.addWidget(self.bar_overall)
        content_layout.addLayout(status_layout)

        main_layout.addWidget(content_widget)

        # 上下文菜单
        self.context_menu = QMenu(self)
        self.action_download = self.context_menu.addAction('⬇️ 下载选中')
        self.action_download.setObjectName('downloadAction')
        self.action_download.triggered.connect(self.download_selected)
        self.action_add_to_playlist = self.context_menu.addAction('➕ 添加到歌单')
        self.action_add_to_playlist.triggered.connect(self.add_selected_to_playlist)

    def _init_signals(self):
        self.button_parse_playlist.clicked.connect(self.parse_playlist)
        self.btn_play.clicked.connect(self.toggle_playback)
        self.btn_stop.clicked.connect(self.stop_playback)
        self.slider_position.sliderMoved.connect(self._on_seek_preview)
        self.slider_position.sliderReleased.connect(self._on_seek_apply)
        self.slider_volume.valueChanged.connect(self.set_volume)
        self.btn_prev.clicked.connect(self.play_prev)
        self.btn_next.clicked.connect(self.play_next)
        self.btn_visualize.clicked.connect(self.show_visualization)
        self.result_list.itemDoubleClicked.connect(self.on_list_double_click)

    def _check_deps(self):
        import shutil
        if shutil.which('ffmpeg') is None:
            self.label_stats.setText('⚠️ 未检测到 ffmpeg，播放与可视化功能不可用')
            self.btn_play.setEnabled(False)
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.slider_position.setEnabled(False)
            self.label_time.setText("ffmpeg 不可用")

    # ---------- 窗口事件（拖拽、调整大小） ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.pos()
            if pos.x() >= self.width() - 15 and pos.y() >= self.height() - 15:
                self._resizing = True
                self._resize_start_pos = event.globalPos()
                self._resize_start_geo = self.geometry()
                event.accept()
                return
            if self.title_bar.geometry().contains(pos):
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
        if self.dragging:
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
        if self.dragging:
            self.dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.title_bar.geometry().contains(event.pos()):
            self.toggle_maximize()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def closeEvent(self, event):
        self._url_cache.clear()
        self.settings['volume'] = self.slider_volume.value()
        self.settings['play_mode'] = self.combo_playmode.currentIndex()
        save_settings(self.settings)

        if self.refresh_thread and self.refresh_thread.isRunning():
            self.refresh_thread.stop()
            self.refresh_thread.wait()
            self.refresh_thread.deleteLater()
            self.refresh_thread = None

        for thread in [self.search_thread, self.download_thread, self.parse_thread]:
            if thread is not None:
                try:
                    if thread.isRunning():
                        thread.stop()
                except RuntimeError:
                    pass

        if self.player is not None and self.player.state() != PlayerState.StoppedState:
            self.player.stop()
        self._cover_task_id += 1
        try:
            if self._requests_session:
                self._requests_session.close()
        except Exception:
            pass
        self.cover_pool.waitForDone(3000)
        event.accept()

    def _set_ui_enabled(self, enabled: bool):
        self.search_input.setEnabled(enabled)
        self.btn_search_title.setEnabled(True)
        self.button_parse_playlist.setEnabled(enabled)
        self.lineedit_playlist.setEnabled(enabled)
        self.combo_playlist_source.setEnabled(enabled)
        self.btn_settings.setEnabled(enabled)
        self.btn_about.setEnabled(enabled)
        self.action_download.setEnabled(enabled and not self.is_downloading)

    def _show_warning(self, title: str, text: str):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.NoIcon)
        msg.setWindowTitle(title)
        msg.setText(text)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec_()

    def show_about(self):
        QMessageBox.about(self, "关于",
            "🎵 cYy Music Client\n"
            "基于 PyQt5 + musicdl\n"
            "版本 4.7.0\n"
            "本程序遵循 GNU 3.0 开源协议\n"
            "© 2026 cYy"
        )

    def cover_click(self, event):
        self.show_visualization()

    # ---------- 歌单解析（仍在主窗口，因为涉及 UI 进度） ----------
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

        self._ensure_music_client()
        if not self.music_client:
            QMessageBox.warning(self, '警告', '音乐客户端未初始化，请先设置搜索源')
            return

        if source_internal not in self.music_client.music_clients:
            reply = QMessageBox.question(
                self, "源未启用",
                f"当前设置未启用 {source_display}，是否临时启用并解析？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
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
        current_sources = list(self.music_client.music_clients.keys())
        if source_internal not in current_sources:
            current_sources.append(source_internal)
            display = self._internal_to_display(source_internal)
            if display not in self.settings['sources']:
                self.settings['sources'].append(display)
                save_settings(self.settings)
            self._init_music_client()
            self._init_refresh_client()

    def stop_parse(self):
        if self.is_parsing:
            old_thread = self.parse_thread
            if old_thread is None:
                self.is_parsing = False
                self._restore_parse_ui()
                return

            self.parse_thread = None
            if self.parse_progress:
                self.parse_progress.close()
                self.parse_progress = None

            safe_stop_thread(
                old_thread,
                ['parse_started', 'parse_finished', 'parse_error'],
                lambda: self._cleanup_parse_thread(old_thread)
            )
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

    def _cleanup_parse_thread(self, thread):
        if thread is None:
            return
        for sig_name in ['parse_started', 'parse_finished', 'parse_error']:
            sig = getattr(thread, sig_name, None)
            if sig:
                try:
                    sig.disconnect()
                except TypeError:
                    pass
        if thread.isRunning():
            thread.wait()
        thread.deleteLater()

    # ---------- 其他辅助方法 ----------
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
        from utils import build_filename, sanitize_filepath
        base = build_filename(song_info, fmt)
        return sanitize_filepath(base)

    def on_list_double_click(self, item):
        row = self.result_list.row(item)
        info = self.get_song_info_by_row(row)
        if info:
            self.add_to_playlist(info, play=True)

    def show_context_menu(self, pos):
        if not self.is_downloading and self.result_list.count() > 0 and self.result_list.selectedItems():
            self.context_menu.exec_(self.result_list.mapToGlobal(pos))
