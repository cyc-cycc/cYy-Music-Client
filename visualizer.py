# -*- coding: utf-8 -*-
import os
import sys
import re
import threading
import glob
import numpy as np
import sounddevice as sd
import librosa
import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize

from PyQt5.QtCore import Qt, QTimer, QPoint, QObject, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPixmap, QMouseEvent
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QListWidget, QListWidgetItem, QFileDialog,
    QFrame, QSplitter, QMessageBox, QAbstractItemView
)

class UpdateSignals(QObject):
    update_ui = pyqtSignal()

class AudioVisualizer(QMainWindow):
    def __init__(self, audio_path: str = None, lyric_path: str = None,
                 cover_path: str = None, parent=None):
        super().__init__(parent)
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
        self.volume = 0.6
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

        self.cover_path = cover_path

        self.drag_pos = QPoint()
        self.dragging = False

        self.signals = UpdateSignals()
        self.signals.update_ui.connect(self._update_ui)
        self.timer = QTimer()
        self.timer.timeout.connect(self.signals.update_ui.emit)
        self.timer.start(30)

        self._init_ui()

        if audio_path and os.path.exists(audio_path):
            self.load_audio(audio_path, lyric_path, cover_path)

    def _init_ui(self):
        central = QFrame()
        central.setStyleSheet("background: #F5F7FA; border-radius: 8px;")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        title_bar = QWidget()
        title_bar.setFixedHeight(40)
        title_bar.setStyleSheet(
            "background: #E8F0FE; border-bottom: 1px solid #BDC3C7;"
            "border-top-left-radius: 8px; border-top-right-radius: 8px;"
        )
        title_bar.mousePressEvent = self._title_mouse_press
        title_bar.mouseMoveEvent = self._title_mouse_move
        title_bar.mouseReleaseEvent = self._title_mouse_release

        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 0, 10, 0)
        title_layout.setSpacing(5)

        title_label = QLabel("🎵 音频可视化")
        title_label.setStyleSheet("color: #2C3E50; font-weight: bold; font-size: 14px;")
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        for symbol, slot in [("—", self.showMinimized), ("□", self._toggle_maximize), ("✕", self.close)]:
            btn = QPushButton(symbol)
            btn.setFixedSize(32, 32)
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #2C3E50; border: none; border-radius: 4px; font-size: 16px; }"
                "QPushButton:hover { background: #D5D8DC; }"
            )
            if symbol == "✕":
                btn.setStyleSheet(btn.styleSheet() + "QPushButton:hover { background: #E74C3C; color: white; }")
            btn.clicked.connect(slot)
            title_layout.addWidget(btn)
            if symbol == "□":
                self.max_btn = btn

        main_layout.addWidget(title_bar)

        self.content_widget = QWidget()
        self.content_widget.setStyleSheet(
            "background: transparent; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;"
        )
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(10)

        self.bg_label = QLabel(self.content_widget)
        self.bg_label.setScaledContents(True)
        self.bg_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.bg_label.hide()
        self.bg_label.setGeometry(self.content_widget.rect())

        splitter = QSplitter(Qt.Horizontal)
        content_layout.addWidget(splitter, 1)

        left_widget = QWidget()
        left_widget.setStyleSheet("background: rgba(255,255,255,0.8); border-radius: 8px;")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.setSpacing(5)

        self.song_title_label = QLabel("未选择歌曲")
        self.song_title_label.setAlignment(Qt.AlignCenter)
        self.song_title_label.setFixedHeight(35)
        self.song_title_label.setStyleSheet(
            "background: transparent; color: #2C3E50; font-weight: bold; font-size: 16px; padding: 5px;"
        )
        left_layout.addWidget(self.song_title_label)

        self.lyric_list = QListWidget()
        self.lyric_list.setSelectionMode(QListWidget.NoSelection)
        self.lyric_list.setWordWrap(True)
        self.lyric_list.setFont(QFont("Microsoft YaHei", 11))
        self.lyric_list.setStyleSheet(
            "QListWidget { background: transparent; color: #2C3E50; border: none; }"
            "QListWidget::item { padding: 2px 5px; }"
        )
        left_layout.addWidget(self.lyric_list, 1)

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
        self.bar_fig.tight_layout(pad=0)
        self.bar_canvas = FigureCanvas(self.bar_fig)
        self.bar_canvas.setStyleSheet("background: transparent; border: none;")
        left_layout.addWidget(self.bar_canvas, 1)
        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_widget.setStyleSheet("background: rgba(255,255,255,0.8); border-radius: 8px;")
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
        self.ring_fig.tight_layout(pad=0)
        self.ring_canvas = FigureCanvas(self.ring_fig)
        self.ring_canvas.setStyleSheet("background: transparent; border: none;")
        right_layout.addWidget(self.ring_canvas)
        splitter.addWidget(right_widget)
        splitter.setSizes([550, 350])

        control = QWidget()
        control.setStyleSheet("background: rgba(255,255,255,0.8); border-radius: 8px; padding: 5px;")
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
        self.time_label.setStyleSheet("color: #2C3E50; font-size: 13px;")
        control_layout.addWidget(self.time_label)

        control_layout.addWidget(QLabel("🔊"))

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(60)
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

    def _make_btn(self, text, slot):
        btn = QPushButton(text)
        btn.setFixedHeight(32)
        btn.clicked.connect(slot)
        btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.8); color: #2C3E50; border: 1px solid #BDC3C7; border-radius: 4px; padding: 5px 12px; font-weight: bold; }"
            "QPushButton:hover { background: #E8F0FE; border-color: #4A90D9; }"
            "QPushButton:pressed { background: rgba(255,255,255,0.5); }"
            "QPushButton:disabled { color: #BDC3C7; border-color: #D5D8DC; }"
        )
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
        if hasattr(self, 'bg_label'):
            self.bg_label.setGeometry(self.content_widget.rect())
            self.bg_label.lower()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'bg_label') and self.cover_path:
            self.bg_label.setGeometry(self.content_widget.rect())
            self.bg_label.lower()

    def _set_cover_background(self, image_path):
        if image_path and os.path.exists(image_path):
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                self.bg_label.setPixmap(pixmap)
                self.bg_label.show()
                self.bg_label.setGeometry(self.content_widget.rect())
                return
        self.bg_label.hide()
        self.bg_label.clear()

    def load_audio(self, audio_path, lyric_path=None, cover_path=None):
        if not os.path.exists(audio_path):
            return
        try:
            data, sr = librosa.load(audio_path, sr=None, mono=True)
            self.audio_data = data.astype(np.float32)
            self.sample_rate = sr
            self.read_index = 0
            self.paused = False
            self.pause_btn.setText("⏸ 暂停")
            self.pause_btn.setEnabled(True)

            hop_length = 512
            n_fft = 2048
            D = librosa.stft(self.audio_data, n_fft=n_fft, hop_length=hop_length)
            mag = np.abs(D)
            mel_basis = librosa.filters.mel(sr=sr, n_fft=n_fft, n_mels=self.fft_bins)
            mel_spec = np.dot(mel_basis, mag)
            log_mel = np.log1p(mel_spec)
            col_max = np.max(log_mel, axis=0)
            col_max[col_max == 0] = 1.0
            self.norm_stft = (log_mel / col_max[np.newaxis, :]).clip(0.0, 1.0).astype(np.float32)
            self.frame_count = self.norm_stft.shape[1]
            self.frames_per_second = sr / hop_length

            if lyric_path and os.path.exists(lyric_path):
                self.lyrics = self._parse_lrc(lyric_path)
            else:
                default_lrc = os.path.splitext(audio_path)[0] + ".lrc"
                if os.path.exists(default_lrc):
                    self.lyrics = self._parse_lrc(default_lrc)
                else:
                    self.lyrics = []
            self.lyric_list.clear()
            for _, text in self.lyrics:
                item = QListWidgetItem(text)
                item.setForeground(QColor(44, 62, 80))
                self.lyric_list.addItem(item)
            if not self.lyrics:
                self.lyric_list.addItem("（无歌词）")
            self.current_lyric_index = -1

            self.song_title_label.setText(os.path.splitext(os.path.basename(audio_path))[0])

            if cover_path and os.path.exists(cover_path):
                self.cover_path = cover_path
            else:
                covers = glob.glob(os.path.splitext(audio_path)[0] + "_cover.*")
                self.cover_path = covers[0] if covers else None
            self._set_cover_background(self.cover_path)

            if self.stream:
                self.stream.stop()
                self.stream.close()
            self.stream = sd.OutputStream(
                samplerate=sr, channels=1, callback=self._audio_callback,
                blocksize=1024, latency='low'
            )
            self.stream.start()

            self.total_time = len(self.audio_data) / sr
            self.progress_slider.setValue(0)
            self.time_label.setText(f"00:00 / {self._format_time(self.total_time)}")

        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载音频失败: {e}")
            self.pause_btn.setEnabled(False)
            self.norm_stft = None

    def _parse_lrc(self, lrc_path):
        lyrics = []
        pattern = re.compile(r'\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)')
        try:
            with open(lrc_path, 'r', encoding='utf-8') as f:
                for line in f:
                    m = pattern.match(line.strip())
                    if m:
                        minute, sec, ms, text = m.groups()
                        total = int(minute) * 60 + int(sec) + int(ms) / 1000.0
                        if text.strip():
                            lyrics.append((total, text.strip()))
        except Exception:
            pass
        return lyrics

    def _select_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择音频文件", "",
            "Audio Files (*.mp3 *.wav *.flac *.ogg *.m4a);;All Files (*.*)"
        )
        if path:
            self.load_audio(path)

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

    def _update_ui(self):
        if self.audio_data is None:
            return
        if self.is_dragging:
            return

        with self.lock:
            idx = self.read_index
        total = len(self.audio_data)

        if total > 0:
            progress = (idx / total) * 100
            if not self.is_dragging:
                self.progress_slider.setValue(int(progress))

        cur_time = idx / self.sample_rate
        self.time_label.setText(f"{self._format_time(cur_time)} / {self._format_time(self.total_time)}")

        self._update_lyric(cur_time)

        if idx < total and self.norm_stft is not None:
            self._update_spectrum(idx)

        if idx >= total and total > 0:
            self._playback_finished()

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
                old.setFont(f)

        if new_idx != -1 and new_idx < self.lyric_list.count():
            new = self.lyric_list.item(new_idx)
            if new:
                new.setBackground(QColor(74, 144, 217, 80))
                new.setForeground(QColor(0, 0, 0))
                f = new.font()
                f.setBold(True)
                new.setFont(f)
                self.lyric_list.scrollToItem(new, QListWidget.PositionAtCenter)

        self.current_lyric_index = new_idx

    def _update_spectrum(self, idx):
        if self.norm_stft is None or self.frame_count == 0:
            return

        frame = int((idx / self.sample_rate) * self.frames_per_second)
        frame = max(0, min(frame, self.frame_count - 1))
        raw = self.norm_stft[:, frame]

        alpha = self.smooth_alpha
        self.smooth_bar_vals = alpha * raw + (1 - alpha) * self.smooth_bar_vals
        smoothed_bar = self.smooth_bar_vals

        ring_idx = np.linspace(0, self.fft_bins - 1, self.ring_bins, dtype=int)
        raw_ring = raw[ring_idx]
        self.smooth_ring_vals = alpha * raw_ring + (1 - alpha) * self.smooth_ring_vals
        smoothed_ring = self.smooth_ring_vals

        cmap = plt.cm.viridis
        norm = Normalize(vmin=0, vmax=1)
        colors = cmap(norm(smoothed_bar))
        for rect, val, color in zip(self.bar_rects, smoothed_bar, colors):
            rect.set_height(val)
            rect.set_color(color)
        self.bar_ax.set_ylim(0, self.y_max)
        self.bar_canvas.draw_idle()

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

    def _playback_finished(self):
        if self.stream:
            self.stream.stop()
        self.pause_btn.setText("⏯️ 继续")
        self.paused = True
        self.progress_slider.setValue(0)
        self.time_label.setText(f"{self._format_time(self.total_time)} / {self._format_time(self.total_time)}")

    def _toggle_pause(self):
        if self.audio_data is None:
            return
        with self.lock:
            self.paused = not self.paused
        self.pause_btn.setText("⏯️ 继续" if self.paused else "⏸ 暂停")
        if not self.paused and self.stream and not self.stream.active:
            self.stream.start()

    def _volume_change(self, val):
        self.volume = val / 100.0

    def _progress_press(self):
        if self.audio_data is None:
            return
        self.is_dragging = True
        with self.lock:
            self.paused = True
        self.pause_btn.setText("⏯️ 继续")

    def _progress_release(self):
        if self.audio_data is None:
            return
        self.is_dragging = False
        with self.lock:
            self.paused = False
        self.pause_btn.setText("⏸ 暂停")
        if self.stream and not self.stream.active:
            self.stream.start()
        self._progress_changed(self.progress_slider.value())

    def _progress_changed(self, val):
        if self.audio_data is None:
            return
        pos = val / 100.0
        new_idx = int(pos * len(self.audio_data))
        with self.lock:
            self.read_index = new_idx
        cur = new_idx / self.sample_rate
        self.time_label.setText(f"{self._format_time(cur)} / {self._format_time(self.total_time)}")

    @staticmethod
    def _format_time(sec):
        return f"{int(sec // 60):02d}:{int(sec % 60):02d}"

    def closeEvent(self, e):
        plt.close(self.bar_fig)
        plt.close(self.ring_fig)
        self.timer.stop()
        try:
            self.signals.update_ui.disconnect()
        except TypeError:
            pass
        if self.stream:
            self.stream.stop()
            self.stream.close()
        super().closeEvent(e)
