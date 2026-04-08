# -*- mode: python ; coding: utf-8 -*-
# PyInstaller --onedir spec for Medical Record Management Assistant
# Build: pyinstaller --clean build/lpa.spec

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect all data files from Flet packages (includes the native Flutter desktop binary).
_flet_datas = (
    collect_data_files('flet')
    + collect_data_files('flet_core')
    + collect_data_files('flet_desktop')
)

# Resolve flet_desktop at spec-evaluation time (handles venv vs user-site installs)
import flet_desktop as _fd
import os as _os
_flet_app_dir = _os.path.join(_os.path.dirname(_fd.__file__), "app", "flet")

a = Analysis(
    ['../main.py'],
    pathex=['..'],
    binaries=[],
    datas=[
        # Bundle the Flutter renderer + DLLs so the window renders correctly
        (_flet_app_dir, "flet/app/flet"),
        *_flet_datas,
    ],
    hiddenimports=[
        # SQLCipher
        'sqlcipher3',
        'sqlcipher3._sqlite3',
        # Cryptography
        'cryptography',
        'cryptography.hazmat.primitives.ciphers.aead',
        # Flet
        'flet',
        'flet_core',
        'flet_desktop',
        # llama-cpp (optional — only needed if model bundled)
        # 'llama_cpp',
        # platformdirs
        'platformdirs',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='lpa',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='lpa',
)
