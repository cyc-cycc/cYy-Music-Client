# -*- coding: utf-8 -*-
import os
import sys
import re
import glob
import time
import threading
from typing import Optional, List, Tuple
import numpy as np
import sounddevice as sd
import librosa
import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize

from PyQt5.QtCore import Qt, QTimer, QPoint, pyqtSignal, QRectF, QThread
from PyQt5.QtGui import QColor, QFont, QPixmap, QMouseEvent, QPainterPath, QRegion
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QListWidget, QListWidgetItem, QFileDialog,
    QFrame, QSplitter, QMessageBox, QAbstractItemView
)

from constants import PlayerState, PlayerMediaStatus
from stream_player import StreamPlayer

class CoverDownloadThread(QThread):
    """网络封面下载线程（不依赖 QtNetwork，避免打包排除问题）"""
    finished = pyqtSignal(bytes)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        from utils import _download_image_data
        data, _ = _download_image_data(self.url, {'timeout': 10}, session=None)
        if data:
            self.finished.emit(data)

class AudioLoadThread(QThread):
    loaded = pyqtSignal(np.ndarray, int, np.ndarray, str, str)
    error = pyqtSignal(str)

    def __init__(self, audio_path, lyric_path=None, cover_path=None):
        super().__init__()
        self.audio_path = audio_path
        self.lyric_path = lyric_path
        self.cover_path = cover_path

    def run(self):
        try:
            data, sr = librosa.load(self.audio_path, sr=None, mono=True)
            hop_length = 512
            n_fft = 2048
            D = librosa.stft(data, n_fft=n_fft, hop_length=hop_length)
            mag = np.abs(D)
            mel_basis = librosa.filters.mel(sr=sr, n_fft=n_fft, n_mels=45)
            mel_spec = np.dot(mel_basis, mag)
            log_mel = np.log1p(mel_spec)
            col_max = np.max(log_mel, axis=0)
            col_max[col_max == 0] = 1.0
            norm_stft = (log_mel / col_max[np.newaxis, :]).clip(0.0, 1.0).astype(np.float32)
            self.loaded.emit(data, sr, norm_stft, self.lyric_path, self.cover_path)
        except Exception as e:
            self.error.emit(str(e))

