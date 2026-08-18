# -*- coding: utf-8 -*-
import os
import sys
import threading
import time
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Callable, Tuple
import tempfile

import requests
from PyQt5.QtCore import QThread, pyqtSignal, QRunnable, QObject, QThreadPool, pyqtSlot

from utils import logger, _download_image_data, download_cover_image, get_cover_url, sanitize_filepath, build_filename, convert_audio, embed_lyrics, atomic_write, retry
from constants import DEFAULT_SAVE_DIR

# ==================== 封面下载任务 ====================
class CoverRunnableSignals(QObject):
    finished = pyqtSignal(object)

class CoverRunnable(QRunnable):
    def __init__(self, url: str, request_kwargs: Dict, task_id: int, session: Optional[requests.Session] = None, max_size: int = 5 * 1024 * 1024):
        super().__init__()
        self.url = url
        self.request_kwargs = request_kwargs.copy() if request_kwargs else {}
        self.task_id = task_id
        self.signals = CoverRunnableSignals()
        self._session = session
        self._max_size = max_size

    @pyqtSlot()
    def run(self):
        data, ext = _download_image_data(self.url, self.request_kwargs, self._max_size, self._session)
        if data:
            self.signals.finished.emit((data, self.task_id))
        else:
            self.signals.finished.emit((b'', self.task_id))

# ==================== 搜索线程 ====================
class SearchThread(QThread):
    result_ready = pyqtSignal(int, str, dict)
    source_done = pyqtSignal(int, str, int)
    source_error = pyqtSignal(int, str)
    all_done = pyqtSignal(int)

    def __init__(self, music_client, keyword: str, task_id: int):
        super().__init__()
        self.music_client = music_client
        self.keyword = keyword
        self.task_id = task_id
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        sources = list(self.music_client.music_clients.keys())
        if not sources:
            self.all_done.emit(self.task_id)
            return

        with ThreadPoolExecutor(max_workers=len(sources)) as executor:
            futures = {}
            for src in sources:
                fut = executor.submit(self._search_source, src)
                futures[fut] = src

            for fut in concurrent.futures.as_completed(futures):
                src = futures[fut]
                try:
                    count = fut.result()
                    self.source_done.emit(self.task_id, src, count)
                except Exception as e:
                    self.source_error.emit(self.task_id, f"{src}: {str(e)}")

        self.all_done.emit(self.task_id)

    def _search_source(self, source):
        try:
            client = self.music_client.music_clients[source]
            results = client.search(keyword=self.keyword, num_threadings=3)
            if self._stop:
                return 0
            count = 0
            for info in results:
                if self._stop:
                    break
                if not isinstance(info, dict):
                    info = {k: getattr(info, k, '') for k in ['song_name', 'singers', 'album', 'ext',
                                                               'duration', 'duration_s', 'cover_url',
                                                               'lyric', 'download_url', 'identifier',
                                                               'source', 'file_size']}
                    info['source'] = source
                else:
                    info['source'] = info.get('source', source)
                self.result_ready.emit(self.task_id, source, info)
                count += 1
            return count
        except Exception as e:
            self.source_error.emit(self.task_id, f"{source}: {str(e)}")
            return 0

# ==================== 歌单解析线程 ====================
class PlaylistParseThread(QThread):
    parse_started = pyqtSignal(int)
    parse_finished = pyqtSignal(int, list, str)
    parse_error = pyqtSignal(int, str)

    def __init__(self, music_client, playlist_url: str, source_internal: str, source_display: str, task_id: int):
        super().__init__()
        self.music_client = music_client
        self.playlist_url = playlist_url
        self.source_internal = source_internal
        self.source_display = source_display
        self.task_id = task_id
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            self.parse_started.emit(self.task_id)
            client = self.music_client.music_clients.get(self.source_internal)
            temp_client = None
            if not client:
                from musicdl import musicdl
                temp_client = musicdl.MusicClient(music_sources=[self.source_internal])
                client = temp_client.music_clients[self.source_internal]

            if self._stop:
                return
            song_infos = client.parseplaylist(self.playlist_url)
            if self._stop:
                return
            if not song_infos:
                self.parse_error.emit(self.task_id, "歌单解析结果为空")
                return

            valid = []
            for info in song_infos:
                if self._stop:
                    return
                info['source'] = self.source_internal
                if not info.get('download_url'):
                    if info.get('url'):
                        info['download_url'] = info['url']
                    else:
                        logger.warning(f"歌曲 {info.get('song_name')} 缺少下载链接，跳过")
                        continue
                if 'identifier' not in info and 'song_id' in info:
                    info['identifier'] = info['song_id']
                valid.append(info)

            if not valid:
                self.parse_error.emit(self.task_id, "所有歌曲均无有效下载链接")
                return
            self.parse_finished.emit(self.task_id, valid, self.source_display)
        except Exception as e:
            logger.error(f"歌单解析线程异常: {e}", exc_info=True)
            self.parse_error.emit(self.task_id, str(e))
        finally:
            try:
                if temp_client and hasattr(temp_client, 'close'):
                    temp_client.close()
            except Exception:
                pass

