# -*- coding: utf-8 -*-
import os
import re
import glob
from typing import List, Tuple, Dict
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QPixmap, QColor
from PyQt5.QtWidgets import QMessageBox, QAbstractItemView
from threads import CoverRunnable
from constants import PlayerState, PlayerMediaStatus, PlayMode
from utils import logger, get_cover_url
from .base_mixin import BaseMixin

class PlaybackMixin(BaseMixin):
    """播放控制、歌词显示、封面加载"""

    def _ensure_player(self):
        if self.player is None:
            from player import PlayerWrapper
            self.player = PlayerWrapper()
            self.player.setVolume(self.slider_volume.value())
            self.player.positionChanged.connect(self.update_position)
            self.player.durationChanged.connect(self.update_duration)
            self.player.stateChanged.connect(self.update_play_button)
            self.player.mediaStatusChanged.connect(self.handle_media_status)
            self.player.positionChanged.connect(self.update_lyric_display)
            # 迷你频谱直接读取播放引擎的音频帧（共用同一解码）
            self.spectrum_widget.set_player(self.player)
        return self.player

    def toggle_playback(self):
        self._ensure_player()
        if not self.playlist and self.player.state() == PlayerState.StoppedState:
            QMessageBox.information(self, "提示", "播放列表为空，请先选择歌曲播放。")
            return

        state = self.player.state()
        if state == PlayerState.PlayingState:
            self.player.pause()
        elif state == PlayerState.PausedState:
            self.player.play(volume=self.slider_volume.value())
        else:
            # 已停止：从头重新播放当前歌曲（play_current 会刷新链接并完整设置歌名/封面/歌词）
            if not self.playlist:
                return
            self.play_current()

    def stop_playback(self):
        # 引擎 stop() 已复位进度并清空解码（无需再 setPosition(0)，否则会重启解码导致停止后仍出声）
        if self.player is not None:
            self.player.stop()
        self.slider_position.setValue(0)
        self.label_time.setText("00:00 / 00:00")
        self.now_playing_label.setText("未播放")
        self.clear_lyric_display()
        self.cover_label.setText("🎵")
        self.cover_label.setPixmap(QPixmap())
        self._cover_task_id += 1

    # ---------- 进度条跳转（拖动时仅预览，释放时才执行 seek，避免频繁重启解码） ----------
    def _on_seek_preview(self, pos):
        """拖动进度条：仅更新时间显示"""
        self._seeking = True
        if self.player is not None:
            total = self.player.duration()
            if total > 0:
                self.label_time.setText(f"{self._format_time(pos)} / {self._format_time(total)}")

    def _on_seek_apply(self):
        """释放进度条：执行实际跳转"""
        self._seeking = False
        if self.player is not None:
            self.player.setPosition(self.slider_position.value())

    def set_volume(self, vol):
        if self.player is not None:
            self.player.setVolume(vol)

    def update_position(self, pos):
        if self._seeking:
            return  # 拖动中不抢进度条位置
        self.slider_position.setValue(pos)
        if self.player is not None:
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
            if self.player is not None:
                self.player.reset()
        elif status == PlayerMediaStatus.EndOfMedia:
            self._on_playback_ended()

    def on_playmode_changed(self, index):
        self.play_mode = PlayMode(index)
        self.settings['play_mode'] = index
        from config import save_settings
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

        self.now_playing_label.setText("⏳ 加载中...")
        self.label_stats.setText("正在刷新播放链接...")
        self.btn_play.setEnabled(False)

        def on_refresh_done(refreshed_list, user_data):
            self.btn_play.setEnabled(True)
            if not refreshed_list:
                QMessageBox.warning(self, "播放失败", "无法获取有效的播放链接，请检查网络或重新搜索。")
                self.stop_playback()
                return
            new_info = refreshed_list[0]
            self.playlist[self.current_play_index] = new_info
            self._do_play(new_info)

        self.refresh_song_info_async([song_info], on_refresh_done)

    def _do_play(self, song_info):
        self._ensure_player()
        url = song_info.get('download_url')
        if not url:
            QMessageBox.warning(self, "无法播放", "该歌曲没有可用的播放链接。")
            self.stop_playback()
            return

        self.player.reset()
        source = song_info.get('source', '')
        req_kwargs = self._get_request_kwargs_for_source(source)
        headers = req_kwargs.get('headers') or {}
        duration_ms = self._parse_duration_ms(song_info.get('duration'))
        self.player.setMedia(url, headers=headers, duration_ms=duration_ms)

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

        volume = self.slider_volume.value()
        QTimer.singleShot(200, lambda: self.player.play(volume=volume))

        cover_url = get_cover_url(song_info)
        if cover_url:
            req_kwargs = self._get_request_kwargs_for_source(source)
            QTimer.singleShot(300, lambda: self._fetch_cover_async(cover_url, req_kwargs))
        else:
            self.cover_label.setText("🎵")
            self.cover_label.setPixmap(QPixmap())

    @staticmethod
    def _parse_duration_ms(duration_str) -> int:
        """将时长（"05:30"/"1:02:03"/秒数）解析为毫秒；无法解析返回 0"""
        if not duration_str:
            return 0
        if isinstance(duration_str, (int, float)):
            return int(float(duration_str) * 1000)
        try:
            parts = str(duration_str).strip().split(':')
            if len(parts) == 2:
                secs = int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            else:
                return 0
            return secs * 1000
        except Exception:
            return 0

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

    # ---------- 歌词 ----------
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

        if self.player is None or self.player.state() == PlayerState.StoppedState:
            return

        self.player.setPosition(time_ms)
        if self.player.state() == PlayerState.PausedState:
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

    # ---------- 封面 ----------
    def _fetch_cover_async(self, url: str, request_kwargs: Dict):
        self._cover_task_id += 1
        task_id = self._cover_task_id
        session = self._ensure_requests_session()
        runnable = CoverRunnable(url, request_kwargs, task_id, session=session)
        runnable.signals.finished.connect(self._on_cover_loaded)
        self.cover_pool.start(runnable)

    def _on_cover_loaded(self, payload):
        from PyQt5.QtGui import QPixmap
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
                scaled = pixmap.scaled(60, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.cover_label.setPixmap(scaled)
            else:
                self.cover_label.setText("🎵")
                self.cover_label.setPixmap(QPixmap())
        except Exception as e:
            logger.error(f"加载封面失败: {e}", exc_info=True)
            self.cover_label.setText("🎵")
            self.cover_label.setPixmap(QPixmap())

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
