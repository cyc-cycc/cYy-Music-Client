# -*- coding: utf-8 -*-
import os
import sys
import threading
import time
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Callable, Tuple

import requests
from PyQt5.QtCore import QThread, pyqtSignal, QRunnable, QObject, QThreadPool, pyqtSlot

from utils import logger, _download_image_data, download_cover_image, get_cover_url, sanitize_filepath
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
    source_started = pyqtSignal(int, str)          # task_id, source
    source_finished = pyqtSignal(int, str, list)  # task_id, source, results
    finished = pyqtSignal(int)                    # task_id
    error = pyqtSignal(int, str)                  # task_id, error

    def __init__(self, music_client, sources: List[str],
                 keyword: str, limit_per_source: int, task_id: int,
                 threadings_per_source: int = 5):
        super().__init__()
        self.music_client = music_client
        self.sources = sources
        self.keyword = keyword
        self.limit_per_source = limit_per_source
        self.task_id = task_id
        self.threadings_per_source = threadings_per_source
        self._stop_event = threading.Event()
        self._executor = None

    def stop(self):
        self._stop_event.set()
        if self._executor:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass

    def run(self):
        self._executor = ThreadPoolExecutor(max_workers=max(1, len(self.sources)))
        futures = {}
        try:
            for source in self.sources:
                if self._stop_event.is_set():
                    break
                self.source_started.emit(self.task_id, source)
                fut = self._executor.submit(
                    self._search_single_source, source, self.keyword,
                    self.limit_per_source, self.threadings_per_source
                )
                futures[fut] = source

            for fut in concurrent.futures.as_completed(futures):
                if self._stop_event.is_set():
                    break
                source = futures.get(fut)
                try:
                    results = fut.result()
                    if results is None:
                        results = []
                    self.source_finished.emit(self.task_id, source, results)
                except Exception as e:
                    self.error.emit(self.task_id, f"{source} 搜索失败: {str(e)}")
        except Exception as e:
            self.error.emit(self.task_id, str(e))
        finally:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass
            self.finished.emit(self.task_id)

    def _search_single_source(self, source: str, keyword: str, limit: int,
                              num_threadings: int) -> Optional[List]:
        if self._stop_event.is_set():
            return None
        try:
            client = self.music_client.music_clients[source]
            results = client.search(
                keyword=keyword,
                num_threadings=num_threadings
            )
            return results[:limit]
        except Exception as e:
            raise e

# ==================== 歌单解析线程 ====================
class PlaylistParseThread(QThread):
    parse_started = pyqtSignal(int)
    parse_finished = pyqtSignal(int, list, str)     # task_id, song_infos, source_display
    parse_error = pyqtSignal(int, str)

    def __init__(self, playlist_url: str, source_internal: str, source_display: str, task_id: int):
        super().__init__()
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
            from musicdl import musicdl
            init_cfg = {self.source_internal: {'search_size_per_source': 50}}
            client = musicdl.MusicClient(
                music_sources=[self.source_internal],
                init_music_clients_cfg=init_cfg
            )
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
                valid.append(info)
            if not valid:
                self.parse_error.emit(self.task_id, "所有歌曲均无有效下载链接")
                return
            self.parse_finished.emit(self.task_id, valid, self.source_display)
        except Exception as e:
            logger.error(f"歌单解析线程异常: {e}", exc_info=True)
            self.parse_error.emit(self.task_id, str(e))

