# -*- coding: utf-8 -*-
import os
import sys
import re
import logging
import traceback
import time
import subprocess
from logging.handlers import RotatingFileHandler
from typing import Dict, Optional, Tuple

import requests
import filetype

from constants import DATA_DIR, LOG_DIR, LOG_FILE, DEFAULT_SAVE_DIR
from constants import THEMES

# ==================== 运行时路径设置 ====================
def setup_runtime_paths():
    """在源码或打包环境下，自动定位 VLC 和 FFmpeg 并设置环境变量（支持 Windows / macOS）"""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(sys.argv[0]))

    # ---------- VLC ----------
    vlc_dir = None
    candidates = [
        base,
        os.path.join(base, 'vlc'),
        os.path.join(base, 'VLC'),
    ]
    if os.path.exists(os.path.join(base, 'libvlc.dll')) or os.path.exists(os.path.join(base, 'libvlc.dylib')):
        candidates.append(base)

    for cand in candidates:
        if sys.platform == 'win32':
            if os.path.isdir(cand) and os.path.exists(os.path.join(cand, 'libvlc.dll')):
                vlc_dir = cand
                break
        else:  # macOS / Linux
            if os.path.isdir(cand) and (os.path.exists(os.path.join(cand, 'libvlc.dylib')) or
                                        os.path.exists(os.path.join(cand, 'libvlc.so'))):
                vlc_dir = cand
                break

    if vlc_dir is None and sys.platform == 'darwin':
        vlc_app_lib = '/Applications/VLC.app/Contents/MacOS/lib'
        if os.path.exists(os.path.join(vlc_app_lib, 'libvlc.dylib')):
            vlc_dir = vlc_app_lib
        else:
            try:
                import subprocess
                prefix = subprocess.check_output(['brew', '--prefix', 'vlc'], text=True).strip()
                vlc_brew_lib = os.path.join(prefix, 'lib')
                if os.path.exists(os.path.join(vlc_brew_lib, 'libvlc.dylib')):
                    vlc_dir = vlc_brew_lib
            except Exception:
                pass

    if vlc_dir:
        if sys.platform == 'win32':
            os.environ['PATH'] = vlc_dir + os.pathsep + os.environ.get('PATH', '')
            if hasattr(os, 'add_dll_directory'):
                try:
                    os.add_dll_directory(vlc_dir)
                except Exception:
                    pass
            plugin_dir = os.path.join(vlc_dir, 'plugins')
            if os.path.isdir(plugin_dir):
                os.environ['VLC_PLUGIN_PATH'] = plugin_dir
        else:
            lib_path = os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', '')
            if lib_path:
                os.environ['DYLD_FALLBACK_LIBRARY_PATH'] = vlc_dir + os.pathsep + lib_path
            else:
                os.environ['DYLD_FALLBACK_LIBRARY_PATH'] = vlc_dir
            plugin_dir = os.path.join(vlc_dir, 'plugins')
            if not os.path.isdir(plugin_dir):
                parent = os.path.dirname(vlc_dir)
                if os.path.isdir(os.path.join(parent, 'plugins')):
                    plugin_dir = os.path.join(parent, 'plugins')
            if os.path.isdir(plugin_dir):
                os.environ['VLC_PLUGIN_PATH'] = plugin_dir

    # ---------- FFmpeg ----------
    ffmpeg_bin = None
    ffmpeg_lib = None
    for cand in [os.path.join(base, 'ffmpeg', 'bin'), os.path.join(base, 'ffmpeg')]:
        if os.path.isdir(cand):
            if sys.platform == 'win32':
                if os.path.exists(os.path.join(cand, 'ffmpeg.exe')):
                    ffmpeg_bin = cand
                    ffmpeg_lib = os.path.join(os.path.dirname(cand), 'lib') if os.path.isdir(os.path.join(os.path.dirname(cand), 'lib')) else None
                    break
            else:
                if os.path.exists(os.path.join(cand, 'ffmpeg')):
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
        except Exception:
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

    # 始终先定义 formatter（避免 try/except 中未定义导致 NameError）
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    file_handler = None
    try:
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
        file_handler.setLevel(logging.ERROR if os.getenv('MUSICDL_GUI_DEBUG') is None else logging.DEBUG)
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
        # 按需导入 QApplication，避免模块级依赖
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import QTimer
        app = QApplication.instance()
        if app:
            main_widget = app.activeWindow()
            if main_widget and hasattr(main_widget, 'label_stats'):
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
    sess_local = False
    try:
        sess = session or requests.Session()
        if session is None:
            sess_local = True
        kw = request_kwargs.copy() if request_kwargs else {}
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
            data_arr = bytearray()
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                data_arr.extend(chunk)
                if len(data_arr) > max_size:
                    logger.warning("封面数据超过限制，截断")
                    return None, None
            if not data_arr:
                return None, None

            data_bytes = bytes(data_arr)
            kind = filetype.guess(data_bytes)
            if kind and kind.extension in ('jpg', 'jpeg', 'png', 'bmp', 'gif'):
                ext = kind.extension
                if ext == 'jpeg':
                    ext = 'jpg'
                return data_bytes, ext
            else:
                content_type = resp.headers.get('content-type', '').lower()
                if 'png' in content_type:
                    return data_bytes, 'png'
                elif 'jpeg' in content_type or 'jpg' in content_type:
                    return data_bytes, 'jpg'
                else:
                    logger.warning(f"未知图片格式: {content_type}")
                    return None, None
    except Exception as e:
        logger.error(f"图片下载异常: {e}", exc_info=True)
        return None, None
    finally:
        if sess_local:
            try:
                sess.close()
            except Exception:
                pass

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

