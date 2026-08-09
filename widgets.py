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

from constants import THEMES

# 使用 utils._download_image_data 来统一封面下载行为（大小限制与格式检测）
from utils import _download_image_data

# ==================== 封面加载器（独立类） ====================
class CoverLoader(QThread):
    finished = pyqtSignal(bytes)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            data, ext = _download_image_data(self.url, {}, max_size=5 * 1024 * 1024, session=None)
            if data:
                self.finished.emit(data)
        except Exception:
            pass

# ==================== 歌曲卡片 ====================
class SongCard(QFrame):
    def __init__(self, song_info, source_display=None, parent=None):
        super().__init__(parent)
        self.song_info = song_info
        self.source_display = source_display
        self.setFrameStyle(QFrame.NoFrame)
        self.setObjectName("songCard")   # 新增
        self.setFixedHeight(100)
        self._init_ui()
        self._load_cover()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(80, 80)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setText("🎵")
        self.cover_label.setScaledContents(True)
        layout.addWidget(self.cover_label)

        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        self.name_label = QLabel(self.song_info.get('song_name', '未知歌曲'))
        self.name_label.setObjectName("titleLabel")
        self.name_label.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        info_layout.addWidget(self.name_label)

        self.singer_label = QLabel(self.song_info.get('singers', '未知歌手'))
        self.name_label.setObjectName("titleLabel")
        info_layout.addWidget(self.singer_label)

        album = self.song_info.get('album', '')
        duration = self.song_info.get('duration', '')
        file_size = self.song_info.get('file_size', '')

        detail_text = f"{album}" if album else ""
        if duration:
            detail_text += f"  •  {duration}" if detail_text else duration
        if file_size:
            detail_text += f"  •  {file_size}" if detail_text else file_size

        self.detail_label = QLabel(detail_text)
        self.detail_label.setObjectName("subLabel")
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

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(0, 0, 0, 30))
        shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)

    def _load_cover(self):
        cover_url = self.song_info.get('cover_url') or self.song_info.get('cover')
        if cover_url:
            self.loader = CoverLoader(cover_url)
            self.loader.finished.connect(self._set_cover_pixmap)
            self.loader.start()

    def _set_cover_pixmap(self, data):
        pix = QPixmap()
        if pix.loadFromData(data):
            scaled = pix.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.cover_label.setPixmap(scaled)
            self.cover_label.setText("")

    def set_selected(self, selected):
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

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

