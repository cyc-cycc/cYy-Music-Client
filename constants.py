# -*- coding: utf-8 -*-
import os
import sys
from enum import IntEnum

# ==================== 路径常量 ====================
APP_DIR = os.path.dirname(os.path.abspath(__file__)) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
if sys.platform == 'darwin':
    DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", "musicspdgui-cyy")
else:
    DATA_DIR = APP_DIR
LOG_DIR = os.path.join(DATA_DIR, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'musicdl_gui.log')
DEFAULT_SAVE_DIR = os.path.join(DATA_DIR, 'download')

# ==================== 搜索源相关 ====================
SOURCE_GROUPS = {
    '国内音乐': [
        'QQ音乐(高质量无损,推荐)',
        '网易云音乐(高质量无损)',
        '酷我音乐(普通无损,推荐)',
        '酷狗音乐(普通无损)',
        '咪咕音乐(普通音质,推荐)',
        '5sing音乐'
    ],
    '国外音乐': [
        'SoundCloud(for XuiS😍)',
    ]
}

SOURCE_INTERNAL = {
    '网易云音乐(高质量无损)': 'NeteaseMusicClient',
    'QQ音乐(高质量无损,推荐)': 'QQMusicClient',
    '酷我音乐(普通无损,推荐)': 'KuwoMusicClient',
    '酷狗音乐(普通无损)': 'KugouMusicClient',
    '咪咕音乐(普通音质,推荐)': 'MiguMusicClient',
    'SoundCloud(for XuiS😍)': 'SoundCloudMusicClient',
    '5sing音乐': 'FiveSingMusicClient',
}

FILENAME_FORMATS = ['歌曲名', '歌手-歌曲名', '歌曲名-歌手', '自定义']

PLAYLIST_SOURCE_MAP = {
    '网易云音乐': 'NeteaseMusicClient',
    'QQ音乐': 'QQMusicClient',
    '酷我音乐': 'KuwoMusicClient',
    '酷狗音乐': 'KugouMusicClient',
    '5sing音乐': 'FiveSingMusicClient',
}

# ==================== 播放器枚举 ====================
class PlayerState(IntEnum):
    StoppedState = 0
    PlayingState = 1
    PausedState = 2

class PlayerMediaStatus(IntEnum):
    UnknownMediaStatus = 0
    NoMedia = 1
    LoadingMedia = 2
    LoadedMedia = 3
    BufferingMedia = 4
    BufferedMedia = 5
    EndOfMedia = 6
    InvalidMedia = 7

class PlayMode(IntEnum):
    SingleRepeat = 0
    SingleStop = 1
    ListRepeat = 2
    ListStop = 3
