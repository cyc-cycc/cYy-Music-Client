# -*- coding: utf-8 -*-
import os
import glob
from typing import Optional
from PyQt5.QtWidgets import QMessageBox
from utils import get_cover_url
from .base_mixin import BaseMixin
from constants import PlayerState

class VisualizationMixin(BaseMixin):
    """可视化窗口相关"""

    def show_visualization(self):
        from visualizer import AudioVisualizer
        if self.player is None or self.player.state() == PlayerState.StoppedState:
            self._open_blank_visualization()
            return

        if self.current_play_index < 0 or not self.playlist:
            self._open_blank_visualization()
            return

        song_info = self.playlist[self.current_play_index]
        audio_file = self._find_downloaded_audio(song_info)

        if audio_file and os.path.exists(audio_file):
            # 已有本地文件：离线模式（频谱效果最佳）
            self._open_visualization(audio_file, song_info)
        else:
            # 无本地文件：刷新链接后直接流式可视化，无需先下载
            self._open_stream_visualization(song_info)

    def _find_downloaded_audio(self, song_info) -> Optional[str]:
        """在保存目录（含分组子目录）中查找已下载的音频文件。

        兼容：
        - 按歌手/专辑分组产生的子目录
        - 用户自定义的文件名格式（与下载时一致）
        - 下载后转换格式（扩展名变化）
        """
        from utils import build_filename, sanitize_filepath

        save_dir = self.settings.get('save_dir', '')
        if not save_dir or not os.path.isdir(save_dir):
            return None

        # 候选文件名（与下载命名逻辑一致，按优先级排序）
        candidates = []
        formats = [self._get_filename_template(), '{歌手}-{歌曲名}', '歌手-歌曲名', '歌曲名-歌手', '歌曲名']
        for fmt in formats:
            try:
                cand = sanitize_filepath(build_filename(song_info, fmt))
            except Exception:
                cand = ''
            if cand and cand not in candidates:
                candidates.append(cand)

        # 候选扩展名（原始格式 + 转换格式）
        exts = {song_info.get('ext', 'mp3') or 'mp3'}
        convert_fmt = (self.settings.get('convert_format', '') or '').lower()
        if convert_fmt:
            exts.add(convert_fmt)

        def _score(path):
            stem = os.path.splitext(os.path.basename(path))[0]
            best = -1
            for idx, cand in enumerate(candidates):
                if not cand:
                    continue
                if stem == cand:
                    return 1000 - idx
                if stem.startswith(cand):
                    best = max(best, 500 - idx)
                elif cand in stem:
                    best = max(best, 100 - idx)
            return best

        best_path, best_score = None, -1
        for ext in exts:
            for path in glob.glob(os.path.join(save_dir, '**', f'*.{ext}'), recursive=True):
                base_name = os.path.basename(path)
                if '_cover' in base_name or base_name.endswith('.lrc'):
                    continue
                score = _score(path)
                if score > best_score:
                    best_score, best_path = score, path
        return best_path

    def _open_blank_visualization(self):
        from visualizer import AudioVisualizer
        if hasattr(self, 'vis_window') and self.vis_window is not None:
            try:
                self.vis_window.close()
            except RuntimeError:
                pass
            self.vis_window = None

        self.vis_window = AudioVisualizer(
            parent=self,
            initial_volume=self.slider_volume.value(),
            theme_name=self.settings.get('theme', 'light')
        )
        self.vis_window.destroyed.connect(self._on_vis_window_destroyed)
        self.vis_window.show()

    def _open_visualization(self, audio_file, song_info):
        from visualizer import AudioVisualizer
        if hasattr(self, 'vis_window') and self.vis_window is not None:
            try:
                self.vis_window.close()
            except RuntimeError:
                pass
            self.vis_window = None

        if self.player is not None and self.player.state() == PlayerState.PlayingState:
            self.player.pause()

        base, _ = os.path.splitext(audio_file)
        lyric_file = base + '.lrc'
        cover_file = None
        cover_pattern = base + '_cover.*'
        covers = glob.glob(cover_pattern)
        if covers:
            cover_file = covers[0]

        self.vis_window = AudioVisualizer(
            audio_file,
            lyric_file if os.path.exists(lyric_file) else None,
            cover_file if cover_file and os.path.exists(cover_file) else None,
            parent=self,
            initial_volume=self.slider_volume.value(),
            theme_name=self.settings.get('theme', 'light')
        )
        self.vis_window.destroyed.connect(self._on_vis_window_destroyed)
        self.vis_window.show()

    def _on_vis_window_destroyed(self):
        self.vis_window = None

    def _open_stream_visualization(self, song_info):
        """无本地文件时：刷新链接后直接流式可视化（无需下载）"""
        self.label_stats.setText("⏳ 刷新链接中...")
        self.btn_visualize.setEnabled(False)

        def on_refresh_done(refreshed_list, user_data):
            self.btn_visualize.setEnabled(True)
            if not refreshed_list:
                QMessageBox.warning(self, "链接失效", "无法获取有效链接，请稍后重试。")
                self.label_stats.setText("❌ 链接刷新失败")
                return
            self._do_open_stream_visualization(refreshed_list[0])

        self.refresh_song_info_async([song_info], on_refresh_done)

    def _do_open_stream_visualization(self, song_info):
        from visualizer import AudioVisualizer

        url = song_info.get('download_url')
        if not url:
            QMessageBox.warning(self, "无法播放", "该歌曲没有可用的播放链接。")
            return

        # 与本地模式一致：打开可视化时暂停主播放器，避免双音源
        if self.player is not None and self.player.state() == PlayerState.PlayingState:
            self.player.pause()

        if hasattr(self, 'vis_window') and self.vis_window is not None:
            try:
                self.vis_window.close()
            except RuntimeError:
                pass
            self.vis_window = None

        singer = song_info.get('singers', '')
        name = song_info.get('song_name', '')
        song_title = f"{singer} - {name}" if singer and name else (name or singer or "未知歌曲")

        self.vis_window = AudioVisualizer(
            parent=self,
            initial_volume=self.slider_volume.value(),
            theme_name=self.settings.get('theme', 'light'),
            stream_url=url,
            duration_str=song_info.get('duration'),
            lyric_text=song_info.get('lyric') or song_info.get('lyrics', ''),
            cover_url=get_cover_url(song_info),
            song_title=song_title,
        )
        self.vis_window.destroyed.connect(self._on_vis_window_destroyed)
        self.vis_window.show()
        self.label_stats.setText(f"🎨 正在流式可视化: {song_title}")
