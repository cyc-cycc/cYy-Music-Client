# -*- coding: utf-8 -*-
import os
import sys
from PyQt5.QtCore import QObject, pyqtSignal, QTimer

from constants import PlayerState, PlayerMediaStatus, DATA_DIR

class PlayerWrapper(QObject):
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    stateChanged = pyqtSignal(PlayerState)
    mediaStatusChanged = pyqtSignal(PlayerMediaStatus)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._vlc_available = False
        self._instance = None
        self._player = None
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_position)
        self._timer.setInterval(200)
        self._duration = 0
        self._current_media = None

        # 尝试初始化 VLC
        try:
            import vlc
            vlc_log = os.path.join(DATA_DIR, 'vlc.log')
            vlc_args = ['--verbose=0', f'--logfile={vlc_log}']
            self._instance = vlc.Instance(*vlc_args)
            self._player = self._instance.media_player_new()
            self._vlc_available = True
            # 保存 vlc 模块引用供其他方法使用
            self._vlc = vlc
        except Exception as e:
            from utils import logger
            logger.error(f"VLC 初始化失败: {e}")

    def setMedia(self, url: str, headers: dict = None):
        if not self._vlc_available:
            return
        self._current_media = self._instance.media_new(url)
        self._current_media.add_option(':network-caching=3000')
        if headers:
            for k, v in headers.items():
                if v is not None:
                    self._current_media.add_option(f'--http-header={k}={v}')
        self._player.set_media(self._current_media)
        self._duration = 0
        self.durationChanged.emit(0)
        self.mediaStatusChanged.emit(PlayerMediaStatus.LoadedMedia)

    def play(self, volume=None):
        if not self._vlc_available:
            return
        self._player.play()
        self._timer.start()
        self.stateChanged.emit(PlayerState.PlayingState)
        if volume is not None:
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(500, lambda: self._player.audio_set_volume(volume))

    def pause(self):
        if not self._vlc_available:
            return
        self._player.pause()
        self.stateChanged.emit(PlayerState.PausedState)

    def stop(self):
        if not self._vlc_available:
            self.positionChanged.emit(0)
            self.stateChanged.emit(PlayerState.StoppedState)
            return
        self._player.stop()
        self._timer.stop()
        self.positionChanged.emit(0)
        self.stateChanged.emit(PlayerState.StoppedState)

    def setPosition(self, pos_ms: int):
        if not self._vlc_available:
            return
        self._player.set_time(pos_ms)

    def position(self) -> int:
        if not self._vlc_available:
            return 0
        return self._player.get_time()

    def duration(self) -> int:
        if not self._vlc_available:
            return 0
        return self._player.get_length()

    def setVolume(self, vol: int):
        if not self._vlc_available:
            return
        self._player.audio_set_volume(vol)

    def volume(self) -> int:
        if not self._vlc_available:
            return 0
        return self._player.audio_get_volume()

    def state(self) -> PlayerState:
        if not self._vlc_available:
            return PlayerState.StoppedState
        state = self._player.get_state()
        if state == self._vlc.State.Playing:
            return PlayerState.PlayingState
        elif state == self._vlc.State.Paused:
            return PlayerState.PausedState
        else:
            return PlayerState.StoppedState

    def mediaStatus(self) -> PlayerMediaStatus:
        if not self._vlc_available:
            return PlayerMediaStatus.UnknownMediaStatus
        state = self._player.get_state()
        if state in (self._vlc.State.Ended, self._vlc.State.Stopped):
            return PlayerMediaStatus.EndOfMedia
        elif state == self._vlc.State.Playing:
            return PlayerMediaStatus.LoadedMedia
        else:
            return PlayerMediaStatus.LoadedMedia

    def _update_position(self):
        if not self._vlc_available:
            return
        try:
            pos = self._player.get_time()
            if pos >= 0:
                self.positionChanged.emit(pos)
            dur = self._player.get_length()
            if dur != self._duration and dur > 0:
                self._duration = dur
                self.durationChanged.emit(dur)
            state = self._player.get_state()
            if state == self._vlc.State.Ended:
                self._timer.stop()
                self.mediaStatusChanged.emit(PlayerMediaStatus.EndOfMedia)
                self.stateChanged.emit(PlayerState.StoppedState)
        except Exception as e:
            from utils import logger
            logger.error(f"VLC 位置更新异常: {e}", exc_info=True)

    def reset(self):
        if not self._vlc_available:
            self._duration = 0
            self.durationChanged.emit(0)
            return
        self.stop()
        self._player = self._instance.media_player_new()
        self._duration = 0
        self._current_media = None
        self.durationChanged.emit(0)