# ==================== 下载线程 ====================
class DownloadThread(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str, str, str)   # song_name, singers, file_path
    error = pyqtSignal(str)

    def __init__(self, song_info: Dict, get_request_kwargs: Callable[[str], Dict],
                 save_dir: str, filename_format: str,
                 download_lyric: bool, download_cover: bool):
        super().__init__()
        self.song_info = song_info
        self.get_request_kwargs = get_request_kwargs
        self.save_dir = save_dir
        self.filename_format = filename_format
        self.download_lyric = download_lyric
        self.download_cover = download_cover
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            source = self.song_info.get('source', '')
            url = self.song_info['download_url']
            work_dir = self.save_dir
            song_name = self.song_info.get('song_name', '')
            singers = self.song_info.get('singers', '')
            ext = self.song_info.get('ext', 'mp3') or 'mp3'

            base_name = self._build_base_name(song_name, singers)
            base_name = sanitize_filepath(base_name)
            if not os.path.exists(work_dir):
                os.makedirs(work_dir, exist_ok=True)

            audio_file_path = os.path.join(work_dir, f"{base_name}.{ext}")
            audio_file_path = self._get_unique_path(audio_file_path)

            request_kwargs = self.get_request_kwargs(source)
            if 'cookies' not in request_kwargs and self.song_info.get('cookies'):
                request_kwargs['cookies'] = self.song_info['cookies']

            self._download_file(url, audio_file_path, request_kwargs)

            if self.download_lyric and not self._stop:
                lyric_text = self.song_info.get('lyric') or self.song_info.get('lyrics', '')
                if lyric_text:
                    lyric_path = os.path.join(work_dir, f"{base_name}.lrc")
                    lyric_path = self._get_unique_path(lyric_path)
                    try:
                        with open(lyric_path, 'w', encoding='utf-8-sig') as f:
                            f.write(lyric_text)
                    except Exception as e:
                        logger.error(f"歌词保存失败: {e}", exc_info=True)
                        self.error.emit(f"歌词保存失败: {str(e)}")

            if self.download_cover and not self._stop:
                cover_url = get_cover_url(self.song_info)
                if cover_url:
                    img_data, cover_ext = download_cover_image(cover_url, request_kwargs)
                    if img_data:
                        cover_path = os.path.join(work_dir, f"{base_name}_cover.{cover_ext}")
                        cover_path = self._get_unique_path(cover_path)
                        try:
                            with open(cover_path, 'wb') as f:
                                f.write(img_data)
                            self._embed_cover(audio_file_path, img_data, cover_ext)
                        except Exception as e:
                            logger.error(f"保存/嵌入封面失败: {e}", exc_info=True)
                            self.error.emit(f"保存/嵌入封面失败: {str(e)}")

            if not self._stop:
                self.finished.emit(song_name, singers, audio_file_path)

        except Exception as e:
            logger.error(f"下载失败: {e}", exc_info=True)
            self.error.emit(str(e))

    def _build_base_name(self, song_name: str, singers: str) -> str:
        fmt = self.filename_format
        if fmt == "歌曲名":
            return song_name
        elif fmt == "歌手-歌曲名":
            return f"{singers}-{song_name}"
        elif fmt == "歌曲名-歌手":
            return f"{song_name}-{singers}"
        else:
            template = fmt
            template = template.replace("{歌手}", singers)
            template = template.replace("{歌曲名}", song_name)
            template = template.replace("{专辑}", self.song_info.get('album', ''))
            template = template.replace("{时长}", self.song_info.get('duration', ''))
            return template

    def _get_unique_path(self, path: str) -> str:
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        counter = 1
        while True:
            new_path = f"{base}({counter}){ext}"
            if not os.path.exists(new_path):
                return new_path
            counter += 1

    def _download_file(self, url: str, file_path: str, request_kwargs: Dict):
        f = None
        session = None
        try:
            session = requests.Session()
            session.verify = request_kwargs.get('verify', True)
            headers = request_kwargs.get('headers') or {}
            session.headers.update(headers)
            cookies = request_kwargs.get('cookies') or {}
            if cookies:
                session.cookies.update(cookies)

            kw = {}
            kw.update({k: v for k, v in request_kwargs.items() if k in ('timeout', 'proxies', 'verify')})
            kw['stream'] = True
            timeout = kw.get('timeout', 30)

            with session.get(url, timeout=timeout, stream=True, **({} if 'proxies' not in kw else {'proxies': kw.get('proxies')})) as resp:
                if resp.status_code != 200:
                    raise Exception(f"HTTP {resp.status_code}")
                total_hdr = resp.headers.get('content-length')
                total = int(total_hdr) if total_hdr and total_hdr.isdigit() else None
                downloaded = 0
                f = open(file_path, 'wb')
                last_emit_time = 0.0
                chunk_size = 32 * 1024
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if self._stop:
                        break
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if total:
                        percent = int(downloaded / total * 100)
                    else:
                        percent = min(99, int(downloaded / 1024))
                    if (now - last_emit_time) > 0.25 or percent == 100:
                        try:
                            self.progress.emit(percent)
                        except Exception:
                            pass
                        last_emit_time = now
                if self._stop:
                    try:
                        f.close()
                    except Exception:
                        pass
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except Exception:
                            logger.error(f"清理临时文件失败: {file_path}")
                    raise Exception("下载已取消")
                self.progress.emit(100)
        except Exception as e:
            if f:
                try:
                    f.close()
                except Exception:
                    pass
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    logger.error(f"清理临时文件失败: {file_path}")
            raise e
        finally:
            if session:
                try:
                    session.close()
                except Exception:
                    pass
            if f and not f.closed:
                try:
                    f.close()
                except Exception:
                    pass

    def _embed_cover(self, audio_path: str, img_data: bytes, cover_ext: str):
        try:
            ext_lower = os.path.splitext(audio_path)[1].lower()
            if ext_lower == '.mp3':
                from mutagen.id3 import ID3, APIC
                try:
                    audio = ID3(audio_path)
                except Exception:
                    audio = ID3()
                audio.add(APIC(
                    encoding=3,
                    mime=f'image/{cover_ext}',
                    type=3,
                    desc='Cover',
                    data=img_data
                ))
                audio.save(audio_path)
            elif ext_lower in ['.m4a', '.m4b']:
                from mutagen.mp4 import MP4, MP4Cover
                audio = MP4(audio_path)
                if cover_ext.lower() == 'png':
                    cover_format = MP4Cover.FORMAT_PNG
                else:
                    cover_format = MP4Cover.FORMAT_JPEG
                audio['covr'] = [MP4Cover(img_data, imageformat=cover_format)]
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
            try:
                self.error.emit(f"嵌入封面失败: {str(e)}")
            except Exception:
                pass
