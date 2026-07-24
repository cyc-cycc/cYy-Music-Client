# -*- coding: utf-8 -*-
import os
import sys
import re
import logging
import traceback
import time
from logging.handlers import RotatingFileHandler
from typing import Dict, Optional, Tuple

import requests
import filetype

from constants import DATA_DIR, LOG_DIR, LOG_FILE, DEFAULT_SAVE_DIR

# ==================== 资源路径查找工具 ====================
def get_resource_path(relative_path: str) -> str:
    """
    在打包环境下（PyInstaller / Nuitka）返回资源的绝对路径。
    开发环境下返回相对于当前文件的路径。
    """
    if getattr(sys, 'frozen', False):
        # ----- PyInstaller 模式 -----
        if hasattr(sys, '_MEIPASS'):
            base = sys._MEIPASS
        # ----- Nuitka 模式（standalone） -----
        else:
            # 在 macOS .app 中，可执行文件位于 .app/Contents/MacOS/
            # 资源通常放在 .app/Contents/Resources/ 或与可执行文件同级
            base = os.path.dirname(sys.executable)
            # 如果可执行文件在 MacOS 目录下，资源在上级 Resources 目录
            if sys.platform == 'darwin' and base.endswith('MacOS'):
                base = os.path.join(base, '..', 'Resources')
            # 如果是其他平台（Linux）可能在同一目录，暂不额外处理
    else:
        # ----- 开发模式（源码运行）-----
        base = os.path.dirname(os.path.abspath(__file__))
    
    return os.path.join(base, relative_path)

