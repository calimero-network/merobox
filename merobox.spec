# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

# Collect all nacl dependencies (required for PyNaCl/CFFI)
nacl_datas, nacl_binaries, nacl_hiddenimports = collect_all('nacl')

a = Analysis(
    ['merobox/cli.py'],
    pathex=[],
    binaries=nacl_binaries,
    datas=nacl_datas,
    hiddenimports=[
        '_cffi_backend',
        'cffi',
        'nacl',
        'nacl.bindings',
        'nacl.bindings.crypto_aead',
    ] + nacl_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='merobox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
