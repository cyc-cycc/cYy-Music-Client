# -*- coding: utf-8 -*-
import os
import json
import base64
import hashlib
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QListWidgetItem, QMenu, QFileDialog, QMessageBox, QApplication
from cryptography.fernet import Fernet
from constants import ENCRYPTION_PASSWORD
from utils import logger, safe_stop_thread
from .base_mixin import BaseMixin

class PlaylistMixin(BaseMixin):
    """播放列表管理（增删改、保存/加载）"""

    def add_to_playlist(self, song_info, play=False):
        if not song_info:
            return
        if 'identifier' not in song_info and 'song_id' in song_info:
            song_info['identifier'] = song_info['song_id']
        self.playlist.append(song_info)
        if play:
            self.current_play_index = len(self.playlist) - 1
            self.play_current()
        self.update_playlist_widget()

    def add_selected_to_playlist(self):
        rows = self.get_selected_rows()
        if not rows:
            QMessageBox.information(self, "提示", "请先在搜索结果中选择歌曲")
            return
        for row in rows:
            info = self.music_records.get(str(row))
            if info:
                self.add_to_playlist(info, play=False)
        self.update_playlist_widget()
        self.label_stats.setText(f"已添加 {len(rows)} 首歌曲到歌单")

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
        new_playlist = []
        for i in range(self.playlist_widget.count()):
            item = self.playlist_widget.item(i)
            idx = item.data(Qt.UserRole)
            if idx is not None and 0 <= idx < len(self.playlist):
                new_playlist.append(self.playlist[idx])
        self.playlist = new_playlist
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
        self.update_playlist_widget()

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

        self.playlist = data
        self.current_play_index = -1
        self.update_playlist_widget()
        self.label_stats.setText(f"已加载歌单，共 {len(self.playlist)} 首歌曲")

    def _sanitize_song_info(self, song_info):
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

    def get_selected_rows(self):
        rows = []
        for item in self.result_list.selectedItems():
            rows.append(self.result_list.row(item))
        return rows

    def get_song_info_by_row(self, row):
        return self.music_records.get(str(row))
