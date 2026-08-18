# -*- coding: utf-8 -*-
import threading
from typing import Dict, Optional, List
from cachetools import TTLCache
from utils import logger
from threads import RefreshThread
from .base_mixin import BaseMixin
from constants import REFRESH_SEARCH_SIZE

class RefreshMixin(BaseMixin):
    """链接刷新相关逻辑"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._url_cache = TTLCache(maxsize=500, ttl=300)
        self._refresh_lock = threading.Lock()
        self.refresh_thread = None

    def refresh_song_url(self, song_info: Dict) -> Optional[Dict]:
        identifier = song_info.get('identifier') or song_info.get('song_id')
        if not identifier:
            logger.warning("缺少 identifier，无法刷新")
            return None

        with self._refresh_lock:
            cached_url = self._url_cache.get(identifier)
            if cached_url is not None:
                if cached_url != song_info.get('download_url'):
                    song_info['download_url'] = cached_url
                    logger.debug(f"使用缓存的链接: {identifier}")
                return song_info

        url = song_info.get('download_url', '')
        if url and 'expires' not in url and 'sign' not in url:
            try:
                import requests
                head_resp = requests.head(url, timeout=5, allow_redirects=True)
                if head_resp.status_code < 400:
                    self._url_cache[identifier] = url
                    return song_info
            except Exception:
                pass

        source = song_info.get('source')
        if not source:
            logger.warning(f"缺少 source，无法刷新: {identifier}")
            return None

        # 确保 refresh_client 已初始化
        if self.refresh_client is None:
            self._ensure_refresh_client()
        client = self.refresh_client.music_clients.get(source) if self.refresh_client else None
        if not client:
            logger.warning(f"刷新客户端中无源 {source}，跳过刷新")
            return None

        keyword = f"{song_info.get('singers', '')} {song_info.get('song_name', '')}".strip()
        if not keyword:
            logger.warning(f"无关键词，无法刷新: {identifier}")
            return None

        try:
            results = client.search(keyword, num_threadings=1)
        except Exception as e:
            logger.error(f"刷新搜索失败: {e}")
            results = None

        if not results:
            logger.warning(f"刷新搜索没有返回结果: {identifier}")
            return None

        matched = None
        for item in results[:REFRESH_SEARCH_SIZE]:
            if item.get('identifier') == identifier:
                matched = item
                break
        if not matched and results:
            matched = results[0]

        if not matched:
            logger.warning(f"未找到匹配歌曲: {identifier}")
            return None

        new_url = matched.get('download_url') or matched.get('url')
        if not new_url:
            return None

        song_info['download_url'] = new_url
        if matched.get('cover_url'):
            song_info['cover_url'] = matched['cover_url']
        if matched.get('duration'):
            song_info['duration'] = matched['duration']
        if matched.get('lyric'):
            song_info['lyric'] = matched['lyric']
        if matched.get('ext'):
            song_info['ext'] = matched['ext']

        self._url_cache[identifier] = new_url
        logger.info(f"链接刷新成功: {identifier}")
        return song_info

    def refresh_song_info_async(self, song_infos, callback, user_data=None):
        if not song_infos:
            callback([], user_data)
            return

        if self.refresh_thread and self.refresh_thread.isRunning():
            old_thread = self.refresh_thread
            self.refresh_thread = None
            try:
                old_thread.refresh_finished.disconnect(self._on_refresh_done)
                old_thread.progress.disconnect(self._on_refresh_progress)
                old_thread.finished.disconnect()
            except TypeError:
                pass
            old_thread.stop()
            old_thread.finished.connect(lambda: self._cleanup_refresh_thread(old_thread))

        self.refresh_thread = RefreshThread(
            song_infos,
            self.refresh_song_url,
            user_data
        )
        self.refresh_thread.progress.connect(self._on_refresh_progress)
        self.refresh_thread.refresh_finished.connect(
            lambda refreshed, ud: self._on_refresh_done(refreshed, ud, callback)
        )
        self.refresh_thread.start()

    def _on_refresh_progress(self, current, total):
        self.label_stats.setText(f"⏳ 刷新链接中... {current}/{total}")

    def _on_refresh_done(self, refreshed_list, user_data, callback):
        sender = self.sender()
        if sender != self.refresh_thread:
            return
        callback(refreshed_list, user_data)
        if self.refresh_thread:
            self.refresh_thread.deleteLater()
            self.refresh_thread = None

    def _cleanup_refresh_thread(self, thread):
        if thread is None:
            return
        for sig_name in ['progress', 'refresh_finished']:
            sig = getattr(thread, sig_name, None)
            if sig:
                try:
                    sig.disconnect()
                except TypeError:
                    pass
        if thread.isRunning():
            thread.wait()
        thread.deleteLater()
