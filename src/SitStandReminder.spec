# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller build spec for Sit / Stand Reminder.
#
# Everything below is resolved relative to this file, so the project can be
# built from any location:
#
#     cd src
#     pyinstaller --noconfirm SitStandReminder.spec
#
# The result lands in ./dist/SitStandReminder/ (onedir layout - see README
# for why onedir is used instead of a single-file executable).

import os

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    [os.path.join(SPEC_DIR, 'sitstand.py')],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['pystray._win32'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'matplotlib', 'numpy', 'scipy', 'pandas', 'IPython', 'notebook', 'PIL.ImageQt'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SitStandReminder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(SPEC_DIR, 'app.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SitStandReminder',
)
