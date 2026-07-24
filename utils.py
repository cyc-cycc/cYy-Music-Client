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
def get_global_stylesheet():
    return """
    /* 基础字体和全局背景 */
    QWidget {
        font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", "Segoe UI", sans-serif;
        font-size: 12px;
    }

    /* 主窗口（通过 objectName 匹配） */
    #musicdlGUI {
        background-color: #F5F7FA;
        border-radius: 8px;
    }

    /* 标题栏 */
    #titleBar {
        background-color: #E8F0FE;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        border-bottom: 1px solid #BDC3C7;
    }
    #titleBar QLabel {
        background: transparent;
        font-size: 14px;
    }

    /* 标题栏按钮 */
    #titleSearchButton, #titleSettingsButton, #titleAboutButton {
        background-color: transparent;
        border: none;
        border-radius: 4px;
        font-size: 16px;
        color: #2C3E50;
    }
    #titleSearchButton:hover, #titleSettingsButton:hover, #titleAboutButton:hover {
        background-color: #D5D8DC;
    }
    #titleMinButton, #titleMaxButton, #titleCloseButton {
        background-color: transparent;
        border: none;
        border-radius: 4px;
        font-size: 16px;
        font-weight: bold;
        color: #2C3E50;
    }
    #titleMinButton:hover { background-color: #D5D8DC; }
    #titleMaxButton:hover { background-color: #D5D8DC; }
    #titleCloseButton:hover { background-color: #E74C3C; color: white; }

    /* 内容区域 */
    #contentWidget {
        background-color: rgba(200, 225, 245, 240);
        border-bottom-left-radius: 8px;
        border-bottom-right-radius: 8px;
    }

    /* 分组框 */
    QGroupBox {
        font-weight: bold;
        border: 1px solid #BDC3C7;
        border-radius: 5px;
        margin-top: 10px;
        padding-top: 10px;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 5px;
        color: #2C3E50;
    }
    QGroupBox#playGroup {
        background-color: #E8F0FE;
        border-color: #4A90D9;
    }

    /* 通用标签 */
    QLabel { color: #2C3E50; }

    /* 复选框 */
    QCheckBox { color: #2C3E50; spacing: 5px; }
    QCheckBox::indicator { width: 16px; height: 16px; }

    /* 输入框、数字框、下拉框 */
    QLineEdit, QSpinBox, QComboBox {
        background-color: white;
        border: 1px solid #BDC3C7;
        border-radius: 5px;
        padding: 5px;
        color: #2C3E50;
    }
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
        border: 1px solid #4A90D9;
    }

    /* 按钮基础 */
    QPushButton {
        background-color: #E8EDF2;
        color: #2C3E50;
        border: 1px solid #BDC3C7;
        border-radius: 4px;
        padding: 4px 10px;
    }
    QPushButton:hover { background-color: #D5D8DC; }

    /* 特殊功能按钮（通过 objectName） */
    QPushButton#playButton {
        background-color: #4A90D9;
        color: white;
        font-weight: bold;
        border: none;
    }
    QPushButton#playButton:hover { background-color: #357ABD; }

    QPushButton#stopButton {
        background-color: #E67E22;
        color: white;
        font-weight: bold;
        border: none;
    }
    QPushButton#stopButton:hover { background-color: #D35400; }

    QPushButton#prevButton, QPushButton#nextButton {
        background-color: #5DADE2;
        color: white;
        font-weight: bold;
        border: none;
        border-radius: 4px;
    }
    QPushButton#prevButton:hover, QPushButton#nextButton:hover {
        background-color: #3498DB;
    }

    QPushButton#visualizeButton {
        background-color: #8E44AD;
        color: white;
        font-weight: bold;
        border: none;
        border-radius: 4px;
    }
    QPushButton#visualizeButton:hover { background-color: #6C3483; }

    QPushButton#parsePlaylistButton {
        background-color: #8E44AD;
        color: white;
        font-weight: bold;
        border: none;
        border-radius: 4px;
    }
    QPushButton#parsePlaylistButton:hover { background-color: #6C3483; }

    /* 表格（虽然未使用，但保留兼容） */
    QTableWidget#resultTable {
        background-color: white;
        alternate-background-color: #ECF0F1;
        border: 1px solid #BDC3C7;
        border-radius: 5px;
        gridline-color: #D5D8DC;
    }
    QTableWidget::item { padding: 4px; color: #2C3E50; }
    QTableWidget::item:selected { background-color: #4A90D9; color: white; }
    QHeaderView::section {
        background-color: #4A90D9;
        color: white;
        padding: 5px;
        border: none;
    }

    /* 进度条 */
    QProgressBar {
        border: 1px solid #BDC3C7;
        border-radius: 5px;
        background-color: white;
        text-align: center;
        color: #2C3E50;
        font-weight: bold;
    }
    QProgressBar::chunk {
        background-color: #4A90D9;
        border-radius: 5px;
    }

    /* 状态标签 */
    QLabel#statsLabel {
        color: #1E88E5;
        font-weight: bold;
        font-size: 13px;
        background-color: rgba(74, 144, 217, 0.1);
        border-radius: 5px;
        padding: 4px;
    }

    /* 右键菜单 */
    QMenu {
        background-color: white;
        border: 1px solid #BDC3C7;
        border-radius: 5px;
    }
    QMenu::item {
        padding: 6px 20px;
        color: #2C3E50;
    }
    QMenu::item:selected {
        background-color: #4A90D9;
        color: white;
    }

    /* 滑块 */
    QSlider::groove:horizontal {
        height: 6px;
        background: #D5D8DC;
        border-radius: 3px;
    }
    QSlider::handle:horizontal {
        background: #4A90D9;
        width: 14px;
        height: 14px;
        margin: -4px 0;
        border-radius: 7px;
    }
    QSlider::sub-page:horizontal {
        background: #4A90D9;
        border-radius: 3px;
    }

    /* 列表控件（结果列表） */
    QListWidget {
        background-color: transparent;
        border: none;
        outline: none;
    }
    QListWidget::item {
        padding: 2px 5px;
    }
    /* 卡片式列表的选中状态由卡片自己控制，这里不干扰 */
    QListWidget::item:selected {
        background: transparent;
    }

    /* ==================== 新增补充样式 ==================== */

    /* 标签页 */
    QTabWidget::pane {
        border: 1px solid #BDC3C7;
        border-radius: 5px;
        background: white;
    }
    QTabBar::tab {
        background: #E8EDF2;
        color: #2C3E50;
        padding: 8px 16px;
        margin-right: 2px;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
        border: 1px solid #BDC3C7;
        border-bottom: none;
    }
    QTabBar::tab:selected {
        background: #4A90D9;
        color: white;
    }
    QTabBar::tab:hover:!selected {
        background: #D5D8DC;
    }

    /* 对话框 */
    QDialog {
        background: #F5F7FA;
        border-radius: 8px;
    }

    /* 数字输入框的箭头按钮 */
    QSpinBox::up-button, QSpinBox::down-button {
        background: #E8EDF2;
        border: none;
        border-radius: 2px;
        width: 16px;
    }
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {
        background: #D5D8DC;
    }

    /* 下拉框的下拉按钮 */
    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 20px;
        border-left: 1px solid #BDC3C7;
        border-top-right-radius: 5px;
        border-bottom-right-radius: 5px;
        background: #E8EDF2;
    }
    QComboBox::down-arrow {
        /* 使用 Unicode 箭头或图片，这里用默认 */
        width: 12px;
        height: 12px;
    }
    QComboBox QAbstractItemView {
        border: 1px solid #BDC3C7;
        border-radius: 5px;
        background: white;
        selection-background-color: #4A90D9;
        selection-color: white;
    }

    /* 滚动条（垂直/水平） */
    QScrollBar:vertical {
        background: transparent;
        width: 8px;
        margin: 0px;
    }
    QScrollBar::handle:vertical {
        background: rgba(160, 160, 160, 180);
        border-radius: 4px;
        min-height: 20px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
        background: transparent;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: transparent;
    }

    QScrollBar:horizontal {
        background: transparent;
        height: 8px;
        margin: 0px;
    }
    QScrollBar::handle:horizontal {
        background: rgba(160, 160, 160, 180);
        border-radius: 4px;
        min-width: 20px;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0px;
        background: transparent;
    }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
        background: transparent;
    }

    /* 工具提示 */
    QToolTip {
        background: #2C3E50;
        color: white;
        border: none;
        border-radius: 4px;
        padding: 4px;
    }
    """
