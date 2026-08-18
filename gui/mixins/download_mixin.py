# -*- coding: utf-8 -*-
import os
import time
from typing import Dict
from PyQt5.QtWidgets import QMessageBox, QProgressDialog
from utils import logger
from threads import DownloadThread
from .base_mixin import BaseMixin

class DownloadMixin(BaseMixin):
    """下载逻辑"""

    def download_selected(self):
        if self.is_downloading:
            QMessageBox.information(self, '提示', '正在下载中，请稍候...')
            return

        selected_rows = set(self.get_selected_rows())
        if not selected_rows:
            QMessageBox.warning(self, '警告', '请先选择至少一首歌曲')
            return

        infos_to_refresh = []
        for row in sorted(selected_rows):
            info = self.music_records.get(str(row))
            if info:
                infos_to_refresh.append(info)
            else:
                QMessageBox.warning(self, '警告', f'第 {row+1} 首歌曲信息缺失，已跳过')

        if not infos_to_refresh:
            return

        self._set_ui_enabled(False)
        self.result_list.setEnabled(False)
        self.action_download.setEnabled(False)
        self.label_stats.setText('⏳ 正在刷新链接...')

        def on_refresh_done(refreshed_list, user_data):
            if not refreshed_list:
                self._set_ui_enabled(True)
                self.result_list.setEnabled(True)
                self.action_download.setEnabled(True)
                QMessageBox.warning(self, '警告', '所有歌曲链接均已失效，请重新搜索。')
                return
            self._start_download_for_list(refreshed_list)

        self.refresh_song_info_async(infos_to_refresh, on_refresh_done)

    def _start_download_for_list(self, song_list):
        if not song_list:
            self._set_ui_enabled(True)
            self.result_list.setEnabled(True)
            self.action_download.setEnabled(True)
            self.btn_cancel_download.setEnabled(False)
            return

        save_dir = self.settings['save_dir']
        if not save_dir:
            self._set_ui_enabled(True)
            self.result_list.setEnabled(True)
            self.action_download.setEnabled(True)
            QMessageBox.warning(self, '警告', '请选择有效的保存路径')
            return
        if not os.path.exists(save_dir):
            try:
                os.makedirs(save_dir)
            except Exception as e:
                self._set_ui_enabled(True)
                self.result_list.setEnabled(True)
                self.action_download.setEnabled(True)
                QMessageBox.critical(self, '错误', f'无法创建目录：{str(e)}')
                return

        self._download_cancelled = False
        self._all_done_emitted = False
        self.download_queue = song_list.copy()
        self.active_downloads.clear()
        self.download_completed = 0
        self.download_start_time = time.time()

        self._total_to_download = len(song_list)

        self.is_downloading = True
        self._set_ui_enabled(False)
        self.result_list.setEnabled(False)
        self.action_download.setEnabled(False)
        self.btn_cancel_download.setEnabled(True)

        self.bar_overall.setMaximum(self._total_to_download)
        self.bar_overall.setValue(0)
        self.bar_download.setValue(0)
        self.label_stats.setText(f"准备下载 {len(song_list)} 首...")

        self._start_downloads()

    def _start_downloads(self):
        while len(self.active_downloads) < self.download_concurrency and self.download_queue:
            song_info = self.download_queue.pop(0)
            thread = self._create_download_thread(song_info)
            self.active_downloads.append(thread)
            thread.start()

    def _create_download_thread(self, song_info):
        session = self._ensure_requests_session()
        thread = DownloadThread(
            song_info,
            self._get_request_kwargs_for_source,
            self.settings['save_dir'],
            self._get_filename_template(),
            self.settings['download_lyric'],
            self.settings['download_cover'],
            convert_format=self.settings.get('convert_format', ''),
            convert_bitrate=self.settings.get('convert_bitrate', ''),
            embed_lyrics=self.settings.get('embed_lyrics', False),
            delete_lyrics=self.settings.get('delete_lyrics', False),
            embed_cover=self.settings.get('embed_cover', False),
            delete_cover=self.settings.get('delete_cover', False),
            group_by=self.settings.get('group_by', '无分组'),
            session=session
        )
        thread.progress.connect(self._on_single_progress)
        thread.finished.connect(self._on_single_download_finished)
        thread.error.connect(self._on_single_download_error)
        return thread

    def _on_single_progress(self, percent):
        self.bar_download.setValue(percent)

    def _update_eta(self):
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
        if not self.btn_cancel_download.isEnabled():
            return
        self.btn_cancel_download.setEnabled(False)

        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.stop()
            try:
                self.download_thread.progress.disconnect()
                self.download_thread.finished.disconnect()
                self.download_thread.error.disconnect()
            except TypeError:
                pass

        for t in list(self.active_downloads):
            try:
                if hasattr(t, 'stop'):
                    t.stop()
            except Exception:
                pass
            try:
                if t.isRunning():
                    t.wait(1000)
            except Exception:
                pass
            try:
                t.progress.disconnect()
            except Exception:
                pass
            try:
                t.finished.disconnect()
            except Exception:
                pass
            try:
                t.error.disconnect()
            except Exception:
                pass

        # 只停止仍在下载中的任务：线程自身会清理其临时文件，
        # 已经下载完成的文件一律保留，不做删除。
        self._download_cancelled = True
        self.download_queue.clear()
        self._on_all_downloads_finished(cancelled=True)

    def _on_single_download_finished(self, song_name, singers, file_path):
        self.download_completed += 1
        self.bar_overall.setValue(self.download_completed)
        thread = self.sender()
        if thread in self.active_downloads:
            self.active_downloads.remove(thread)
        self._update_eta()
        self._start_downloads()
        if self.download_completed >= getattr(self, '_total_to_download', 0):
            self._on_all_downloads_finished()

    def _on_single_download_error(self, error_msg):
        if self._download_cancelled:
            return
        QMessageBox.critical(self, '下载错误', f'下载失败：{error_msg}')
        self.download_completed += 1
        self.bar_overall.setValue(self.download_completed)
        thread = self.sender()
        if thread in self.active_downloads:
            self.active_downloads.remove(thread)
        self._start_downloads()
        if self.download_completed >= getattr(self, '_total_to_download', 0):
            self._on_all_downloads_finished()

    def _on_all_downloads_finished(self, cancelled=False):
        if self._all_done_emitted:
            return
        self._all_done_emitted = True

        self.is_downloading = False
        self._download_cancelled = False
        self._set_ui_enabled(True)
        self.result_list.setEnabled(True)
        self.action_download.setEnabled(True)
        self.btn_cancel_download.setEnabled(False)
        total = self.bar_overall.maximum()
        if not cancelled:
            self.bar_download.setValue(0)
            self.bar_overall.setValue(total)
            self.label_stats.setText(f'✅ 所有下载任务已完成 ({total} 首)')
            QMessageBox.information(self, '下载完成', f'全部 {total} 首歌曲下载完毕。')
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
        self._total_to_download = 0
