# -*- mode: python ; coding: utf-8 -*-

import os
import subprocess

APP_NAME = 'cYy-Music-Client_macos-arm64'   # 最终 .app 名称
EXE_NAME = APP_NAME + '_bin'
ICON_PATH = 'icon.icns'                     # 请确保此文件存在

# ----- 查找 ffmpeg (macOS 通过 Homebrew) -----
def find_ffmpeg():
    try:
        prefix = subprocess.check_output(['brew', '--prefix', 'ffmpeg'], text=True).strip()
        if prefix:
            return prefix
    except:
        pass
    for p in ['/usr/local/opt/ffmpeg', '/opt/homebrew/opt/ffmpeg']:
        if os.path.exists(p):
            return p
    raise RuntimeError('ffmpeg not found.')

FFMPEG_PREFIX = find_ffmpeg()
ffmpeg_bin = os.path.join(FFMPEG_PREFIX, 'bin')
ffmpeg_lib = os.path.join(FFMPEG_PREFIX, 'lib')
if not os.path.exists(os.path.join(ffmpeg_bin, 'ffmpeg')):
    raise RuntimeError('ffmpeg executable not found.')

# ----- 构建 datas（不包含 VLC，只包含 FFmpeg 和 PortAudio）-----
datas = [
    (ffmpeg_bin, 'ffmpeg/bin'),
    (ffmpeg_lib, 'ffmpeg/lib'),
]

# 查找 portaudio (sounddevice 依赖)
PORT_AUDIO_LIB = None
for p in ['/usr/local/lib/libportaudio.dylib', '/opt/homebrew/lib/libportaudio.dylib']:
    if os.path.exists(p):
        PORT_AUDIO_LIB = p
        break
if PORT_AUDIO_LIB:
    datas.append((PORT_AUDIO_LIB, 'ffmpeg/lib'))
else:
    print("Warning: libportaudio.dylib not found, sounddevice may fail.")

# 添加图标（若有）
if os.path.exists(ICON_PATH):
    datas.append((ICON_PATH, '.'))

# ----- 分析 -----
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
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
        'vlc',            # 保留，因为代码中会导入，但运行时依赖系统 VLC
        'PyQt5.sip',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 排除不需要的 PyQt5 模块，显著减小体积
        'PyQt5.QtWebEngine',
        'PyQt5.QtWebEngineWidgets',
        'PyQt5.QtMultimedia',
        'PyQt5.QtMultimediaWidgets',
        'PyQt5.QtSensors',
        'PyQt5.QtOpenGL',
        'PyQt5.QtSql',
        'PyQt5.QtNetwork',
        'PyQt5.QtPositioning',
        'PyQt5.QtPrintSupport',
        'PyQt5.QtQml',
        'PyQt5.QtQuick',
        'PyQt5.QtRemoteObjects',
        'PyQt5.QtScxml',
        'PyQt5.QtScript',
        'PyQt5.QtScriptTools',
        'PyQt5.QtTextToSpeech',
        'PyQt5.QtWebChannel',
        'PyQt5.QtWebSockets',
        'PyQt5.QtXmlPatterns',
        'PyQt5.Qt3DAnimation',
        'PyQt5.Qt3DCore',
        'PyQt5.Qt3DExtras',
        'PyQt5.Qt3DInput',
        'PyQt5.Qt3DLogic',
        'PyQt5.Qt3DRender',
        'PyQt5.QtGamepad',
        'PyQt5.QtVirtualKeyboard',
        'PyQt5.QtChart',
        'PyQt5.QtDataVisualization',
        'PyQt5.QtPurchasing',
        'PyQt5.QtBluetooth',
        'PyQt5.QtNfc',
    ],
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
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=APP_NAME + '.app',
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
    bundle_identifier='com.yourcompany.musicdlgui',
    info_plist={
        'CFBundleShortVersionString': '4.2.0',
        'CFBundleVersion': '4.2.0',
        'CFBundleName': 'cYy Music Client',
        'CFBundleDisplayName': 'cYy Music Client',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.13',
    },
)