# -*- mode: python ; coding: utf-8 -*-
"""
Fretio Launcher — PyInstaller spec (one-file, sem versão no nome)
Entrada : installer/launcher.py
Saída   : installer/dist/Romaneio.exe
"""

from pathlib import Path

_assets = Path(SPECPATH).parent.parent / "app" / "assets"
_icon = None
for _name in ("romaneio.ico", "fretio.ico"):
    if (_assets / _name).exists():
        _icon = str(_assets / _name)
        break

a = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.messagebox"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "numpy", "scipy", "pandas",
        "IPython", "jupyter", "pytest", "unittest",
        "webview", "clr", "PIL", "playwright", "pdfplumber",
        "httpx", "httpcore", "bs4",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Romaneio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)
