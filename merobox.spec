# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

# Collect all nacl dependencies (required for PyNaCl/CFFI)
nacl_datas, nacl_binaries, nacl_hiddenimports = collect_all('nacl')

# rich loads Unicode data modules dynamically by version; include all so the
# frozen binary works regardless of runtime Unicode version (e.g. unicode17-0-0).
# List must match rich._unicode_data._versions in the installed rich package.
_rich_unicode_versions = (
    '4.1.0', '5.0.0', '5.1.0', '5.2.0', '6.0.0', '6.1.0', '6.2.0', '6.3.0',
    '7.0.0', '8.0.0', '9.0.0', '10.0.0', '11.0.0', '12.0.0', '12.1.0',
    '13.0.0', '14.0.0', '15.0.0', '15.1.0', '16.0.0', '17.0.0',
)
_rich_unicode_hidden = [
    'rich._unicode_data',
    'rich._unicode_data._versions',
] + [
    'rich._unicode_data.unicode' + v.replace('.', '-') for v in _rich_unicode_versions
]

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
    ] + _rich_unicode_hidden + nacl_hiddenimports,
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