# ==================== 依赖检查工具 ====================
def check_vlc() -> bool:
    """检查 VLC 是否可用（尝试导入 vlc 并创建实例）"""
    try:
        import vlc
        inst = vlc.Instance('--verbose=0')
        return True
    except Exception:
        return False

def check_dependencies():
    """返回依赖状态，仅检测 VLC（FFmpeg 和 PortAudio 已内置）"""
    return {
        'vlc': check_vlc(),
    }

# ==================== 全局样式表 ====================
def get_global_stylesheet(theme_name: str = 'light', bg_opacity: float = 0.8) -> str:
    colors = THEMES.get(theme_name, THEMES['light'])
    alpha = int(255 * max(0.5, min(1.0, bg_opacity)))
    content_bg = f"rgba({colors['content_rgb']},{alpha})"
    colors_with_alpha = colors.copy()
    colors_with_alpha['content_bg'] = content_bg
    template = """
    /* 基础字体和全局背景 */
    QWidget {{
        font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", "Segoe UI", sans-serif;
        font-size: 12px;
        color: {text};
    }}
    #musicdlGUI {{
        background-color: {background};
        border-radius: 8px;
    }}
    #titleBar {{
        background-color: {title_bar};
        border-radius: 8px;
        border-bottom: 1px solid {border};
    }}
    #titleBar QLabel {{
        background: transparent;
        font-size: 14px;
        font-weight: bold;
        color: {title_text};
    }}
    #titleSearchButton, #titleSettingsButton, #titleAboutButton {{
        background-color: transparent;
        border: none;
        border-radius: 4px;
        font-size: 16px;
        color: {text};
    }}
    #titleSearchButton:hover, #titleSettingsButton:hover, #titleAboutButton:hover {{
        background-color: {hover};
    }}
    #titleMinButton, #titleMaxButton, #titleCloseButton {{
        background-color: transparent;
        border: none;
        border-radius: 4px;
        font-size: 16px;
        font-weight: bold;
        color: {text};
    }}
    #titleMinButton:hover {{ background-color: {hover}; }}
    #titleMaxButton:hover {{ background-color: {hover}; }}
    #titleCloseButton:hover {{ background-color: #E74C3C; color: white; }}
    #contentWidget {{
        background-color: {content_bg};
        border-radius: 8px;
    }}
    QGroupBox {{
        font-weight: bold;
        border: 1px solid {border};
        border-radius: 5px;
        margin-top: 10px;
        padding-top: 10px;
        color: {text};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 5px;
        color: {text};
    }}
    QGroupBox#playGroup {{
        background-color: {title_bar};
        border-color: {primary};
    }}
    QLabel {{ color: {text}; }}
    QCheckBox {{ color: {text}; spacing: 5px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; }}
    QLineEdit, QSpinBox, QComboBox {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 5px;
        padding: 5px;
        color: {text};
    }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
        border: 1px solid {primary};
    }}
    QPushButton {{
        background-color: {hover};
        color: {text};
        border: 1px solid {border};
        border-radius: 4px;
        padding: 4px 10px;
    }}
    QPushButton:hover {{ background-color: {border}; }}
    QPushButton#playButton {{
        background-color: {primary};
        color: white;
        font-weight: bold;
        border: none;
    }}
    QPushButton#playButton:hover {{ background-color: {primary_dark}; }}
    QPushButton#stopButton {{
        background-color: #E67E22;
        color: white;
        font-weight: bold;
        border: none;
    }}
    QPushButton#stopButton:hover {{ background-color: #D35400; }}
    QPushButton#prevButton, QPushButton#nextButton {{
        background-color: {primary_light};
        color: white;
        font-weight: bold;
        border: none;
        border-radius: 4px;
    }}
    QPushButton#prevButton:hover, QPushButton#nextButton:hover {{
        background-color: {primary};
    }}
    QPushButton#visualizeButton {{
        background-color: #8E44AD;
        color: white;
        font-weight: bold;
        border: none;
        border-radius: 4px;
    }}
    QPushButton#visualizeButton:hover {{ background-color: #6C3483; }}
    QPushButton#parsePlaylistButton {{
        background-color: #8E44AD;
        color: white;
        font-weight: bold;
        border: none;
        border-radius: 4px;
    }}
    QPushButton#parsePlaylistButton:hover {{ background-color: #6C3483; }}
    QTableWidget#resultTable {{
        background-color: {surface};
        alternate-background-color: {hover};
        border: 1px solid {border};
        border-radius: 5px;
        gridline-color: {border};
    }}
    QTableWidget::item {{ padding: 4px; color: {text}; }}
    QTableWidget::item:selected {{ background-color: {primary}; color: white; }}
    QHeaderView::section {{
        background-color: {primary};
        color: white;
        padding: 5px;
        border: none;
    }}
    QProgressBar {{
        border: 1px solid {border};
        border-radius: 5px;
        background-color: {surface};
        text-align: center;
        color: {text};
        font-weight: bold;
    }}
    QProgressBar::chunk {{
        background-color: {primary};
        border-radius: 5px;
    }}
    QLabel#statsLabel {{
        color: {primary};
        font-weight: bold;
        font-size: 13px;
        background-color: rgba(74, 144, 217, 0.1);
        border-radius: 5px;
        padding: 4px;
    }}
    QMenu {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 5px;
    }}
    QMenu::item {{
        padding: 6px 20px;
        color: {text};
    }}
    QMenu::item:selected {{
        background-color: {primary};
        color: white;
    }}
    QSlider::groove:horizontal {{
        height: 6px;
        background: {border};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        background: {primary};
        width: 14px;
        height: 14px;
        margin: -4px 0;
        border-radius: 7px;
    }}
    QSlider::sub-page:horizontal {{
        background: {primary};
        border-radius: 3px;
    }}
    QListWidget {{
        background-color: transparent;
        border: none;
        outline: none;
    }}
    QListWidget::item {{
        padding: 2px 5px;
        color: {text};
    }}
    QListWidget::item:selected {{
        background: transparent;
    }}
    QTabWidget::pane {{
        border: 1px solid {border};
        border-radius: 5px;
        background: {surface};
    }}
    QTabBar::tab {{
        background: {hover};
        color: {text};
        padding: 8px 16px;
        margin-right: 2px;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
        border: 1px solid {border};
        border-bottom: none;
    }}
    QTabBar::tab:selected {{
        background: {primary};
        color: white;
    }}
    QTabBar::tab:hover:!selected {{
        background: {border};
    }}
    QDialog {{
        background: {background};
        border-radius: 8px;
    }}
    QSpinBox::up-button, QSpinBox::down-button {{
        background: {hover};
        border: none;
        border-radius: 2px;
        width: 16px;
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
        background: {border};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 20px;
        border-left: 1px solid {border};
        border-top-right-radius: 5px;
        border-bottom-right-radius: 5px;
        background: {hover};
    }}
    QComboBox::down-arrow {{
        width: 12px;
        height: 12px;
    }}
    QComboBox QAbstractItemView {{
        border: 1px solid {border};
        border-radius: 5px;
        background: {surface};
        selection-background-color: {primary};
        selection-color: white;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: rgba(160, 160, 160, 180);
        border-radius: 4px;
        min-height: 20px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
        background: transparent;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
        margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background: rgba(160, 160, 160, 180);
        border-radius: 4px;
        min-width: 20px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
        background: transparent;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
    }}
    QToolTip {{
        background: {text};
        color: {surface};
        border: none;
        border-radius: 4px;
        padding: 4px;
    }}
    QLabel#playlist_title, QLabel#nowPlayingLabel {{
        color: {text};
        font-weight: bold;
    }}
    /* 时长标签（进度条右侧） */
    QLabel#label_time {{
        color: {text};
        background-color: transparent;
    }}

    /* 结果卡片（SongCard） */
    #songCard {{
        background: {surface};
        border: 1px solid {border};
        border-radius: 8px;
    }}
    #songCard:hover {{
        border: 1px solid {primary};
        background: {hover};
    }}
    #songCard[selected="true"] {{
        border: 2px solid {primary};
        background: {title_bar};
    }}
    /* 设置对话框标题栏 */
    #settingsTitleBar {{
        background-color: {title_bar};
        border-radius: 8px;
        border-bottom: 1px solid {border};
    }}
    #settingsContent {{
        background-color: {background};
        border-radius: 8px;
    }}

    /* 结果卡片内部文字 */
    #songCard QLabel#titleLabel {{
        color: {text};
        font-weight: bold;
        font-size: 16px;
    }}
    #songCard QLabel#subLabel {{
        color: {text_secondary};
        font-size: 12px;
    }}
    #songCard QLabel#sourceLabel {{
        background-color: {hover};
        color: {text};
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 11px;
    }}
    /* ===== 可视化窗口 ===== */
    AudioVisualizer QFrame#centralWidget {{
        background: rgba({surface_rgb}, 0.5);
        border-radius: 8px;
    }}
    AudioVisualizer #titleBar {{
        background-color: {title_bar};
        border-bottom: 1px solid {border};
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
    }}
    AudioVisualizer #contentWidget {{
        background: transparent;
        border-bottom-left-radius: 8px;
        border-bottom-right-radius: 8px;
    }}
    AudioVisualizer #leftWidget, AudioVisualizer #rightWidget {{
        background: rgba({surface_rgb}, 0.8);
        border-radius: 8px;
    }}
    AudioVisualizer #controlWidget {{
        background: rgba({surface_rgb}, 0.8);
        border-radius: 8px;
    }}
    AudioVisualizer QLabel#songTitle {{
        color: {text};
        font-weight: bold;
        font-size: 16px;
    }}
    AudioVisualizer QPushButton {{
        background: rgba({surface_rgb}, 0.8);
        color: {text};
        border: 1px solid {border};
        border-radius: 4px;
        padding: 5px 12px;
        font-weight: bold;
    }}
    AudioVisualizer QPushButton:hover {{
        background: {hover};
        border-color: {primary};
    }}
    AudioVisualizer QPushButton:pressed {{
        background: rgba({surface_rgb}, 0.5);
    }}
    AudioVisualizer QPushButton:disabled {{
        color: {border};
    }}
    AudioVisualizer QSlider::groove:horizontal {{
        height: 6px;
        background: {border};
        border-radius: 3px;
    }}
    AudioVisualizer QSlider::handle:horizontal {{
        background: {primary};
        width: 16px;
        margin: -5px 0;
        border-radius: 8px;
    }}
    AudioVisualizer QSlider::sub-page:horizontal {{
        background: {progress_gradient};
        border-radius: 3px;
    }}
    AudioVisualizer QListWidget {{
        background: transparent;
        border: none;
        outline: none;
    }}
    AudioVisualizer QListWidget::item {{
        padding: 2px 5px;
        color: {text};
        background: transparent;
    }}
    """
    return template.format(**colors_with_alpha)

