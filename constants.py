# -*- coding: utf-8 -*-
import os
import sys
from enum import IntEnum

# ==================== 路径常量 ====================
APP_DIR = os.path.dirname(os.path.abspath(__file__)) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
if sys.platform == 'darwin':
    DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", "CMC")
else:
    DATA_DIR = APP_DIR
LOG_DIR = os.path.join(DATA_DIR, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'CMC.log')
DEFAULT_SAVE_DIR = os.path.join(DATA_DIR, 'download')

# ==================== 搜索源相关 ====================
SOURCE_GROUPS = {
    '国内音乐': [
        'QQ音乐(高质量无损,推荐)',
        '网易云音乐(高质量无损)',
        '酷我音乐(普通无损,推荐)',
        '酷狗音乐(普通无损)',
        '咪咕音乐(普通音质,推荐)',
        '5sing音乐',
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

# ==================== 下载分组方式 ====================
GROUP_BY_OPTIONS = ['无分组', '按歌手', '按专辑', '按歌手-专辑']

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

# ==================== 主题定义 ====================
THEMES = {
    'light': {
        'display_name': '亮色',
        'primary': '#4A90D9',
        'primary_light': '#5DADE2',
        'primary_dark': '#357ABD',
        'background': '#F5F7FA',
        'content_rgb': '200,225,245',
        'surface': '#FFFFFF',
        'text': '#2C3E50',
        'title_text': '#2C3E50',
        'text_secondary': '#5D6D7E',
        'border': '#BDC3C7',
        'title_bar': '#E8F0FE',
        'hover': '#D5D8DC',
        'selected': '#4A90D9',
        'shadow': 'rgba(0,0,0,30)',
        'progress_gradient': 'qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4A90D9, stop:1 #7B2FFC)',
        'surface_rgb': '255,255,255',
    },
    'dark': {
        'display_name': '暗色',
        'primary': '#4A90D9',
        'primary_light': '#5DADE2',
        'primary_dark': '#357ABD',
        'background': '#2C3E50',
        'content_rgb': '44,62,80',
        'surface': '#34495E',
        'text': '#ECF0F1',
        'title_text': '#ECF0F1',
        'text_secondary': '#BDC3C7',
        'border': '#5D6D7E',
        'title_bar': '#34495E',
        'hover': '#5D6D7E',
        'selected': '#4A90D9',
        'shadow': 'rgba(255,255,255,30)',
        'progress_gradient': 'qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4A90D9, stop:1 #7B2FFC)',
        'surface_rgb': '52,73,94',
    },
}

# ==================== 加密常量 ====================
ENCRYPTION_PASSWORD = "cYy4_Music3_Client0_playlist_PASSWORD"   # 可由外部覆盖

# ==================== 刷新搜索大小 ====================
REFRESH_SEARCH_SIZE = 2
