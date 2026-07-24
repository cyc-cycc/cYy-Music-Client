# -*- coding: utf-8 -*-
import os
import sys
from PyQt5.QtCore import QObject, pyqtSignal, QTimer
import vlc

from constants import PlayerState, PlayerMediaStatus, DATA_DIR

class PlayerWrapper(QObject):
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    stateChanged = pyqtSignal(PlayerState)
    mediaStatusChanged = pyqtSignal(PlayerMediaStatus)

    def __init__(self, parent=None):
        super().__init__(parent)
        vlc_log = os.path.join(DATA_DIR, 'vlc.log')
        vlc_args = ['--verbose=0', f'--logfile={vlc_log}']
        plugin_path = os.environ.get('VLC_PLUGIN_PATH')
        if plugin_path:
            vlc_args.append(f'--plugin-path={plugin_path}')
        self._instance = vlc.Instance(*vlc_args)
        self._player = self._instance.media_player_new()
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_position)
        self._timer.setInterval(200)
        self._duration = 0
        self._current_media = None

    def setMedia(self, url: str, headers: dict = None):
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
        self._player.play()
        self._timer.start()
        self.stateChanged.emit(PlayerState.PlayingState)
        if volume is not None:
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(500, lambda: self._player.audio_set_volume(volume))

    def pause(self):
        self._player.pause()
        self.stateChanged.emit(PlayerState.PausedState)

    def stop(self):
        self._player.stop()
        self._timer.stop()
        self.positionChanged.emit(0)
        self.stateChanged.emit(PlayerState.StoppedState)

    def setPosition(self, pos_ms: int):
        self._player.set_time(pos_ms)

    def position(self) -> int:
        return self._player.get_time()

    def duration(self) -> int:
        return self._player.get_length()

    def setVolume(self, vol: int):
        self._player.audio_set_volume(vol)

    def volume(self) -> int:
        return self._player.audio_get_volume()

    def state(self) -> PlayerState:
        state = self._player.get_state()
        if state == vlc.State.Playing:
            return PlayerState.PlayingState
        elif state == vlc.State.Paused:
            return PlayerState.PausedState
        else:
            return PlayerState.StoppedState

    def mediaStatus(self) -> PlayerMediaStatus:
        state = self._player.get_state()
        if state in (vlc.State.Ended, vlc.State.Stopped):
            return PlayerMediaStatus.EndOfMedia
        elif state == vlc.State.Playing:
            return PlayerMediaStatus.LoadedMedia
        else:
            return PlayerMediaStatus.LoadedMedia

    def _update_position(self):
        try:
            pos = self._player.get_time()
            if pos >= 0:
                self.positionChanged.emit(pos)
            dur = self._player.get_length()
            if dur != self._duration and dur > 0:
                self._duration = dur
                self.durationChanged.emit(dur)
            state = self._player.get_state()
            if state == vlc.State.Ended:
                self._timer.stop()
                self.mediaStatusChanged.emit(PlayerMediaStatus.EndOfMedia)
                self.stateChanged.emit(PlayerState.StoppedState)
        except Exception as e:
            from utils import logger
            logger.error(f"VLC 位置更新异常: {e}", exc_info=True)

    def reset(self):
        self.stop()
        self._player = self._instance.media_player_new()
        self._duration = 0
        self._current_media = None
        self.durationChanged.emit(0)