class AudioVisualizer(QMainWindow):
    def __init__(self, audio_path: str = None, lyric_path: str = None,
                 cover_path: str = None, parent=None, initial_volume: int = 60,
                 theme_name: str = 'light',
                 stream_url: str = None, duration_str: str = None,
                 lyric_text: str = None, cover_url: str = None,
                 song_title: str = None):
        super().__init__(parent)
        self.theme_name = theme_name
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("🎵 音频可视化")
        self.setGeometry(100, 100, 800, 700)
        self.setMinimumSize(700, 700)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self.audio_data = None
        self.sample_rate = None
        self.read_index = 0
        self.lock = threading.Lock()
        self.paused = False
        self.volume = initial_volume / 100
        self.stream = None
        self.lyrics = []
        self.current_lyric_index = -1
        self.total_time = 0
        self.is_dragging = False

        self.fft_bins = 45
        self.ring_bins = 40
        self.y_max = 1.1
        self.smooth_alpha = 0.4
        self.smooth_bar_vals = None
        self.smooth_ring_vals = None

        self.norm_stft = None
        self.frame_count = 0
        self.frames_per_second = 0.0

        # ===== 流式模式状态 =====
        self.stream_url = stream_url
        # 与主窗口共用同一套流式播放引擎（ffmpeg + sounddevice）
        self._player = StreamPlayer(self)
        self._player.mediaStatusChanged.connect(self._on_player_media_status)
        self._mel_basis = None        # 45 频段 mel 滤波器组（流式模式预计算）
        self._fft_window = None
        self._running_max = np.zeros(self.fft_bins, dtype=np.float32)  # 按频段运行最大值
        self._pcm_tail = None         # 上一帧，用于拼接 2048 样本窗口
        self._got_pcm = False         # 是否已收到音频数据（连接超时判定）
        self._stream_start_time = 0.0
        self._was_playing_before_drag = False
        self._cover_thread = None

        self.cover_path = cover_path

        self.drag_pos = QPoint()
        self.dragging = False

        # UI 更新定时器（QTimer 本就在主线程，无需信号中转）
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_ui)
        self.timer.start(40)

        self.initial_volume = initial_volume

        self.WATERFALL_HEIGHT = 60
        self.GAP_SIZE = 1
        self.DISPLAY_WIDTH = self.fft_bins * (self.GAP_SIZE + 1) - 1

        self.show_piano = True
        self._update_counter = 0
        self._background_update_interval = 2

        self._init_ui()

        from utils import get_global_stylesheet
        self.setStyleSheet(get_global_stylesheet(theme_name))

        self.load_thread = None

        if audio_path and os.path.exists(audio_path):
            self.load_audio(audio_path, lyric_path, cover_path)
        elif stream_url:
            self.load_stream(stream_url, duration_str, lyric_text, cover_url, song_title)

    def _init_ui(self):
        central = QFrame()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        title_bar = QWidget()
        title_bar.setFixedHeight(40)
        title_bar.mousePressEvent = self._title_mouse_press
        title_bar.mouseMoveEvent = self._title_mouse_move
        title_bar.mouseReleaseEvent = self._title_mouse_release

        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 0, 10, 0)
        title_layout.setSpacing(5)
        title_label = QLabel("🎵 音频可视化")
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        self.btn_piano = QPushButton("🎹 钢琴流")
        self.btn_piano.setObjectName("titlePianoButton")
        self.btn_piano.setFixedSize(80, 28)
        self.btn_piano.setCheckable(True)
        self.btn_piano.setChecked(True)
        self.btn_piano.clicked.connect(self.toggle_piano)
        title_layout.addWidget(self.btn_piano)

        for symbol, slot in [("□", self._toggle_maximize), ("✕", self.close)]:
            btn = QPushButton(symbol)
            btn.setFixedSize(36, 32)
            btn.clicked.connect(slot)
            if symbol == "□":
                self.max_btn = btn
                btn.setObjectName("titleMaxButton")
            else:
                btn.setObjectName("titleCloseButton")
            title_layout.addWidget(btn)

        main_layout.addWidget(title_bar)

        self.content_widget = QWidget()
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(10)

        self.bg_label = QLabel(self.content_widget)
        self.bg_label.setStyleSheet("background: transparent; border-radius: 8px;")
        self.bg_label.setScaledContents(True)
        self.bg_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.bg_label.hide()
        self.bg_label.setGeometry(self.content_widget.rect())

        self.waterfall_fig = Figure(figsize=(8, 6), dpi=100, facecolor='none')
        self.waterfall_ax = self.waterfall_fig.add_subplot(111)
        self.waterfall_ax.set_facecolor('none')
        self.waterfall_ax.set_frame_on(False)
        self.waterfall_ax.set_xticks([])
        self.waterfall_ax.set_yticks([])
        for spine in self.waterfall_ax.spines.values():
            spine.set_visible(False)

        self.waterfall_data = np.zeros((self.WATERFALL_HEIGHT, self.fft_bins))
        self.waterfall_rgba = np.zeros((self.WATERFALL_HEIGHT, self.DISPLAY_WIDTH, 4), dtype=np.uint8)

        self.waterfall_img = self.waterfall_ax.imshow(
            self.waterfall_rgba,
            aspect='auto',
            origin='upper',
            interpolation='nearest',
            cmap=None
        )
        # 用 subplots_adjust 替代 tight_layout（后者在打开窗口时显著耗时）
        self.waterfall_fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.waterfall_canvas = FigureCanvas(self.waterfall_fig)
        self.waterfall_canvas.setStyleSheet("background: transparent; border: none;")
        self.waterfall_canvas.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.waterfall_canvas.setParent(self.content_widget)
        self.waterfall_canvas.setGeometry(self.content_widget.rect())

        splitter = QSplitter(Qt.Horizontal)
        content_layout.addWidget(splitter, 1)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.setSpacing(5)

        self.song_title_label = QLabel("未选择歌曲")
        self.song_title_label.setAlignment(Qt.AlignCenter)
        self.song_title_label.setFixedHeight(35)
        left_layout.addWidget(self.song_title_label)

        self.lyric_list = QListWidget()
        self.lyric_list.setSelectionMode(QListWidget.NoSelection)
        self.lyric_list.setWordWrap(True)
        left_layout.addWidget(self.lyric_list, 1)
        self.default_lyric_font = QFont(self.lyric_list.font())

        self.bar_fig = Figure(figsize=(6, 3), dpi=100, facecolor='none')
        self.bar_ax = self.bar_fig.add_subplot(111)
        self.bar_ax.set_facecolor('none')
        self.bar_ax.set_ylim(0, self.y_max)
        self.bar_ax.set_xticks([])
        self.bar_ax.set_yticks([])
        for spine in self.bar_ax.spines.values():
            spine.set_visible(False)
        self.bar_rects = self.bar_ax.bar(range(self.fft_bins), np.zeros(self.fft_bins),
                                         width=0.8, color='#4A90D9', edgecolor='none')
        self.bar_fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.bar_canvas = FigureCanvas(self.bar_fig)
        self.bar_canvas.setStyleSheet("background: transparent; border: none;")
        left_layout.addWidget(self.bar_canvas, 1)

        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(5, 5, 5, 5)
        right_layout.setSpacing(5)

        self.ring_fig = Figure(figsize=(4, 4), dpi=100, facecolor='none')
        self.ring_ax = self.ring_fig.add_subplot(111, projection='polar')
        self.ring_ax.set_facecolor('none')
        self.ring_ax.set_ylim(0, self.y_max)
        self.ring_ax.set_xticks([])
        self.ring_ax.set_yticks([])
        self.ring_ax.spines['polar'].set_visible(False)
        self.ring_ax.grid(False)
        self.ring_angles = np.linspace(0, 2 * np.pi, self.ring_bins, endpoint=False)
        self.ring_lines = []
        self.ring_dots = []
        for angle in self.ring_angles:
            line, = self.ring_ax.plot([angle, angle], [0, 0], color='#4A90D9', linewidth=2, alpha=0.8)
            self.ring_lines.append(line)
            dot = self.ring_ax.scatter(angle, 0, s=10, color='#4A90D9', alpha=0.9, zorder=5)
            self.ring_dots.append(dot)
        self.ring_fig.subplots_adjust(left=0.02, right=0.98, top=0.98, bottom=0.02)
        self.ring_canvas = FigureCanvas(self.ring_fig)
        self.ring_canvas.setStyleSheet("background: transparent; border: none;")
        right_layout.addWidget(self.ring_canvas, 1)

        splitter.addWidget(right_widget)

        control = QWidget()
        control_layout = QHBoxLayout(control)
        control_layout.setContentsMargins(10, 5, 10, 5)
        control_layout.setSpacing(10)

        self.select_btn = self._make_btn("📂 选择文件", self._select_file)
        control_layout.addWidget(self.select_btn)

        self.pause_btn = self._make_btn("⏸ 暂停", self._toggle_pause)
        self.pause_btn.setEnabled(False)
        control_layout.addWidget(self.pause_btn)

        self.progress_slider = QSlider(Qt.Horizontal)
        self.progress_slider.setRange(0, 100)
        self.progress_slider.setValue(0)
        self.progress_slider.sliderPressed.connect(self._progress_press)
        self.progress_slider.sliderReleased.connect(self._progress_release)
        self.progress_slider.valueChanged.connect(self._progress_changed)
        self.progress_slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 6px; background: #D5D8DC; border-radius: 3px; }"
            "QSlider::handle:horizontal { background: #4A90D9; width: 16px; margin: -5px 0; border-radius: 8px; }"
            "QSlider::sub-page:horizontal { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4A90D9, stop:1 #7B2FFC); border-radius: 3px; }"
        )
        control_layout.addWidget(self.progress_slider, 1)

        self.time_label = QLabel("00:00 / 00:00")
        control_layout.addWidget(self.time_label)

        control_layout.addWidget(QLabel("🔊"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self.initial_volume)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.valueChanged.connect(self._volume_change)
        self.volume_slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #D5D8DC; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #4A90D9; width: 12px; margin: -4px 0; border-radius: 6px; }"
            "QSlider::sub-page:horizontal { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4A90D9, stop:1 #7B2FFC); border-radius: 2px; }"
        )
        control_layout.addWidget(self.volume_slider)

        content_layout.addWidget(control, 0)
        main_layout.addWidget(self.content_widget)

        self.smooth_bar_vals = np.zeros(self.fft_bins)
        self.smooth_ring_vals = np.zeros(self.ring_bins)

        central.setObjectName("centralWidget")
        title_bar.setObjectName("titleBar")
        self.content_widget.setObjectName("contentWidget")
        left_widget.setObjectName("leftWidget")
        right_widget.setObjectName("rightWidget")
        control.setObjectName("controlWidget")
        self.song_title_label.setObjectName("songTitle")

    # ==================== 流式模式（与主窗口共用 StreamPlayer 引擎） ====================
    def load_stream(self, url: str, duration_str: str = None, lyric_text: str = None,
                    cover_url: str = None, song_title: str = None):
        """从网络 URL 流式加载并播放；重活（设备打开/解码）由引擎后台完成，不阻塞主窗口"""
        self.select_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.song_title_label.setText("正在连接流...")

        self.stream_url = url
        self.total_time = self._parse_duration(duration_str) or 0
        self.progress_slider.setRange(0, 100)
        self.progress_slider.setValue(0)
        if self.total_time > 0:
            self.time_label.setText(f"00:00 / {self._format_time(self.total_time)}")
        else:
            self.time_label.setText("00:00 / --:--")

        # 标题：优先使用传入的“歌手 - 歌名”，不再被 URL 文件名覆盖
        self.song_title_label.setText(song_title or os.path.basename(url))

        self.lyrics = self._parse_lrc_text(lyric_text) if lyric_text else []
        self._refresh_lyric_list()

        if cover_url:
            self._load_cover_from_url(cover_url)

        # mel 滤波器组预计算（与离线模式一致：n_fft=2048，45 频段；耗时 <1ms）
        self._mel_basis = librosa.filters.mel(sr=44100, n_fft=2048, n_mels=self.fft_bins).astype(np.float32)
        self._fft_window = np.hanning(2048).astype(np.float32)
        self._running_max = np.zeros(self.fft_bins, dtype=np.float32)
        self._pcm_tail = None
        self._got_pcm = False
        self._stream_start_time = time.time()

        self._player.set_media(url, None, int(self.total_time * 1000))
        self._player.set_volume(self.initial_volume)
        self._player.play()

        self.pause_btn.setText("⏸ 暂停")
        self.pause_btn.setEnabled(True)
        self.select_btn.setEnabled(True)

    def _on_player_media_status(self, status):
        if status == PlayerMediaStatus.EndOfMedia:
            self._playback_finished()
        elif status == PlayerMediaStatus.InvalidMedia:
            QMessageBox.critical(self, "流式播放失败",
                                 "无法解码该音乐链接（可能已失效或需要请求头）。")
            self._playback_finished()

    # ==================== 本地文件模式 ====================
    def load_audio(self, audio_path, lyric_path=None, cover_path=None):
        if not os.path.exists(audio_path):
            return
        self._player.stop()
        self.stream_url = None
        # 自动查找歌词
        if lyric_path is None:
            base = os.path.splitext(audio_path)[0]
            for cand in (base + '.lrc', base + '.LRC'):
                if os.path.exists(cand):
                    lyric_path = cand
                    break
        # 自动查找封面
        if cover_path is None:
            covers = glob.glob(os.path.splitext(audio_path)[0] + '_cover.*')
            if covers:
                cover_path = covers[0]
        self.audio_path = audio_path
        self.select_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.song_title_label.setText("加载中...")
        self.progress_slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")

        self.load_thread = AudioLoadThread(audio_path, lyric_path, cover_path)
        self.load_thread.loaded.connect(self._on_audio_loaded)
        self.load_thread.error.connect(self._on_load_error)
        self.load_thread.start()

    def _on_load_error(self, error_msg):
        self.select_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        QMessageBox.critical(self, "加载失败", error_msg)

    def _on_audio_loaded(self, data, sr, stft_data, lyric_path, cover_path):
        self.audio_data = data.astype(np.float32)
        self.sample_rate = sr
        self.norm_stft = stft_data
        self.frame_count = self.norm_stft.shape[1]
        self.hop_length = 512
        self.frames_per_second = sr / self.hop_length

        self.read_index = 0
        self.paused = False
        self.pause_btn.setText("⏸ 暂停")
        self.pause_btn.setEnabled(True)

        if lyric_path and os.path.exists(lyric_path):
            self.lyrics = self._parse_lrc(lyric_path)
        else:
            self.lyrics = []
        self._refresh_lyric_list()

        self.song_title_label.setText(os.path.splitext(os.path.basename(self.audio_path))[0])

        if cover_path and os.path.exists(cover_path):
            self._set_cover_background(cover_path)

        self.total_time = len(self.audio_data) / sr
        self.progress_slider.setValue(0)
        self.time_label.setText(f"00:00 / {self._format_time(self.total_time)}")

        if self.stream:
            self.stream.stop()
            self.stream.close()
        self.stream = sd.OutputStream(
            samplerate=sr, channels=1, callback=self._audio_callback,
            blocksize=1024, latency='low'
        )
        self.stream.start()

        self.select_btn.setEnabled(True)
        self.pause_btn.setEnabled(True)

    def _audio_callback(self, outdata, frames, time, status):
        with self.lock:
            if self.paused or self.audio_data is None:
                outdata.fill(0)
                return
            start = self.read_index
            end = start + frames
            data_len = len(self.audio_data)
            if start >= data_len:
                outdata.fill(0)
                return
            avail = min(frames, data_len - start)
            if avail < frames:
                outdata[:avail, 0] = self.audio_data[start:start + avail] * self.volume
                outdata[avail:, 0] = 0
                self.read_index = data_len
            else:
                outdata[:, 0] = self.audio_data[start:start + frames] * self.volume
                self.read_index += frames

    # ==================== 封面（本地路径 / 网络 URL） ====================
    def _load_cover_from_url(self, url: str):
        if not url:
            return
        self._cover_thread = CoverDownloadThread(url, self)
        self._cover_thread.finished.connect(self._on_cover_data)
        self._cover_thread.start()

    def _on_cover_data(self, data):
        pix = QPixmap()
        if pix.loadFromData(data):
            self._set_cover_background(pix)

    def _set_cover_background(self, pix_or_path):
        if isinstance(pix_or_path, QPixmap):
            pixmap = pix_or_path
        else:
            pixmap = QPixmap(pix_or_path) if pix_or_path and os.path.exists(pix_or_path) else None
        if pixmap and not pixmap.isNull():
            self.bg_label.setPixmap(pixmap)
            self.bg_label.setScaledContents(True)
            self.bg_label.show()
            QTimer.singleShot(0, self._update_bg_geometry)
            return
        self.bg_label.hide()
        self.bg_label.clear()
        self.bg_label.setScaledContents(False)

    # ==================== 歌词解析 ====================
    def _parse_lrc(self, lrc_path: str) -> List[Tuple[float, str]]:
        lyrics = []
        pattern = re.compile(r'\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)')
        try:
            with open(lrc_path, 'r', encoding='utf-8') as f:
                for line in f:
                    m = pattern.match(line.strip())
                    if m:
                        minute, sec, ms_str, text = m.groups()
                        # LRC 规范：2 位为厘秒，3 位为毫秒
                        ms = int(ms_str) * 10 if len(ms_str) == 2 else int(ms_str)
                        total = int(minute) * 60 + int(sec) + ms / 1000.0
                        if text.strip():
                            lyrics.append((total, text.strip()))
        except Exception:
            pass
        return lyrics

    def _parse_lrc_text(self, text: str) -> List[Tuple[float, str]]:
        lyrics = []
        pattern = re.compile(r'\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)')
        for line in text.splitlines():
            m = pattern.match(line.strip())
            if m:
                minute, sec, ms_str, content = m.groups()
                # LRC 规范：2 位为厘秒，3 位为毫秒
                ms = int(ms_str) * 10 if len(ms_str) == 2 else int(ms_str)
                total = int(minute) * 60 + int(sec) + ms / 1000.0
                if content.strip():
                    lyrics.append((total, content.strip()))
        return lyrics

    def _refresh_lyric_list(self):
        self.lyric_list.clear()
        if self.lyrics:
            for _, text in self.lyrics:
                item = QListWidgetItem(text)
                item.setForeground(QColor(44, 62, 80))
                self.lyric_list.addItem(item)
        else:
            self.lyric_list.addItem("（无歌词）")

    def _parse_duration(self, duration_str) -> Optional[float]:
        if not duration_str:
            return None
        if isinstance(duration_str, (int, float)):
            return float(duration_str)
        try:
            parts = str(duration_str).strip().split(':')
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except Exception:
            return None
        return None

    # ==================== UI 更新（核心） ====================
    def _update_ui(self):
        if self.audio_data is not None:
            self._update_local_ui()
        elif self.stream_url is not None:
            self._update_stream_ui()

    def _update_local_ui(self):
        if self.audio_data is None or self.is_dragging:
            return
        with self.lock:
            idx = self.read_index
        total = len(self.audio_data)
        if total > 0:
            progress = (idx / total) * 100
            self.progress_slider.setValue(int(progress))

        cur_time = idx / self.sample_rate
        self.time_label.setText(f"{self._format_time(cur_time)} / {self._format_time(self.total_time)}")
        self._update_lyric(cur_time)

        if idx < total and self.norm_stft is not None:
            self._update_spectrum_local(idx)

        if idx >= total and total > 0:
            self._playback_finished()

    def _update_stream_ui(self):
        if self.is_dragging:
            return
        player = self._player
        if player.state() == PlayerState.StoppedState:
            return
        # 连接超时（15 秒仍未收到任何音频数据）
        if (not self._got_pcm and time.time() - self._stream_start_time > 15
                and player.state() == PlayerState.PlayingState):
            QMessageBox.critical(self, "流式播放失败", "连接超时，请检查网络或链接是否有效。")
            self._playback_finished()
            return

        if self.total_time > 0:
            cur_time = player.position() / 1000.0
            self.progress_slider.setValue(int(cur_time / self.total_time * 100))
            self.time_label.setText(f"{self._format_time(cur_time)} / {self._format_time(self.total_time)}")
            self._update_lyric(cur_time)

        if not self._got_pcm and float(np.max(np.abs(player.pcm_frame))) > 0.001:
            self._got_pcm = True
        self._update_spectrum_from_pcm()

    # ==================== 频谱计算与渲染（两模式共用） ====================
    def _update_spectrum_local(self, idx):
        if self.paused or self.norm_stft is None or self.frame_count == 0:
            return
        frame = int((idx / self.sample_rate) * self.frames_per_second)
        frame = max(0, min(frame, self.frame_count - 1))
        self._update_spectrum_common(self.norm_stft[:, frame])

    def _update_spectrum_from_pcm(self):
        if self._mel_basis is None:
            return
        cur = self._player.pcm_frame
        if self._pcm_tail is None:
            frame = np.concatenate([np.zeros(len(cur), dtype=np.float32), cur])
        else:
            frame = np.concatenate([self._pcm_tail, cur])
        self._pcm_tail = cur
        norm, self._running_max = self._mel_normalize(
            frame, self._mel_basis, self._fft_window, self._running_max)
        self._update_spectrum_common(norm)

    @staticmethod
    def _mel_normalize(pcm_frame: np.ndarray, mel_basis: np.ndarray,
                       fft_window: np.ndarray, running_max: np.ndarray,
                       decay: float = 0.995) -> Tuple[np.ndarray, np.ndarray]:
        """将一段 PCM 转为与离线模式一致的归一化 Mel 频谱（45 频段）。

        与离线管线等价：n_fft 加窗 → |STFT| → Mel 滤波器组 → log1p → 归一化。
        归一化用带慢衰减的运行最大值近似离线版“按频段列最大值”。
        """
        n = len(fft_window)
        if len(pcm_frame) >= n:
            frame = pcm_frame[-n:]
        else:
            frame = np.zeros(n, dtype=np.float32)
            frame[n - len(pcm_frame):] = pcm_frame
        spec = np.fft.rfft(frame * fft_window)
        mel = mel_basis @ np.abs(spec)
        log_mel = np.log1p(mel)
        running_max = np.maximum(log_mel, running_max * decay)
        denom = np.maximum(running_max, 1e-6)
        norm = (log_mel / denom).clip(0.0, 1.0).astype(np.float32)
        return norm, running_max

    def _update_spectrum_common(self, raw):
        raw_enhanced = raw ** 2

        alpha = self.smooth_alpha
        self.smooth_bar_vals = alpha * raw + (1 - alpha) * self.smooth_bar_vals
        smoothed_bar = self.smooth_bar_vals

        cmap = plt.cm.viridis
        norm = Normalize(vmin=0, vmax=1)
        colors = cmap(norm(smoothed_bar))
        for rect, val, color in zip(self.bar_rects, smoothed_bar, colors):
            rect.set_height(val)
            rect.set_color(color)
        self.bar_ax.set_ylim(0, self.y_max)
        self.bar_canvas.draw_idle()

        ring_idx = np.linspace(0, self.fft_bins - 1, self.ring_bins, dtype=int)
        raw_ring = raw[ring_idx]
        self.smooth_ring_vals = alpha * raw_ring + (1 - alpha) * self.smooth_ring_vals
        smoothed_ring = self.smooth_ring_vals

        ring_colors = cmap(norm(smoothed_ring))
        radii = 0.2 + 0.8 * smoothed_ring
        for i, (angle, radius, color, val) in enumerate(
                zip(self.ring_angles, radii, ring_colors, smoothed_ring)):
            self.ring_lines[i].set_data([angle, angle], [0, radius])
            self.ring_lines[i].set_color(color)
            self.ring_dots[i].set_offsets([[angle, radius]])
            self.ring_dots[i].set_sizes([20 + 80 * val])
            self.ring_dots[i].set_color(color)
        self.ring_ax.set_ylim(0, self.y_max)
        self.ring_canvas.draw_idle()

        if self.show_piano:
            self._update_counter += 1
            if self._update_counter % self._background_update_interval == 0:
                self.waterfall_data = np.roll(self.waterfall_data, shift=1, axis=0)
                self.waterfall_data[0, :] = raw_enhanced

                self.waterfall_rgba.fill(0)
                threshold = 0.02
                for col in range(self.fft_bins):
                    dest_col = col * (self.GAP_SIZE + 1)
                    values = self.waterfall_data[:, col]
                    mask = values > threshold
                    self.waterfall_rgba[mask, dest_col, 0] = 65
                    self.waterfall_rgba[mask, dest_col, 1] = 105
                    self.waterfall_rgba[mask, dest_col, 2] = 225
                    self.waterfall_rgba[mask, dest_col, 3] = 255

                self.waterfall_img.set_data(self.waterfall_rgba)
                self.waterfall_canvas.draw_idle()

    # ==================== 进度控制 ====================
    def _progress_press(self):
        if self.audio_data is None and self.stream_url is None:
            return
        self.is_dragging = True
        if self.stream_url:
            self._was_playing_before_drag = (self._player.state() == PlayerState.PlayingState)
            self._player.pause()
        else:
            with self.lock:
                self.paused = True
        self.pause_btn.setText("⏯️ 继续")

    def _progress_release(self):
        if self.audio_data is None and self.stream_url is None:
            return
        self.is_dragging = False
        if self.stream_url and self.total_time > 0:
            pos = self.progress_slider.value() / 100.0
            self._player.seek(int(pos * self.total_time * 1000))
            if self._was_playing_before_drag:
                self._player.play()
            self.pause_btn.setText("⏸ 暂停")
        else:
            with self.lock:
                self.paused = False
            self.pause_btn.setText("⏸ 暂停")
            if self.stream and not self.stream.active:
                self.stream.start()
            self._progress_changed(self.progress_slider.value())

    def _progress_changed(self, val):
        if self.audio_data is not None:
            pos = val / 100.0
            new_idx = int(pos * len(self.audio_data))
            with self.lock:
                self.read_index = new_idx
            cur = new_idx / self.sample_rate
        elif self.stream_url and self.total_time > 0:
            pos = val / 100.0
            cur = pos * self.total_time
        else:
            return
        self.time_label.setText(f"{self._format_time(cur)} / {self._format_time(self.total_time)}")

    # ==================== 播放控制 ====================
    def _toggle_pause(self):
        if self.audio_data is None and self.stream_url is None:
            return
        if self.stream_url:
            st = self._player.state()
            if st == PlayerState.PlayingState:
                self._player.pause()
                self.pause_btn.setText("⏯️ 继续")
            elif st == PlayerState.PausedState:
                self._player.play()
                self.pause_btn.setText("⏸ 暂停")
            else:
                # 已停止：从头重新播放
                self._player.seek(0)
                self._player.play()
                self.pause_btn.setText("⏸ 暂停")
        else:
            with self.lock:
                self.paused = not self.paused
            self.pause_btn.setText("⏯️ 继续" if self.paused else "⏸ 暂停")
            if not self.paused and self.stream and not self.stream.active:
                self.stream.start()

    def _volume_change(self, val):
        if self.stream_url:
            self._player.set_volume(val)
        else:
            self.volume = val / 100.0

    def _playback_finished(self):
        self._player.stop()
        self.pause_btn.setText("⏯️ 继续")
        self.paused = True
        self.progress_slider.setValue(0)
        self.time_label.setText(f"{self._format_time(self.total_time)} / {self._format_time(self.total_time)}")

    # ==================== 辅助 ====================
    def _update_lyric(self, cur_time):
        if not self.lyrics:
            return
        new_idx = -1
        for i, (t, _) in enumerate(self.lyrics):
            if t <= cur_time:
                new_idx = i
            else:
                break
        if new_idx == self.current_lyric_index:
            return

        if self.current_lyric_index != -1:
            old = self.lyric_list.item(self.current_lyric_index)
            if old:
                old.setBackground(QColor(0, 0, 0, 0))
                old.setForeground(QColor(44, 62, 80))
                f = old.font()
                f.setBold(False)
                f.setPointSize(10)
                old.setFont(f)

        if new_idx != -1 and new_idx < self.lyric_list.count():
            new = self.lyric_list.item(new_idx)
            if new:
                new.setBackground(QColor(74, 144, 217, 80))
                new.setForeground(QColor(0, 0, 0))
                f = new.font()
                f.setBold(True)
                f.setPointSize(14)
                new.setFont(f)

                QTimer.singleShot(10, lambda: self.lyric_list.scrollToItem(
                    new, QAbstractItemView.PositionAtCenter
                ))

        self.current_lyric_index = new_idx

    @staticmethod
    def _format_time(sec):
        return f"{int(sec // 60):02d}:{int(sec % 60):02d}"

    def _select_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择音频文件", "",
            "Audio Files (*.mp3 *.wav *.flac *.ogg *.m4a);;All Files (*.*)"
        )
        if path:
            self.load_audio(path)

    def toggle_piano(self, checked):
        self.show_piano = checked
        self.waterfall_canvas.setVisible(checked)
        self.btn_piano.setText("🎹 钢琴流" if checked else "🎹 隐藏")
        if checked:
            self._update_background_forced()

    def _update_background_forced(self):
        if not self.show_piano or self.norm_stft is None:
            return
        with self.lock:
            idx = self.read_index
        if self.audio_data is None:
            return
        frame = int((idx / self.sample_rate) * self.frames_per_second)
        frame = max(0, min(frame, self.frame_count - 1))
        raw = self.norm_stft[:, frame] ** 2
        self.waterfall_data = np.roll(self.waterfall_data, shift=1, axis=0)
        self.waterfall_data[0, :] = raw
        self.waterfall_rgba.fill(0)
        threshold = 0.02
        for col in range(self.fft_bins):
            dest_col = col * (self.GAP_SIZE + 1)
            values = self.waterfall_data[:, col]
            mask = values > threshold
            self.waterfall_rgba[mask, dest_col, 0] = 65
            self.waterfall_rgba[mask, dest_col, 1] = 105
            self.waterfall_rgba[mask, dest_col, 2] = 225
            self.waterfall_rgba[mask, dest_col, 3] = 255
        self.waterfall_img.set_data(self.waterfall_rgba)
        self.waterfall_canvas.draw_idle()

    def _update_bg_geometry(self):
        rect = self.content_widget.rect()
        if hasattr(self, 'bg_label') and self.bg_label.isVisible():
            self.bg_label.setGeometry(rect)
            self.bg_label.setMask(self._round_mask())
        if hasattr(self, 'waterfall_canvas'):
            self.waterfall_canvas.setGeometry(rect)

    def _round_mask(self):
        rect = self.content_widget.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return QRegion(rect, QRegion.Rectangle)
        path = QPainterPath()
        radius = 8
        path.addRoundedRect(QRectF(rect), radius, radius)
        return QRegion(path.toFillPolygon().toPolygon())

    def _make_btn(self, text, slot):
        btn = QPushButton(text)
        btn.setFixedHeight(32)
        btn.clicked.connect(slot)
        return btn

    def _title_mouse_press(self, e):
        if e.button() == Qt.LeftButton:
            self.drag_pos = e.globalPos()
            self.dragging = True
            e.accept()

    def _title_mouse_move(self, e):
        if self.dragging:
            self.move(self.pos() + e.globalPos() - self.drag_pos)
            self.drag_pos = e.globalPos()
            e.accept()

    def _title_mouse_release(self, e):
        if e.button() == Qt.LeftButton:
            self.dragging = False
            e.accept()

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            self.max_btn.setText("□")
        else:
            self.showMaximized()
            self.max_btn.setText("❐")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_bg_geometry()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._update_bg_geometry)

    def closeEvent(self, e):
        self.timer.stop()
        self._player.stop()
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        if self._cover_thread and self._cover_thread.isRunning():
            self._cover_thread.quit()
            self._cover_thread.wait()

        if self.load_thread and self.load_thread.isRunning():
            self.load_thread.quit()
            self.load_thread.wait()

        if hasattr(self, 'bar_canvas'):
            self.bar_canvas.figure.clear()
            self.bar_canvas.deleteLater()
        if hasattr(self, 'ring_canvas'):
            self.ring_canvas.figure.clear()
            self.ring_canvas.deleteLater()
        if hasattr(self, 'waterfall_canvas'):
            self.waterfall_canvas.figure.clear()
            self.waterfall_canvas.deleteLater()
        plt.close('all')

        super().closeEvent(e)