# ==================== 设置对话框（带自定义标题栏，新增格式转换） ====================
class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumSize(620, 400)  # 调高以容纳新选项
        self.resize(620, 400)
        
        # 拖拽相关
        self.drag_pos = QPoint()
        self.dragging = False
        
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---------- 标题栏 ----------
        title_bar = QWidget()
        title_bar.setObjectName("settingsTitleBar")
        title_bar.setFixedHeight(40)

        title_bar.mousePressEvent = self._title_mouse_press
        title_bar.mouseMoveEvent = self._title_mouse_move
        title_bar.mouseReleaseEvent = self._title_mouse_release

        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 0, 10, 0)
        title_layout.setSpacing(5)

        icon_label = QLabel("⚙️")
        title_layout.addWidget(icon_label)

        title_label = QLabel("设置")
        title_layout.addWidget(title_label)

        title_layout.addStretch()

        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("titleCloseButton")
        self.btn_close.setFixedSize(32, 32)
        self.btn_close.clicked.connect(self.close)
        title_layout.addWidget(self.btn_close)

        main_layout.addWidget(title_bar)

        # ---------- 内容区域 ----------
        content_widget = QWidget()
        content_widget.setObjectName("settingsContent")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(15, 15, 15, 15)
        content_layout.setSpacing(10)

        self._create_content(content_layout)

        main_layout.addWidget(content_widget)

    def _create_content(self, parent_layout):
        tabs = QTabWidget()
        tabs.setStyleSheet("QTabWidget::tab-bar { left: 5px; }")
        parent_layout.addWidget(tabs)

        # 搜索源标签页
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
        
        # 主题选择标签页
        theme_tab = QWidget()
        theme_layout = QFormLayout(theme_tab)
        theme_layout.setSpacing(10)
        
        self.theme_combo = QComboBox()
        self.theme_combo.addItems([THEMES['light']['display_name'], THEMES['dark']['display_name']])
        self.theme_combo.setCurrentIndex(0)
        theme_layout.addRow("颜色主题:", self.theme_combo)

        opacity_layout = QHBoxLayout()
        opacity_layout.addWidget(QLabel("窗口透明度:"))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(50, 100)          # 对应 0.50 ~ 1.00
        self.opacity_slider.setValue(80)
        self.opacity_slider.setFixedWidth(150)
        self.opacity_label = QLabel("80%")
        self.opacity_slider.valueChanged.connect(
            lambda v: self.opacity_label.setText(f"{v}%")
        )
        opacity_layout.addWidget(self.opacity_slider)
        opacity_layout.addWidget(self.opacity_label)
        opacity_layout.addStretch()
        theme_layout.addRow(opacity_layout)
        
        tabs.addTab(theme_tab, "主题设置")

        # 下载设置标签页
        download_tab = QWidget()
        download_layout = QVBoxLayout(download_tab)

        # 路径
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

        # 文件名格式
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

        # 歌词/封面
        # 第一行：下载歌词 + 嵌入歌词
        lyric_row = QHBoxLayout()
        self.check_lyric = QCheckBox("下载歌词")
        self.check_embed_lyrics = QCheckBox("嵌入歌词到音频并删除 .lrc 文件")
        lyric_row.addWidget(self.check_lyric)
        lyric_row.addWidget(self.check_embed_lyrics)
        lyric_row.addStretch()
        download_layout.addLayout(lyric_row)

        # 第二行：下载封面 + 嵌入封面
        cover_row = QHBoxLayout()
        self.check_cover = QCheckBox("下载封面")
        self.check_cover.setChecked(True)
        self.check_embed_cover = QCheckBox("嵌入封面到音频并删除封面图片")
        cover_row.addWidget(self.check_cover)
        cover_row.addWidget(self.check_embed_cover)
        cover_row.addStretch()
        download_layout.addLayout(cover_row)

        # ===== 新增：格式转换 =====
        convert_group = QGroupBox("下载后转换格式")
        convert_layout = QVBoxLayout(convert_group)
        conv_row1 = QHBoxLayout()
        self.convert_check = QCheckBox("启用转换")
        self.convert_check.setChecked(False)
        conv_row1.addWidget(self.convert_check)
        conv_row1.addWidget(QLabel("目标格式:"))
        self.convert_combo = QComboBox()
        self.convert_combo.addItems(["mp3", "aac", "ogg", "flac"])
        self.convert_combo.setEnabled(False)
        self.convert_check.toggled.connect(self.convert_combo.setEnabled)
        conv_row1.addWidget(self.convert_combo)
        conv_row1.addWidget(QLabel("比特率:"))
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.setEnabled(False)
        conv_row1.addWidget(self.bitrate_combo)
        conv_row1.addStretch()
        convert_layout.addLayout(conv_row1)
        download_layout.addWidget(convert_group)

        self.convert_combo.currentIndexChanged.connect(self._update_bitrate_options)
        self.convert_check.toggled.connect(self._update_bitrate_options)
        self._update_bitrate_options()

        label = QLabel("Made By cYy")
        label.setAlignment(Qt.AlignRight)
        download_layout.addWidget(label)

        tabs.addTab(download_tab, "下载设置")

        # 底部按钮
        btn_box = QHBoxLayout()
        btn_ok = QPushButton("确定")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addStretch()
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        parent_layout.addLayout(btn_box)

    # ---------- 标题栏拖拽 ----------
    def _title_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPos()
            self.dragging = True
            event.accept()

    def _title_mouse_move(self, event):
        if self.dragging:
            self.move(self.pos() + event.globalPos() - self.drag_pos)
            self.drag_pos = event.globalPos()
            event.accept()

    def _title_mouse_release(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            event.accept()

    # ---------- 其他方法 ----------
    def browse_path(self):
        path = QFileDialog.getExistingDirectory(self, "选择保存目录", self.path_edit.text())
        if path:
            self.path_edit.setText(path)

    def on_format_changed(self, index):
        if self.format_combo.currentText() == "自定义":
            self.format_custom_edit.show()
        else:
            self.format_custom_edit.hide()

    def _update_bitrate_options(self):
        """根据当前选择的转换格式，动态更新比特率下拉列表"""
        fmt = self.convert_combo.currentText()
        checked = self.convert_check.isChecked()

        # FLAC 无损格式，禁用比特率选项
        if fmt == "flac":
            self.bitrate_combo.setEnabled(False)
            self.bitrate_combo.clear()
            self.bitrate_combo.addItem("无损（无需比特率）")
            return

        # 其他格式根据 enable 状态
        self.bitrate_combo.setEnabled(checked)

        # 根据格式提供合适的比特率选项
        if fmt == "mp3":
            options = ["128k", "192k", "256k", "320k"]
        elif fmt == "aac":
            options = ["128k", "192k", "256k"]          # AAC 常用范围
        elif fmt == "ogg":
            options = ["128k", "192k", "256k", "320k"]  # 或可改为质量等级，这里保留比特率
        else:
            options = ["128k", "192k", "256k", "320k"]

        current = self.bitrate_combo.currentText()
        self.bitrate_combo.clear()
        self.bitrate_combo.addItems(options)
        if current in options:
            self.bitrate_combo.setCurrentText(current)
        else:
            self.bitrate_combo.setCurrentIndex(0)

    def get_settings(self):
        selected_sources = [cb.text() for cb in self.source_checkboxes if cb.isChecked()]
        theme_idx = self.theme_combo.currentIndex()
        theme_key = ['light', 'dark'][theme_idx] if theme_idx < 2 else 'light'

        # 获取比特率，若格式为 flac 则置空
        convert_bitrate = self.bitrate_combo.currentText()
        if self.convert_combo.currentText() == "flac":
            convert_bitrate = ""

        return {
            'sources': selected_sources,
            'limit': self.spin_limit.value(),
            'dedup': self.check_dedup.isChecked(),
            'save_dir': self.path_edit.text().strip(),
            'filename_format': self.format_combo.currentText(),
            'custom_format': self.format_custom_edit.text().strip(),
            'download_lyric': self.check_lyric.isChecked(),
            'download_cover': self.check_cover.isChecked(),
            'convert_enabled': self.convert_check.isChecked(),
            'convert_format': self.convert_combo.currentText() if self.convert_check.isChecked() else '',
            'convert_bitrate': convert_bitrate,
            'theme': theme_key,
            'background_opacity': self.opacity_slider.value() / 100.0,
            'embed_lyrics': self.check_embed_lyrics.isChecked(),
            'embed_cover': self.check_embed_cover.isChecked(),
        }