# ==================== 新增工具函数 ====================
def build_filename(song_info: Dict, fmt: str) -> str:
    """
    根据格式模板生成文件名（不含扩展名）
    :param song_info: 歌曲信息字典
    :param fmt: 格式字符串，如 "歌手-歌曲名" 或 "{歌手}-{歌曲名}"
    :return: 生成的文件名
    """
    song_name = song_info.get('song_name', '')
    singers = song_info.get('singers', '')
    if fmt == "歌曲名":
        return song_name
    elif fmt == "歌手-歌曲名":
        return f"{singers}-{song_name}"
    elif fmt == "歌曲名-歌手":
        return f"{song_name}-{singers}"
    else:  # 自定义模板
        template = fmt
        template = template.replace("{歌手}", singers)
        template = template.replace("{歌曲名}", song_name)
        template = template.replace("{专辑}", song_info.get('album', ''))
        template = template.replace("{时长}", song_info.get('duration', ''))
        return template

def safe_stop_thread(thread, signals_to_disconnect=None, finished_callback=None):
    if thread is None:
        return
    if not thread.isRunning():
        return
    if hasattr(thread, 'stop'):
        thread.stop()
    if signals_to_disconnect:
        for sig_name in signals_to_disconnect:
            sig = getattr(thread, sig_name, None)
            if sig:
                try:
                    sig.disconnect()
                except TypeError:
                    pass
    if finished_callback:
        try:
            thread.finished.disconnect()
        except TypeError:
            pass
        thread.finished.connect(finished_callback)
    else:
        thread.wait()
        thread.deleteLater()

