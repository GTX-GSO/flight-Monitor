# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：打包图形界面入口 gui.py。"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas = [
    ("config.example.yaml", "."),
    ("data/city_codes.json", "data"),
]
binaries = []
hiddenimports = [
    "pystray._win32",
    "PIL._tkinter_finder",
    "apscheduler.schedulers.background",
    "apscheduler.triggers.interval",
    "playwright",
    "playwright.sync_api",
    "yaml",
    "httpx",
]
# playwright / greenlet 等
for pkg in ("playwright", "greenlet"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        hiddenimports += collect_submodules(pkg)

a = Analysis(
    ["gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name="JiPiaoMonitor",
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="JiPiaoMonitor",
)