# ==================== 下载线程（增强：临时文件+重试+原子写入） ====================
class DownloadThread(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str, str, str)
    error = pyqtSignal(str)

    def __init__(self, song_info: Dict, get_request_kwargs: Callable[[str], Dict],
                 save_dir: str, filename_format: str,
                 download_lyric: bool, download_cover: bool,
                 convert_format: str = '', convert_bitrate: str = '',
                 embed_lyrics: bool = False, delete_lyrics: bool = False,
                 embed_cover: bool = False, delete_cover: bool = False,
                 group_by: str = '无分组',
                 session: Optional[requests.Session] = None):
        super().__init__()
        self.song_info = song_info
        self.get_request_kwargs = get_request_kwargs
        self.save_dir = save_dir
        self.filename_format = filename_format
        self.download_lyric = download_lyric
        self.download_cover = download_cover
        self.convert_format = convert_format
        self.convert_bitrate = convert_bitrate
        self.embed_lyrics = embed_lyrics
        self.delete_lyrics = delete_lyrics
        self.embed_cover = embed_cover
        self.delete_cover = delete_cover
        self.group_by = group_by
        self._stop = False
        self._session = session

    def stop(self):
        self._stop = True

    @retry(max_attempts=3, delay=1, backoff=2, exceptions=(requests.RequestException, IOError))
    def _download_file(self, url: str, target_path: str, request_kwargs: Dict):
        """使用临时文件下载，成功后原子替换"""
        temp_dir = os.path.dirname(target_path) or '.'
        with tempfile.NamedTemporaryFile(dir=temp_dir, delete=False, suffix='.tmp') as tmp:
            tmp_path = tmp.name
        try:
            session = self._session or requests.Session()
            session.verify = request_kwargs.get('verify', True)
            headers = request_kwargs.get('headers') or {}
            session.headers.update(headers)
            cookies = request_kwargs.get('cookies') or {}
            if cookies:
                session.cookies.update(cookies)

            # 构造请求参数，避免重复传递 timeout
            req_kwargs = {}
            for k in ('proxies', 'verify'):
                if k in request_kwargs:
                    req_kwargs[k] = request_kwargs[k]
            req_kwargs['stream'] = True
            req_kwargs['timeout'] = request_kwargs.get('timeout', 30)

            with session.get(url, **req_kwargs) as resp:
                if resp.status_code != 200:
                    raise Exception(f"HTTP {resp.status_code}")
                total = int(resp.headers.get('content-length', 0)) or None
                downloaded = 0
                last_emit = 0.0
                with open(tmp_path, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=32*1024):
                        if self._stop:
                            break
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            if total:
                                percent = int(downloaded / total * 100)
                            else:
                                percent = min(99, int(downloaded / (1024 * 50)))  # 估算
                            if now - last_emit > 0.25 or percent == 100:
                                self.progress.emit(percent)
                                last_emit = now
                if self._stop:
                    os.unlink(tmp_path)
                    raise Exception("下载已取消")
                self.progress.emit(100)
                # 原子替换
                os.replace(tmp_path, target_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        finally:
            if self._session is None and 'session' in locals():
                try:
                    session.close()
                except Exception:
                    pass

    def run(self):
        try:
            source = self.song_info.get('source', '')
            url = self.song_info['download_url']

            from utils import get_group_subdir
            sub_dir = get_group_subdir(self.song_info, self.group_by)
            target_dir = os.path.join(self.save_dir, sub_dir) if sub_dir else self.save_dir
            os.makedirs(target_dir, exist_ok=True)

            song_name = self.song_info.get('song_name', '')
            singers = self.song_info.get('singers', '')
            ext = self.song_info.get('ext', 'mp3') or 'mp3'
            base_name = self._build_base_name()
            base_name = sanitize_filepath(base_name)

            audio_file_path = os.path.join(target_dir, f"{base_name}.{ext}")

            request_kwargs = self.get_request_kwargs(source)
            if 'cookies' not in request_kwargs and self.song_info.get('cookies'):
                request_kwargs['cookies'] = self.song_info['cookies']

            self._download_file(url, audio_file_path, request_kwargs)

            # 格式转换
            if self.convert_format and not self._stop:
                converted = convert_audio(audio_file_path, self.convert_format, self.convert_bitrate)
                if converted:
                    if converted != audio_file_path:
                        try:
                            os.remove(audio_file_path)
                        except Exception:
                            pass
                        audio_file_path = converted
                else:
                    self.error.emit("格式转换失败，保留原文件")

            # 歌词
            if self.download_lyric and not self._stop:
                lyric_text = self.song_info.get('lyric') or self.song_info.get('lyrics', '')
                if lyric_text:
                    lyric_path = os.path.join(target_dir, f"{base_name}.lrc")
                    try:
                        atomic_write(lyric_text.encode('utf-8-sig'), lyric_path)
                    except Exception as e:
                        logger.error(f"歌词保存失败: {e}", exc_info=True)
                        self.error.emit(f"歌词保存失败: {str(e)}")
                    else:
                        if self.embed_lyrics:
                            if embed_lyrics(audio_file_path, lyric_text) and self.delete_lyrics:
                                try:
                                    os.remove(lyric_path)
                                except Exception:
                                    pass

            # 封面
            if self.download_cover and not self._stop:
                cover_url = get_cover_url(self.song_info)
                if cover_url:
                    img_data, cover_ext = download_cover_image(cover_url, request_kwargs)
                    if img_data:
                        cover_path = os.path.join(target_dir, f"{base_name}_cover.{cover_ext}")
                        try:
                            atomic_write(img_data, cover_path)
                        except Exception as e:
                            logger.error(f"保存封面失败: {e}", exc_info=True)
                            self.error.emit(f"保存封面失败: {str(e)}")
                        else:
                            if self.embed_cover:
                                try:
                                    self._embed_cover(audio_file_path, img_data, cover_ext)
                                    if self.delete_cover and os.path.exists(cover_path):
                                        os.remove(cover_path)
                                except Exception as e:
                                    logger.error(f"嵌入封面失败: {e}", exc_info=True)

            if not self._stop:
                self.finished.emit(song_name, singers, audio_file_path)

        except Exception as e:
            logger.error(f"下载失败: {e}", exc_info=True)
            self.error.emit(str(e))

    def _build_base_name(self) -> str:
        return build_filename(self.song_info, self.filename_format)

    def _embed_cover(self, audio_path: str, img_data: bytes, cover_ext: str):
        try:
            ext_lower = os.path.splitext(audio_path)[1].lower()
            if ext_lower == '.mp3':
                from mutagen.id3 import ID3, APIC
                try:
                    audio = ID3(audio_path)
                except Exception:
                    audio = ID3()
                audio.add(APIC(encoding=3, mime=f'image/{cover_ext}', type=3, desc='Cover', data=img_data))
                audio.save(audio_path)
            elif ext_lower in ['.m4a', '.m4b']:
                from mutagen.mp4 import MP4, MP4Cover
                audio = MP4(audio_path)
                fmt = MP4Cover.FORMAT_PNG if cover_ext.lower() == 'png' else MP4Cover.FORMAT_JPEG
                audio['covr'] = [MP4Cover(img_data, imageformat=fmt)]
                audio.save()
            elif ext_lower == '.flac':
                from mutagen.flac import FLAC, Picture
                pic = Picture()
                pic.data = img_data
                pic.type = 3
                pic.mime = f'image/{cover_ext}'
                audio = FLAC(audio_path)
                audio.add_picture(pic)
                audio.save()
        except Exception as e:
            logger.error(f"嵌入封面失败: {e}", exc_info=True)
            self.error.emit(f"嵌入封面失败: {str(e)}")

# ==================== 链接刷新线程 ====================
class RefreshThread(QThread):
    refresh_finished = pyqtSignal(list, object)
    progress = pyqtSignal(int, int)

    def __init__(self, song_infos: List[Dict], refresh_func, user_data=None):
        super().__init__()
        self.song_infos = song_infos
        self.refresh_func = refresh_func
        self.user_data = user_data
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        total = len(self.song_infos)
        refreshed = []
        for idx, info in enumerate(self.song_infos):
            if self._stop:
                break
            new_info = self.refresh_func(info)
            if new_info:
                refreshed.append(new_info)
            self.progress.emit(idx + 1, total)
        self.refresh_finished.emit(refreshed, self.user_data)