def setup_runtime_paths():
    """在源码或打包环境下，自动定位 VLC 和 FFmpeg 并设置环境变量（支持 Windows / macOS）"""
    # ----- 首先判断打包模式 -----
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            # PyInstaller 模式
            base = sys._MEIPASS
        else:
            # Nuitka 模式，可执行文件所在目录（或 Resources）
            base = os.path.dirname(sys.executable)
            if sys.platform == 'darwin' and base.endswith('MacOS'):
                base = os.path.join(base, '..', 'Resources')
    else:
        # 开发模式
        base = os.path.dirname(os.path.abspath(sys.argv[0]))
    
    # ---------- VLC ----------
    vlc_dir = None
    candidates = [
        base,
        os.path.join(base, 'vlc'),
        os.path.join(base, 'VLC'),
        os.path.join(base, 'resources', 'vlc'),   # Nuitka 中我们把资源放在 resources/vlc
    ]
    # 如果根目录有 libvlc 库文件，直接作为候选
    if sys.platform == 'win32':
        if os.path.exists(os.path.join(base, 'libvlc.dll')):
            candidates.append(base)
    else:
        if os.path.exists(os.path.join(base, 'libvlc.dylib')):
            candidates.append(base)
    
    for cand in candidates:
        if os.path.isdir(cand):
            if sys.platform == 'win32' and os.path.exists(os.path.join(cand, 'libvlc.dll')):
                vlc_dir = cand
                break
            elif sys.platform == 'darwin' and os.path.exists(os.path.join(cand, 'libvlc.dylib')):
                vlc_dir = cand
                break
            elif sys.platform.startswith('linux') and os.path.exists(os.path.join(cand, 'libvlc.so')):
                vlc_dir = cand
                break
    
    # 如果没找到，尝试系统路径（brew 等）
    if vlc_dir is None and sys.platform == 'darwin':
        # 尝试 /Applications/VLC.app
        vlc_app = '/Applications/VLC.app/Contents/MacOS'
        if os.path.exists(os.path.join(vlc_app, 'libvlc.dylib')):
            vlc_dir = vlc_app
        else:
            try:
                import subprocess
                prefix = subprocess.check_output(['brew', '--prefix', 'vlc'], text=True).strip()
                brew_lib = os.path.join(prefix, 'lib')
                if os.path.exists(os.path.join(brew_lib, 'libvlc.dylib')):
                    vlc_dir = brew_lib
            except:
                pass
    
    if vlc_dir:
        if sys.platform == 'win32':
            os.environ['PATH'] = vlc_dir + os.pathsep + os.environ.get('PATH', '')
            if hasattr(os, 'add_dll_directory'):
                try:
                    os.add_dll_directory(vlc_dir)
                except:
                    pass
            # 查找 plugins 子目录
            plugin_dir = os.path.join(vlc_dir, 'plugins')
            if not os.path.isdir(plugin_dir):
                # 也许 plugins 在上级目录？但我们不处理，直接设为环境变量
                pass
            if os.path.isdir(plugin_dir):
                os.environ['VLC_PLUGIN_PATH'] = plugin_dir
        else:
            # macOS / Linux 设置库路径
            lib_path = os.environ.get('DYLD_FALLBACK_LIBRARY_PATH' if sys.platform == 'darwin' else 'LD_LIBRARY_PATH', '')
            if lib_path:
                os.environ['DYLD_FALLBACK_LIBRARY_PATH' if sys.platform == 'darwin' else 'LD_LIBRARY_PATH'] = vlc_dir + os.pathsep + lib_path
            else:
                os.environ['DYLD_FALLBACK_LIBRARY_PATH' if sys.platform == 'darwin' else 'LD_LIBRARY_PATH'] = vlc_dir
            # plugins
            plugin_dir = os.path.join(vlc_dir, 'plugins')
            if not os.path.isdir(plugin_dir):
                # 尝试上级目录（如在打包环境下）
                parent = os.path.dirname(vlc_dir)
                if os.path.isdir(os.path.join(parent, 'plugins')):
                    plugin_dir = os.path.join(parent, 'plugins')
            if os.path.isdir(plugin_dir):
                os.environ['VLC_PLUGIN_PATH'] = plugin_dir

    # ---------- FFmpeg ----------
    # 类似地，查找 ffmpeg 可执行文件
    ffmpeg_bin = None
    ffmpeg_lib = None
    # 在 Nuitka 中，我们把 ffmpeg 放在 resources/ffmpeg/bin
    ffmpeg_candidates = [
        os.path.join(base, 'ffmpeg'),
        os.path.join(base, 'ffmpeg', 'bin'),
        os.path.join(base, 'resources', 'ffmpeg', 'bin'),
    ]
    for cand in ffmpeg_candidates:
        if sys.platform == 'win32':
            if os.path.isdir(cand) and os.path.exists(os.path.join(cand, 'ffmpeg.exe')):
                ffmpeg_bin = cand
                ffmpeg_lib = os.path.join(os.path.dirname(cand), 'lib') if os.path.isdir(os.path.join(os.path.dirname(cand), 'lib')) else None
                break
        else:
            if os.path.isdir(cand) and os.path.exists(os.path.join(cand, 'ffmpeg')):
                ffmpeg_bin = cand
                ffmpeg_lib = os.path.join(os.path.dirname(cand), 'lib') if os.path.isdir(os.path.join(os.path.dirname(cand), 'lib')) else None
                break

    if ffmpeg_bin is None and sys.platform == 'darwin':
        try:
            import subprocess
            prefix = subprocess.check_output(['brew', '--prefix', 'ffmpeg'], text=True).strip()
            brew_bin = os.path.join(prefix, 'bin')
            brew_lib = os.path.join(prefix, 'lib')
            if os.path.exists(os.path.join(brew_bin, 'ffmpeg')):
                ffmpeg_bin = brew_bin
                ffmpeg_lib = brew_lib
        except:
            pass
    
    if ffmpeg_bin:
        os.environ['PATH'] = ffmpeg_bin + os.pathsep + os.environ.get('PATH', '')
        ffmpeg_exe = os.path.join(ffmpeg_bin, 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg')
        if os.path.exists(ffmpeg_exe):
            os.environ['AUDIOREAD_FFMPEG'] = ffmpeg_exe
        if ffmpeg_lib and sys.platform == 'darwin':
            lib_path = os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', '')
            if lib_path:
                os.environ['DYLD_FALLBACK_LIBRARY_PATH'] = ffmpeg_lib + os.pathsep + lib_path
            else:
                os.environ['DYLD_FALLBACK_LIBRARY_PATH'] = ffmpeg_lib

# ==================== 日志设置 ====================
def setup_logging():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)
        os.makedirs(DEFAULT_SAVE_DIR, exist_ok=True)
    except Exception as e:
        print(f"警告：无法创建数据目录：{e}")

    logger = logging.getLogger('MusicdlGUI')
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    file_handler = None
    try:
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
        file_handler.setLevel(logging.ERROR if os.getenv('MUSICDL_GUI_DEBUG') is None else logging.DEBUG)
        file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"警告：无法创建日志文件 {LOG_FILE}：{e}")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.ERROR if os.getenv('MUSICDL_GUI_DEBUG') is None else logging.DEBUG)
    console_handler.setFormatter(file_formatter)
    logger.addHandler(console_handler)

    return logger

