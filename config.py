# -*- coding: utf-8 -*-
import os
import sys
import json
from pathlib import Path
from constants import DATA_DIR, DEFAULT_SAVE_DIR

def get_config_dir():
    if sys.platform == 'win32':
        return Path(DATA_DIR) / '.CMC'
    elif sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / '.CMC'
    else:
        return Path.home() / '.config' / '.CMC'

CONFIG_DIR = get_config_dir()
CONFIG_FILE = CONFIG_DIR / 'config.json'

DEFAULT_SETTINGS = {
    'sources': ['酷我音乐(普通无损,推荐)'],
    'limit': 10,
    'dedup': False,
    'save_dir': DEFAULT_SAVE_DIR,
    'filename_format': '歌手-歌曲名',
    'custom_format': '',
    'download_lyric': True,
    'download_cover': True,
    'volume': 60,
    'play_mode': 2,
    'convert_enabled': False,
    'convert_format': 'mp3',
    'convert_bitrate': '320k',
    'theme': 'light',
    'background_opacity': 0.8,
    'embed_lyrics': False,
    'delete_lyrics': False,
    'embed_cover': False,
    'delete_cover': False,
    'group_by': '无分组',
}

def load_settings() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for k, v in DEFAULT_SETTINGS.items():
                    if k not in data:
                        data[k] = v
                return data
        except Exception as e:
            from utils import logger
            logger.error(f"加载配置失败: {e}")
    return DEFAULT_SETTINGS.copy()

def save_settings(settings: dict):
    try:
        CONFIG_DIR.mkdir(exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        from utils import logger
        logger.error(f"保存配置失败: {e}")