# ==================== 音频格式转换（新增） ====================
def convert_audio(input_path: str, output_format: str, bitrate: str = None) -> str:
    """
    使用 ffmpeg 转换音频格式。
    返回输出文件路径，若失败返回 None。
    """
    if not output_format:
        return input_path
    # 检查 ffmpeg 是否可用
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.warning("FFmpeg 未找到，跳过格式转换")
        return None

    base, _ = os.path.splitext(input_path)
    output_path = f"{base}.{output_format}"
    if os.path.exists(output_path):
        # 如果已存在，添加后缀
        counter = 1
        while os.path.exists(f"{base}_{counter}.{output_format}"):
            counter += 1
        output_path = f"{base}_{counter}.{output_format}"
    
    cmd = ['ffmpeg', '-y', '-i', input_path]
    if bitrate:
        cmd.extend(['-b:a', bitrate])
    if output_format == 'mp3':
        cmd.extend(['-acodec', 'libmp3lame'])
    elif output_format == 'aac':
        cmd.extend(['-acodec', 'aac'])
    elif output_format == 'ogg':
        cmd.extend(['-acodec', 'libvorbis'])
    elif output_format == 'flac':
        cmd.extend(['-acodec', 'flac'])
    cmd.append(output_path)

    startupinfo = None
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    try:
        subprocess.run(cmd, capture_output=True, check=True, startupinfo=startupinfo)
        logger.info(f"转换成功: {output_path}")
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"转换失败: {e.stderr.decode() if e.stderr else '未知错误'}")
        return None
