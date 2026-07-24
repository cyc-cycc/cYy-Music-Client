# -*- coding: utf-8 -*-
import os
import sys
import requests
from PyQt5.QtCore import Qt, QTimer, QPoint, QRect, QSize, pyqtSignal, QThread
from PyQt5.QtGui import QColor, QFont, QPixmap, QMouseEvent
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton, QLineEdit, QCheckBox, QSlider,
    QComboBox, QSpinBox, QGroupBox, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFormLayout, QTabWidget, QFileDialog, QDialog, QFrame, QListWidgetItem,
    QListWidget, QSizePolicy, QGraphicsDropShadowEffect
)
from constants import SOURCE_GROUPS, FILENAME_FORMATS, PLAYLIST_SOURCE_MAP, DEFAULT_SAVE_DIR

# ==================== 封面加载器（独立类） ====================
class CoverLoader(QThread):
    finished = pyqtSignal(bytes)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            resp = requests.get(self.url, timeout=10)
            if resp.status_code == 200:
                self.finished.emit(resp.content)
        except Exception:
            pass

# ==================== 歌曲卡片 ====================
class SongCard(QFrame):
    def __init__(self, song_info, source_display=None, parent=None):
        super().__init__(parent)
        self.song_info = song_info
        self.source_display = source_display
        self.setFrameStyle(QFrame.NoFrame)
        self.setObjectName("songCard")
        self.setFixedHeight(100)
        self._init_ui()
        self._load_cover()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(80, 80)
        self.cover_label.setStyleSheet("border-radius: 6px; background-color: #E8EDF2;")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setText("🎵")
        self.cover_label.setScaledContents(True)
        layout.addWidget(self.cover_label)

        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        self.name_label = QLabel(self.song_info.get('song_name', '未知歌曲'))
        self.name_label.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        self.name_label.setStyleSheet("color: #2C3E50;")
        info_layout.addWidget(self.name_label)

        self.singer_label = QLabel(self.song_info.get('singers', '未知歌手'))
        self.singer_label.setStyleSheet("color: #5D6D7E; font-size: 12px;")
        info_layout.addWidget(self.singer_label)

        album = self.song_info.get('album', '')
        duration = self.song_info.get('duration', '')
        detail_text = f"{album}" if album else ""
        if duration:
            detail_text += f"  •  {duration}" if detail_text else duration
        self.detail_label = QLabel(detail_text)
        self.detail_label.setStyleSheet("color: #7F8C8D; font-size: 14px;")
        info_layout.addWidget(self.detail_label)

        source = self.source_display or self.song_info.get('source', '')
        if source:
            self.source_label = QLabel(source)
            self.source_label.setStyleSheet(
                "background-color: #D5D8DC; color: #2C3E50; padding: 2px 8px; border-radius: 10px; font-size: 11px;"
            )
            self.source_label.setAlignment(Qt.AlignCenter)
            info_layout.addWidget(self.source_label)

        layout.addWidget(info_widget, 1)

        self.setStyleSheet("""
            #songCard {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                            stop:0 #FFFFFF, stop:1 #F4F6F9);
                border: 1px solid #DDE1E6;
                border-radius: 8px;
            }
            #songCard:hover {
                border: 1px solid #4A90D9;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                            stop:0 #F0F7FF, stop:1 #E1EEFA);
            }
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(0, 0, 0, 30))
        shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)

    def _load_cover(self):
        cover_url = self.song_info.get('cover_url') or self.song_info.get('cover')
        if cover_url:
            self.loader = CoverLoader(cover_url)   # 使用模块级类
            self.loader.finished.connect(self._set_cover_pixmap)
            self.loader.start()

    def _set_cover_pixmap(self, data):
        pix = QPixmap()
        if pix.loadFromData(data):
            scaled = pix.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.cover_label.setPixmap(scaled)
            self.cover_label.setText("")

    def set_selected(self, selected):
        if selected:
            self.setStyleSheet(self.styleSheet() + """
                #songCard { border: 2px solid #4A90D9; background: #E8F0FE; }
            """)
        else:
            self.setStyleSheet("""
                #songCard {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                stop:0 #FFFFFF, stop:1 #F4F6F9);
                    border: 1px solid #DDE1E6;
                    border-radius: 8px;
                }
                #songCard:hover {
                    border: 1px solid #4A90D9;
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                stop:0 #F0F7FF, stop:1 #E1EEFA);
                }
            """)

# ==================== 可点击滑动条 ====================
class ClickableSlider(QSlider):
    def mousePressEvent(self, event: QMouseEvent):
        try:
            if event.button() != Qt.LeftButton:
                return super().mousePressEvent(event)
            opt_width = self.width()
            if opt_width <= 0:
                return super().mousePressEvent(event)
            x = event.pos().x()
            x = max(0, min(x, opt_width))
            span = self.maximum() - self.minimum()
            if span <= 0:
                val = self.minimum()
            else:
                ratio = x / opt_width
                val = int(self.minimum() + ratio * span)
            self.setValue(val)
            try:
                self.sliderMoved.emit(val)
            except Exception:
                pass
            event.accept()
        except Exception:
            super().mousePressEvent(event)

# ==================== 滚动标签 ====================
class MarqueeLabel(QLabel):
    def __init__(self, parent=None, interval=150):
        super().__init__(parent)
        self._offset = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._scroll)
        self._timer.setInterval(interval)
        self._full_text = ""
        self._display_text = ""
        self._scroll_enabled = False
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(30)
        self.setStyleSheet("background: transparent;")

    def setText(self, text: str):
        self._full_text = text
        self._offset = 0
        self._timer.stop()
        self._update_display()

    def _update_display(self):
        if not self._full_text:
            super().setText("")
            return
        fm = self.fontMetrics()
        text_width = fm.horizontalAdvance(self._full_text)
        label_width = self.width() - 10
        if label_width <= 0:
            label_width = self.width()
        if text_width <= label_width:
            super().setText(self._full_text)
            self._timer.stop()
            self._scroll_enabled = False
            return
        self._scroll_enabled = True
        if self._offset >= len(self._full_text):
            self._offset = 0
        available = label_width
        chars = list(self._full_text)
        end = self._offset
        while True:
            test_text = self._full_text[self._offset:end+1]
            if fm.horizontalAdvance(test_text) > available:
                break
            end += 1
            if end > len(self._full_text):
                end = len(self._full_text)
                break
        display = self._full_text[self._offset:end]
        while fm.horizontalAdvance(display) > available and len(display) > 1:
            display = display[:-1]
        if not display:
            display = self._full_text[0]
        super().setText(display)
        if not self._timer.isActive() and self._scroll_enabled:
            self._timer.start()

    def _scroll(self):
        if not self._full_text:
            return
        self._offset += 1
        if self._offset >= len(self._full_text):
            self._offset = 0
        self._update_display()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_display()

    def enterEvent(self, event):
        if self._timer.isActive():
            self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._scroll_enabled and not self._timer.isActive():
            self._timer.start()
        super().leaveEvent(event)

# ==================== 设置对话框 ====================
class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumSize(600, 250)
        self.setModal(True)
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        source_tab = QWidget()
        source_layout = QVBoxLayout(source_tab)
        group_layout = QHBoxLayout()
        group_layout.setSpacing(20)

        self.source_checkboxes = []

        for group_name, source_names in SOURCE_GROUPS.items():
            group_box = QGroupBox(group_name)
            group_box.setFlat(True)
            grid = QGridLayout()
            grid.setSpacing(5)
            row, col = 0, 0
            for name in source_names:
                cb = QCheckBox(name)
                cb.setChecked("推荐" in name)
                self.source_checkboxes.append(cb)
                grid.addWidget(cb, row, col)
                col += 1
                if col >= 2:
                    col = 0
                    row += 1
            group_box.setLayout(grid)
            group_layout.addWidget(group_box)
        group_layout.addStretch()
        source_layout.addLayout(group_layout)

        form_layout = QFormLayout()
        self.spin_limit = QSpinBox()
        self.spin_limit.setMinimum(1)
        self.spin_limit.setMaximum(50)
        self.spin_limit.setValue(5)
        form_layout.addRow("每源条数:", self.spin_limit)

        self.check_dedup = QCheckBox("去重")
        self.check_dedup.setToolTip("根据歌曲名和歌手去重，保留第一个来源")
        form_layout.addRow(self.check_dedup)

        source_layout.addLayout(form_layout)
        tabs.addTab(source_tab, "搜索源")

        download_tab = QWidget()
        download_layout = QVBoxLayout(download_tab)

        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setText(DEFAULT_SAVE_DIR)
        path_layout.addWidget(QLabel("保存路径:"))
        path_layout.addWidget(self.path_edit)
        self.btn_browse = QPushButton("浏览...")
        self.btn_browse.clicked.connect(self.browse_path)
        path_layout.addWidget(self.btn_browse)
        self.btn_default = QPushButton("默认")
        self.btn_default.clicked.connect(lambda: self.path_edit.setText(DEFAULT_SAVE_DIR))
        path_layout.addWidget(self.btn_default)
        self.btn_desktop = QPushButton("桌面")
        self.btn_desktop.clicked.connect(lambda: self.path_edit.setText(
            os.path.join(os.path.expanduser("~"), "Desktop")
        ))
        path_layout.addWidget(self.btn_desktop)
        download_layout.addLayout(path_layout)

        fmt_layout = QHBoxLayout()
        self.format_combo = QComboBox()
        self.format_combo.addItems(FILENAME_FORMATS)
        self.format_custom_edit = QLineEdit()
        self.format_custom_edit.setPlaceholderText("例如: {歌手}/{专辑}/{歌曲名}")
        self.format_custom_edit.hide()
        self.format_combo.currentIndexChanged.connect(self.on_format_changed)
        fmt_layout.addWidget(QLabel("文件名格式:"))
        fmt_layout.addWidget(self.format_combo)
        fmt_layout.addWidget(self.format_custom_edit)
        download_layout.addLayout(fmt_layout)

        self.check_lyric = QCheckBox("下载歌词")
        self.check_cover = QCheckBox("下载封面")
        self.check_cover.setChecked(True)
        download_layout.addWidget(self.check_lyric)
        download_layout.addWidget(self.check_cover)

        label = QLabel("Made By cYy")
        label.setAlignment(Qt.AlignRight)
        download_layout.addWidget(label)

        tabs.addTab(download_tab, "下载设置")

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("确定")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addStretch()
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

    def browse_path(self):
        path = QFileDialog.getExistingDirectory(self, "选择保存目录", self.path_edit.text())
        if path:
            self.path_edit.setText(path)

    def on_format_changed(self, index):
        if self.format_combo.currentText() == "自定义":
            self.format_custom_edit.show()
        else:
            self.format_custom_edit.hide()

    def load_settings(self):
        if self.parent() and hasattr(self.parent(), 'settings'):
            settings = self.parent().settings
            pass

    def get_settings(self):
        selected_sources = [cb.text() for cb in self.source_checkboxes if cb.isChecked()]
        return {
            'sources': selected_sources,
            'limit': self.spin_limit.value(),
            'dedup': self.check_dedup.isChecked(),
            'save_dir': self.path_edit.text().strip(),
            'filename_format': self.format_combo.currentText(),
            'custom_format': self.format_custom_edit.text().strip(),
            'download_lyric': self.check_lyric.isChecked(),
            'download_cover': self.check_cover.isChecked()
        }
