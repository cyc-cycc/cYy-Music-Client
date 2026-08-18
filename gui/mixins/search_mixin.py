# -*- coding: utf-8 -*-
import time
from PyQt5.QtCore import QTimer, QSize
from PyQt5.QtWidgets import QListWidgetItem, QMessageBox
from threads import SearchThread
from widgets import SongCard
from utils import safe_stop_thread, logger
from .base_mixin import BaseMixin

class SearchMixin(BaseMixin):
    """搜索相关逻辑"""

    def start_search(self):
        self._ensure_music_client()
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
        self.search_thread.result_ready.connect(self._on_result_ready)
        self.search_thread.source_done.connect(self._on_source_done)
        self.search_thread.source_error.connect(self.on_search_error)
        self.search_thread.all_done.connect(self._on_search_all_done)
        self.search_thread.start()

    def stop_search(self):
        if self.search_in_progress:
            old_thread = self.search_thread
            if old_thread is None:
                self.search_in_progress = False
                self._set_ui_enabled(True)
                self.btn_search_title.setEnabled(True)
                self.btn_search_title.setText('🔍')
                self.btn_search_title.setToolTip('搜索')
                self.label_stats.setText('⏹ 已停止搜索')
                return

            self.search_thread = None
            safe_stop_thread(
                old_thread,
                ['result_ready', 'source_done', 'source_error', 'all_done'],
                lambda: self._cleanup_search_thread(old_thread)
            )
            self.search_in_progress = False
            self._set_ui_enabled(True)
            self.btn_search_title.setEnabled(True)
            self.btn_search_title.setText('🔍')
            self.btn_search_title.setToolTip('搜索')
            self.label_stats.setText('⏹ 已停止搜索')
        else:
            self.finish_search()

    def finish_search(self):
        self.search_in_progress = False
        self.btn_search_title.setEnabled(True)
        self.btn_search_title.setText('🔍')
        self.btn_search_title.setToolTip('搜索')
        self._set_ui_enabled(True)
        if self.result_list.count() == 0:
            if not self.label_stats.text().startswith('❌'):
                self.label_stats.setText('❌ 未找到任何结果')

    def _cleanup_search_thread(self, thread):
        if thread is None:
            return
        for sig_name in ['result_ready', 'source_done', 'source_error', 'all_done']:
            sig = getattr(thread, sig_name, None)
            if sig:
                try:
                    sig.disconnect()
                except TypeError:
                    pass
        if thread.isRunning():
            thread.wait()
        thread.deleteLater()

    def _on_result_ready(self, task_id, source, song_info):
        if task_id != self.current_search_task_id:
            return
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
        if self.search_thread and self.sender() == self.search_thread:
            self.search_thread.deleteLater()
            self.search_thread = None

    def on_search_error(self, task_id: int, error_msg: str):
        if task_id != self.current_search_task_id:
            return
        QMessageBox.warning(self, '搜索警告', error_msg)

    def add_song_card(self, song_info, source_display=None):
        from constants import SOURCE_INTERNAL
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

    def on_search_or_stop(self):
        if self.is_parsing:
            self._show_warning('提示', '正在解析歌单，请稍后再试')
            return
        if not self.search_in_progress:
            self.start_search()
        else:
            self.stop_search()