logger = setup_logging()

# ==================== 全局异常钩子 ====================
def global_exception_hook(exctype, value, tb):
    error_msg = ''.join(traceback.format_exception(exctype, value, tb))
    logger.error(error_msg)
    try:
        app = QApplication.instance()
        if app:
            main_widget = app.activeWindow()
            if main_widget and hasattr(main_widget, 'label_stats'):
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(0, lambda: main_widget.label_stats.setText(
                    f'⚠️ 程序发生错误，详见日志文件: {LOG_FILE}'
                ))
    except Exception:
        pass
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = global_exception_hook

# ==================== 封面下载工具 ====================
def _download_image_data(
    url: str,
    request_kwargs: Dict,
    max_size: int = 5 * 1024 * 1024,
    session: Optional[requests.Session] = None
) -> Tuple[Optional[bytes], Optional[str]]:
    if not url:
        return None, None
    try:
        sess = session or requests.Session()
        kw = request_kwargs.copy()
        kw['timeout'] = kw.get('timeout', 10)
        kw['stream'] = True
        kw.pop('data', None)
        kw.pop('json', None)

        with sess.get(url, **kw) as resp:
            if resp.status_code != 200:
                return None, None
            content_length = resp.headers.get('content-length')
            if content_length and int(content_length) > max_size:
                logger.warning(f"封面图片过大 ({content_length} bytes)，跳过下载")
                return None, None
            data = b''
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                data += chunk
                if len(data) > max_size:
                    logger.warning("封面数据超过限制，截断")
                    return None, None
            if not data:
                return None, None

            kind = filetype.guess(data)
            if kind and kind.extension in ('jpg', 'jpeg', 'png', 'bmp', 'gif'):
                ext = kind.extension
                if ext == 'jpeg':
                    ext = 'jpg'
                return data, ext
            else:
                content_type = resp.headers.get('content-type', '').lower()
                if 'png' in content_type:
                    return data, 'png'
                elif 'jpeg' in content_type or 'jpg' in content_type:
                    return data, 'jpg'
                else:
                    logger.warning(f"未知图片格式: {content_type}")
                    return None, None
    except Exception as e:
        logger.error(f"图片下载异常: {e}", exc_info=True)
        return None, None

def get_cover_url(song_info: Dict) -> Optional[str]:
    for key in ['cover_url', 'cover', 'song_cover', 'album_cover', 'pic_url', 'img_url']:
        val = song_info.get(key)
        if val:
            return val
    return None

def download_cover_image(url: str, request_kwargs: Dict, max_size: int = 5 * 1024 * 1024) -> Tuple[Optional[bytes], Optional[str]]:
    return _download_image_data(url, request_kwargs, max_size, session=None)

# 从 musicdl 导入 sanitize_filepath 供外部使用
try:
    from musicdl.modules.utils.misc import sanitize_filepath
except ImportError:
    # 如果无法导入，定义简单的替代
    def sanitize_filepath(filename):
        # 简单替换非法字符
        illegal_chars = r'[\\/:*?"<>|]'
        return re.sub(illegal_chars, '_', filename)
