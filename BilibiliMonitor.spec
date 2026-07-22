# -*- mode: python ; coding: utf-8 -*-
import os
import shiboken6


SHIBOKEN6_DIR = os.path.dirname(shiboken6.__file__)
ICON_PATH = os.path.join('data', 'icon.ico')


a = Analysis(
    ['gui_main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('ffmpeg', 'ffmpeg'),
        ('tools/aria2', 'tools/aria2'),
        ('config.yaml', '.'),
        ('src', 'src'),
        ('data/icon.png', 'data'),
        (SHIBOKEN6_DIR, 'shiboken6'),
    ],
    hiddenimports=[
        'src',
        'src.gui',
        'src.database',
        'src.monitor',
        'src.downloader',
        'src.web_client',
        'src.video_stream',
        'src.wbi',
        'src.ctfile_uploader',
        'src.upload_manager',
        'src.progress',
        'src.gui.status_tab',
        'src.config_loader',
        'src.logger',
        'src.gui.log_handler',
        'src.gui.filename_template_builder',
        'src.gui.qr_login_dialog',
        'src.gui.user_center_dialog',
        'qrcode',
        'qrcode.image.pil',
        'PIL',
        'PIL.ImageQt',
        'shiboken6',
        'shiboken6.Shiboken',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt6', 'PyQt5', 'PySide2'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='bilibili-monitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='bilibili-monitor',
)
