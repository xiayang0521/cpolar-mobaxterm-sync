# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：生成单个 GUI/托盘 exe（无控制台窗口）。
# 本程序仅依赖 Python 标准库，无需第三方包，打包也很干净。
#
# 打包步骤：
#   1) pip install pyinstaller
#   2) pyinstaller build.spec
#   3) 产物在 dist/cpolar-mobaxterm-sync.exe

a = Analysis(
    ['src/main.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='cpolar-mobaxterm-sync',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # GUI 程序，不显示控制台黑窗口
    icon=None,
)
