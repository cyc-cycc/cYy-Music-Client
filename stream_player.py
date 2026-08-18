# -*- coding: utf-8 -*-
"""统一流式播放引擎（主窗口播放器与可视化窗口共用）。

ffmpeg 解码网络链接 → PCM 队列 → sounddevice 音频输出；
同时以 pcm_frame 暴露最新单声道音频帧，供迷你频谱 / 可视化频谱读取，
保证频谱与播放天然同步、无需额外解码，并支持 HTTP 请求头（Referer 等）。
"""
import sys
import threading
import subprocess
from queue import Queue, Empty, Full
import numpy as np
import sounddevice as sd
from PyQt5.QtCore import QObject, pyqtSignal, QTimer, QThread

from constants import PlayerState, PlayerMediaStatus

_SAMPLE_RATE = 44100
_CHANNELS = 2
_FRAME = 1024

class _StreamInitThread(QThread):
    """后台创建音频输出流（设备打开约 200ms，避免阻塞主线程）"""
    done = pyqtSignal(object)

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.callback = callback

    def run(self):
        try:
            s = sd.OutputStream(
                samplerate=_SAMPLE_RATE, channels=_CHANNELS,
                callback=self.callback, blocksize=_FRAME, latency='low'
            )
        except Exception:
            s = None
        self.done.emit(s)

