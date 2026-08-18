# -*- coding: utf-8 -*-
from PyQt5.QtCore import QPoint, QRect
from PyQt5.QtWidgets import QMessageBox
from constants import PlayerState, PlayerMediaStatus, PlayMode
from utils import logger

class BaseMixin:
    """提供所有 Mixin 共用的属性和基础方法"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 这些属性会在主窗口 __init__ 中初始化，这里仅声明类型提示
        self.settings = {}
        self.playlist = []
        self.current_play_index = -1
        self.play_mode = PlayMode(2)
        self.player = None
        self.music_client = None
        self.refresh_client = None
        self._requests_session = None
        self._url_cache = None
        self._refresh_lock = None
        self._cover_task_id = 0
        self.cover_pool = None
        self.search_thread = None
        self.download_thread = None
        self.parse_thread = None
        self.refresh_thread = None
        self.music_records = {}
        self.current_lyrics = []
        self.current_lyric_index = -1
        self.is_downloading = False
        self.is_parsing = False
        self.search_in_progress = False
        self._all_done_emitted = False
        self._download_cancelled = False
        self.active_downloads = []
        self.download_queue = []
        self.download_completed = 0
        self.download_start_time = None
        self._total_to_download = 0
        self.search_task_counter = 0
        self.current_search_task_id = 0
        self.parse_task_counter = 0
        self.current_parse_task_id = 0
        self.drag_pos = QPoint()
        self.dragging = False
        self._seeking = False
        self._resizing = False
        self._resize_start_pos = QPoint()
        self._resize_start_geo = QRect()
        # UI 控件（由主窗口创建）
        self.title_bar = None
        self.search_input = None
        self.btn_search_title = None
        self.btn_settings = None
        self.btn_about = None
        self.btn_minimize = None
        self.btn_maximize = None
        self.btn_close = None
        self.lineedit_playlist = None
        self.combo_playlist_source = None
        self.button_parse_playlist = None
        self.btn_clear_results = None
        self.btn_cancel_download = None
        self.result_list = None
        self.lyric_display = None
        self.playlist_widget = None
        self.playlist_title = None
        self.btn_add_playlist = None
        self.btn_save_playlist = None
        self.btn_load_playlist = None
        self.cover_label = None
        self.now_playing_label = None
        self.slider_position = None
        self.spectrum_widget = None
        self.label_time = None
        self.btn_prev = None
        self.btn_play = None
        self.btn_next = None
        self.btn_stop = None
        self.btn_visualize = None
        self.slider_volume = None
        self.combo_playmode = None
        self.label_stats = None
        self.bar_download = None
        self.bar_overall = None
        self.context_menu = None
        self.action_download = None
        self.action_add_to_playlist = None
        self.parse_progress = None
        self.vis_window = None
        # 需要导入 mixin 中使用的方法，此处声明，实际由主窗口提供
        # 例如 _apply_volume_from_settings, _set_ui_enabled, _show_warning 等

    def _format_time(self, ms):
        s = ms // 1000
        m, s = divmod(s, 60)
        return f"{m:02d}:{s:02d}"

    def _internal_to_display(self, internal: str) -> str:
        from constants import SOURCE_INTERNAL
        for k, v in SOURCE_INTERNAL.items():
            if v == internal:
                return k
        return internal

    def _show_warning(self, title: str, text: str):
        """显示警告对话框（通用方法）"""
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.NoIcon)
        msg.setWindowTitle(title)
        msg.setText(text)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec_()
