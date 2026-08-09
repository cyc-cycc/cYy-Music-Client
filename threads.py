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

from utils import logger, _download_image_data, download_cover_image, get_cover_url, sanitize_filepath, build_filename, convert_audio, embed_lyrics
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
    result_ready = pyqtSignal(int, str, dict)      # task_id, source, song_info
    source_done = pyqtSignal(int, str, int)        # task_id, source, count
    source_error = pyqtSignal(int, str)            # task_id, error_msg
    all_done = pyqtSignal(int)                     # task_id

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

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(sources)) as executor:
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
            results = client.search(keyword=self.keyword, num_threadings=1)
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
    parse_finished = pyqtSignal(int, list, str)     # task_id, song_infos, source_display
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
            try:
                del temp_client
            except Exception:
                pass

# ==================== 下载线程（增加格式转换） ====================
class DownloadThread(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str, str, str)   # song_name, singers, file_path
    error = pyqtSignal(str)

    def __init__(self, song_info: Dict, get_request_kwargs: Callable[[str], Dict],
                 save_dir: str, filename_format: str,
                 download_lyric: bool, download_cover: bool,
                 convert_format: str = '', convert_bitrate: str = '',
                 embed_lyrics: bool = False, embed_cover: bool = False):
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
        self.embed_cover = embed_cover
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

            base_name = self._build_base_name()
            base_name = sanitize_filepath(base_name)
            if not os.path.exists(work_dir):
                os.makedirs(work_dir, exist_ok=True)

            audio_file_path = os.path.join(work_dir, f"{base_name}.{ext}")
            audio_file_path = self._get_unique_path(audio_file_path)

            request_kwargs = self.get_request_kwargs(source)
            if 'cookies' not in request_kwargs and self.song_info.get('cookies'):
                request_kwargs['cookies'] = self.song_info['cookies']

            self._download_file(url, audio_file_path, request_kwargs)

            # ===== 格式转换（新增） =====
            if self.convert_format and not self._stop:
                converted = convert_audio(audio_file_path, self.convert_format, self.convert_bitrate)
                if converted:
                    # 删除原文件（如果需要）
                    # 注意：如果转换输出同名但不同扩展名，则保留两者；这里我们替换为转换后的文件
                    if converted != audio_file_path:
                        try:
                            os.remove(audio_file_path)
                        except Exception:
                            pass
                        audio_file_path = converted
                else:
                    self.error.emit("格式转换失败，保留原文件")

            if self.download_lyric and not self._stop:
                lyric_text = self.song_info.get('lyric') or self.song_info.get('lyrics', '')
                if lyric_text:
                    if self.embed_lyrics:
                        # 嵌入歌词，不保存独立 .lrc
                        success = embed_lyrics(audio_file_path, lyric_text)
                        if not success:
                            # 嵌入失败，降级保存 .lrc
                            lyric_path = os.path.join(work_dir, f"{base_name}.lrc")
                            lyric_path = self._get_unique_path(lyric_path)
                            try:
                                with open(lyric_path, 'w', encoding='utf-8-sig') as f:
                                    f.write(lyric_text)
                                logger.warning("歌词嵌入失败，已保存为独立 .lrc 文件")
                            except Exception as e:
                                logger.error(f"歌词保存失败: {e}", exc_info=True)
                                self.error.emit(f"歌词保存失败: {str(e)}")
                    else:
                        # 不嵌入，仅保存 .lrc
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
                        if self.embed_cover:
                            # 嵌入封面，不保存独立文件
                            self._embed_cover(audio_file_path, img_data, cover_ext)
                        else:
                            # 不嵌入，仅保存独立封面文件
                            cover_path = os.path.join(work_dir, f"{base_name}_cover.{cover_ext}")
                            cover_path = self._get_unique_path(cover_path)
                            try:
                                with open(cover_path, 'wb') as f:
                                    f.write(img_data)
                            except Exception as e:
                                logger.error(f"保存封面失败: {e}", exc_info=True)
                                self.error.emit(f"保存封面失败: {str(e)}")
            if not self._stop:
                self.finished.emit(song_name, singers, audio_file_path)

        except Exception as e:
            logger.error(f"下载失败: {e}", exc_info=True)
            self.error.emit(str(e))

    def _build_base_name(self) -> str:
        return build_filename(self.song_info, self.filename_format)

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
                        # 当 total 不可用时使用较保守的估算（避免把字节数直接当百分比）
                        percent = min(99, int(downloaded / (1024 * 50)))  # 每 50KB 视作 1%
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
# ==================== 链接刷新线程（异步） ====================
class RefreshThread(QThread):
    """异步刷新歌曲下载链接，返回刷新后的 song_info 列表"""
    refresh_finished = pyqtSignal(list, object)  # (refreshed_list, user_data)
    progress = pyqtSignal(int, int)              # (current, total) 新增

    def __init__(self, song_infos: List[Dict], refresh_func, user_data=None):
        """
        :param song_infos: 需要刷新的歌曲信息列表（每个字典至少包含 identifier, source, singers, song_name）
        :param refresh_func: 实际执行刷新的函数（即原有的 refresh_song_url）
        :param user_data: 透传数据，便于回调识别场景
        """
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
            # 发射进度信号
            self.progress.emit(idx + 1, total)
        self.refresh_finished.emit(refreshed, self.user_data)