class StreamPlayer(QObject):
    """流式播放引擎：ffmpeg 解码 → PCM 队列 → sounddevice 输出。

    信号：positionChanged(ms)、durationChanged(ms)、
    stateChanged(PlayerState)、mediaStatusChanged(PlayerMediaStatus)。
    属性：pcm_frame —— 最新单声道 1024 样本帧（迷你频谱/可视化读取，引用赋值原子）。
    """
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    stateChanged = pyqtSignal(object)
    mediaStatusChanged = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pcm_frame = np.zeros(_FRAME, dtype=np.float32)
        self.pcm_lock = threading.Lock()

        self._url = None
        self._headers = None
        self._duration_ms = 0
        self._playback_samples = 0
        self._seek_offset_ms = 0
        self._volume = 100
        self._paused = False
        self._playing = False
        self._media_loaded = False
        self._ended = False
        self._failed = False

        self._proc = None
        self._queue = None
        self._stream = None
        self._stream_init = None
        self._pending_play = False

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._on_tick)

    # ==================== 公开接口 ====================
    def set_media(self, url, headers=None, duration_ms=0):
        """加载媒体（同时启动解码）。headers 会传给 ffmpeg（如 Referer）。"""
        self.stop()
        self._url = url
        self._headers = headers or {}
        self._duration_ms = int(duration_ms or 0)
        self._media_loaded = bool(url)
        self._seek_offset_ms = 0
        self._playback_samples = 0
        self._failed = False
        self.durationChanged.emit(self._duration_ms)
        if self._media_loaded:
            self.mediaStatusChanged.emit(PlayerMediaStatus.LoadedMedia)
            self._start_engine(seek_ms=0)
            self._ensure_stream()

    def play(self):
        if not self._media_loaded:
            return
        self._ended = False
        self._failed = False
        self._paused = False
        self._playing = True
        if self._stream is None:
            self._pending_play = True
            self._ensure_stream()
            return
        if not self._stream.active:
            try:
                self._stream.start()
            except Exception:
                pass
        self._timer.start()
        self.stateChanged.emit(PlayerState.PlayingState)

    def pause(self):
        self._pending_play = False
        self._paused = True
        self._playing = False
        self._timer.stop()
        self.stateChanged.emit(PlayerState.PausedState)

    def stop(self):
        self._playing = False
        self._paused = False
        self._timer.stop()
        self._stop_engine()
        self._playback_samples = 0
        self._seek_offset_ms = 0
        self._ended = False
        self._failed = False
        with self.pcm_lock:
            self.pcm_frame.fill(0)
        self.positionChanged.emit(0)
        self.stateChanged.emit(PlayerState.StoppedState)

    def seek(self, pos_ms):
        """跳转到指定毫秒（重启解码并 seek）"""
        if not self._media_loaded:
            return
        was_playing = self._playing and not self._paused
        self._stop_engine()
        self._seek_offset_ms = int(max(0, pos_ms))
        self._playback_samples = int(self._seek_offset_ms / 1000.0 * _SAMPLE_RATE)
        self._ended = False
        self._failed = False
        self._start_engine(seek_ms=self._seek_offset_ms)
        if was_playing:
            if self._stream is not None and not self._stream.active:
                try:
                    self._stream.start()
                except Exception:
                    pass
            self._timer.start()
        self.positionChanged.emit(self.position())

    def reset(self):
        self.stop()
        self._media_loaded = False
        self._url = None
        self._duration_ms = 0
        self.durationChanged.emit(0)

    def position(self) -> int:
        return int(self._playback_samples / _SAMPLE_RATE * 1000)

    def duration(self) -> int:
        return self._duration_ms

    def set_volume(self, vol):
        self._volume = int(vol)

    def volume(self) -> int:
        return self._volume

    def state(self) -> PlayerState:
        if self._playing and not self._paused:
            return PlayerState.PlayingState
        if self._paused:
            return PlayerState.PausedState
        return PlayerState.StoppedState

    def media_status(self) -> PlayerMediaStatus:
        if not self._media_loaded:
            return PlayerMediaStatus.NoMedia
        if self._failed:
            return PlayerMediaStatus.InvalidMedia
        if self._ended:
            return PlayerMediaStatus.EndOfMedia
        if self._playing and not self._paused:
            return PlayerMediaStatus.BufferedMedia
        return PlayerMediaStatus.LoadedMedia

    # ==================== 内部：ffmpeg 引擎 ====================
    def _start_engine(self, seek_ms=0):
        if not self._url:
            return
        self._stop_engine()
        cmd = ['ffmpeg']
        if self._headers:
            hdr = ''.join(f'{k}: {v}\r\n' for k, v in self._headers.items() if v)
            if hdr:
                cmd += ['-headers', hdr]
        if seek_ms > 0:
            cmd += ['-ss', str(seek_ms / 1000.0)]
        cmd += ['-i', self._url,
                '-f', 's16le', '-acodec', 'pcm_s16le',
                '-ac', str(_CHANNELS), '-ar', str(_SAMPLE_RATE), '-']
        startupinfo = None
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=4096, startupinfo=startupinfo
            )
        except Exception:
            # 失败统一由 _on_tick 发出 InvalidMedia（避免与 play 后的信号重复弹窗）
            self._failed = True
            return
        q = Queue(maxsize=200)
        self._proc = proc
        self._queue = q
        threading.Thread(target=self._reader_loop, args=(proc, q), daemon=True).start()

    def _stop_engine(self):
        proc = self._proc
        self._proc = None
        self._queue = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _reader_loop(self, proc, q):
        """读取 PCM 入队；解码失败且无数据时标记 failed"""
        got_data = False
        try:
            while proc.poll() is None:
                data = proc.stdout.read(4096)
                if not data:
                    break
                got_data = True
                q.put(data)
        except Exception:
            pass
        rc = proc.poll()
        if rc not in (None, 0) and not got_data:
            self._failed = True
        try:
            q.put_nowait(None)  # 结束标志
        except Full:
            pass

    # ==================== 内部：音频输出 ====================
    def _ensure_stream(self):
        if self._stream is None and self._stream_init is None:
            self._stream_init = _StreamInitThread(self._audio_callback, self)
            self._stream_init.done.connect(self._on_stream_ready)
            self._stream_init.start()

    def _on_stream_ready(self, stream):
        self._stream_init = None
        self._stream = stream
        if stream is None:
            # 音频设备打开失败：统一由 _on_tick 发出 InvalidMedia
            self._failed = True
            self._pending_play = False
            return
        if self._pending_play:
            self._pending_play = False
            self.play()

    def _audio_callback(self, outdata, frames, time_info, status):
        q = self._queue
        # 暂停 / 未播放 / 无解码时输出静音（停止后绝不继续出声或推进进度）
        if self._paused or q is None or not self._playing:
            outdata.fill(0)
            return

        needed = frames * _CHANNELS * 2  # 双声道 s16le 字节数
        buf = bytearray()
        while len(buf) < needed:
            try:
                chunk = q.get(timeout=0.01)
            except Empty:
                break
            if chunk is None:  # 播放结束
                outdata.fill(0)
                self._ended = True
                return
            buf.extend(chunk)

        if len(buf) < needed:
            buf.extend(b'\x00' * (needed - len(buf)))

        pcm = np.frombuffer(buf[:needed], dtype=np.int16).astype(np.float32) / 32768.0
        pcm = pcm.reshape(-1, _CHANNELS) * (self._volume / 100.0)
        if len(pcm) > frames:
            pcm = pcm[:frames]
        outdata[:len(pcm)] = pcm
        if len(pcm) < frames:
            outdata[len(pcm):] = 0

        if not self._paused:
            self._playback_samples += frames

        # 迷你频谱 / 可视化共用：最新单声道帧（引用赋值原子）
        mono = (pcm[:, 0] + pcm[:, 1]) * 0.5
        with self.pcm_lock:
            self.pcm_frame = mono

    def _on_tick(self):
        if self._failed:
            self._timer.stop()
            self._playing = False
            self.mediaStatusChanged.emit(PlayerMediaStatus.InvalidMedia)
            self.stateChanged.emit(PlayerState.StoppedState)
            return
        if self._ended:
            self._timer.stop()
            self._playing = False
            self._paused = False  # 播完=已停止（而非暂停），state() 返回 StoppedState
            self.mediaStatusChanged.emit(PlayerMediaStatus.EndOfMedia)
            self.stateChanged.emit(PlayerState.StoppedState)
            return
        if self._playing and not self._paused:
            self.positionChanged.emit(self.position())
