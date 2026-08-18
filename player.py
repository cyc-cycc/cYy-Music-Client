# -*- coding: utf-8 -*-
from PyQt5.QtCore import QObject, pyqtSignal

from constants import PlayerState, PlayerMediaStatus
from stream_player import StreamPlayer

class PlayerWrapper(QObject):
    """主窗口播放器：与可视化窗口共用同一套流式播放引擎（ffmpeg + sounddevice）。

    对外保持与原 VLC 版本一致的接口（setMedia/play/pause/stop/setPosition/...），
    内部由 StreamPlayer 实现；pcm_frame 属性暴露最新音频帧供迷你频谱读取。
    """
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    stateChanged = pyqtSignal(PlayerState)
    mediaStatusChanged = pyqtSignal(PlayerMediaStatus)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._volume = 60
        self._engine = StreamPlayer(self)
        self._engine.positionChanged.connect(self.positionChanged)
        self._engine.durationChanged.connect(self.durationChanged)
        self._engine.stateChanged.connect(self.stateChanged)
        self._engine.mediaStatusChanged.connect(self.mediaStatusChanged)

    # ---------- 与原接口一致 ----------
    def setMedia(self, url: str, headers: dict = None, duration_ms: int = 0):
        self._engine.set_media(url, headers, duration_ms)
        self._engine.set_volume(self._volume)

    def play(self, volume=None):
        if volume is not None:
            self._volume = int(volume)
            self._engine.set_volume(self._volume)
        self._engine.play()

    def pause(self):
        self._engine.pause()

    def stop(self):
        self._engine.stop()

    def setPosition(self, pos_ms: int):
        self._engine.seek(pos_ms)

    def position(self) -> int:
        return self._engine.position()

    def duration(self) -> int:
        return self._engine.duration()

    def setVolume(self, vol: int):
        self._volume = int(vol)
        self._engine.set_volume(self._volume)

    def volume(self) -> int:
        return self._volume

    def state(self) -> PlayerState:
        return self._engine.state()

    def mediaStatus(self) -> PlayerMediaStatus:
        return self._engine.media_status()

    def reset(self):
        self._engine.reset()

    # ---------- 频谱数据源（与可视化窗口共用） ----------
    @property
    def pcm_frame(self) -> object:
        """最新单声道音频帧（1024 样本 float32），供迷你频谱读取"""
        return self._engine.pcm_frame
