# -*- mode: python ; coding: utf-8 -*-

import os
import sys
import subprocess
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

APP_NAME = 'Music-spd-tool_macos-arm64'
ICON_PATH = 'icon.icns'   # 如果不存在，请注释或移除

# ----- 自动查找 VLC 库和插件目录 -----
def find_vlc():
    """返回 (lib_dir, plugins_dir)"""
    candidates = []

    # 1. 环境变量
    env_prefix = os.environ.get('VLC_PREFIX')
    if env_prefix and os.path.exists(env_prefix):
        candidates.append(env_prefix)

    # 2. 常见安装位置（根据实际目录结构）
    # 注意：VLC.app 安装后库在 Contents/MacOS/lib，插件在 Contents/MacOS/plugins
    candidates.extend([
        '/Applications/VLC.app/Contents/MacOS',
        '/Applications/VLC.app/Contents/MacOS/lib',
        '/Applications/VLC.app/Contents/Frameworks',
        '/usr/local/opt/vlc',
        '/opt/homebrew/opt/vlc',
    ])

    # 3. 通过 brew --prefix vlc 获取（如果可用）
    try:
        output = subprocess.check_output(['brew', '--prefix', 'vlc'], text=True).strip()
        if output and os.path.exists(output):
            candidates.append(output)
    except Exception:
        pass

    for base in candidates:
        if not os.path.exists(base):
            continue

        # 尝试组合 lib_dir 和 plugins_dir
        # 如果 base 直接是 MacOS 目录，则 lib 在 MacOS/lib，plugins 在 MacOS/plugins
        if base.endswith('MacOS'):
            lib_dir = os.path.join(base, 'lib')
            plugins_dir = os.path.join(base, 'plugins')
        elif base.endswith('MacOS/lib'):
            lib_dir = base
            plugins_dir = os.path.join(os.path.dirname(base), 'plugins')
        else:
            # 对于其他路径，尝试 lib 子目录
            lib_dir = os.path.join(base, 'lib')
            plugins_dir = os.path.join(base, 'plugins')
            if not os.path.exists(lib_dir) or not os.path.exists(plugins_dir):
                # 如果 base 下没有 lib 和 plugins，尝试 base/Contents/MacOS
                macos_dir = os.path.join(base, 'Contents', 'MacOS')
                if os.path.exists(macos_dir):
                    lib_dir = os.path.join(macos_dir, 'lib')
                    plugins_dir = os.path.join(macos_dir, 'plugins')

        # 验证 libvlc.dylib 是否存在
        if not os.path.exists(lib_dir) or not os.path.exists(plugins_dir):
            continue

        libvlc = os.path.join(lib_dir, 'libvlc.dylib')
        libvlccore = os.path.join(lib_dir, 'libvlccore.dylib')
        if os.path.exists(libvlc) and os.path.exists(libvlccore):
            return lib_dir, plugins_dir

    raise RuntimeError('Cannot find VLC library (libvlc.dylib) and plugins directory. Please install VLC.')

VLC_LIB_DIR, VLC_PLUGINS_DIR = find_vlc()
print(f'Using VLC libs from: {VLC_LIB_DIR}')
print(f'Using VLC plugins from: {VLC_PLUGINS_DIR}')

# 确认文件存在
libvlc = os.path.join(VLC_LIB_DIR, 'libvlc.dylib')
libvlccore = os.path.join(VLC_LIB_DIR, 'libvlccore.dylib')
if not os.path.exists(libvlc) or not os.path.exists(libvlccore):
    raise RuntimeError(f'libvlc.dylib or libvlccore.dylib not found in {VLC_LIB_DIR}')
if not os.path.isdir(VLC_PLUGINS_DIR):
    raise RuntimeError(f'Plugins directory not found: {VLC_PLUGINS_DIR}')

# ----- PyInstaller 分析 -----
a = Analysis(
    ['musicdlgui.py'],
    pathex=[],
    binaries=[],
    datas=[
        (libvlc, 'vlc'),
        (libvlccore, 'vlc'),
        (VLC_PLUGINS_DIR, 'vlc/plugins'),
    ],
    hiddenimports=[
        'matplotlib.backends.backend_qt5agg',
        'numba',
        'sklearn.utils._cython_blas',
        'scipy.special._cdflib',
        'scipy.linalg.cython_blas',
        'scipy.linalg.cython_lapack',
        'sounddevice',
        'librosa',
        'numpy',
        'mutagen',
        'requests',
        'filetype',
        'musicdl',
        'vlc',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=APP_NAME + '.app',
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
    bundle_identifier='com.yourcompany.musicdlgui',
    info_plist={
        'CFBundleShortVersionString': '4.0.0',
        'CFBundleVersion': '4.0.0',
        'CFBundleName': 'Music Downloader',
        'CFBundleDisplayName': 'Music Downloader',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.13',
    },
)