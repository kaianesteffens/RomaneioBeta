# -*- mode: python ; coding: utf-8 -*-
"""
Fretio Launcher — PyInstaller spec (one-file, sem versão no nome)
Entrada : installer/launcher.py
Saída   : installer/dist/Romaneio.exe
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

_app_dir = Path(SPECPATH).parent.parent / "app"
_assets = _app_dir / "assets"
_icon = None
for _name in ("romaneio.ico", "fretio.ico"):
    if (_assets / _name).exists():
        _icon = str(_assets / _name)
        break

# O launcher verifica a assinatura Ed25519 do update reusando app/update_security.py,
# que depende de cryptography. Ambos precisam ir no bundle do Romaneio.exe para que a
# verificação (fail-closed) funcione fora do processo principal do app.
hiddenimports = [
    "tkinter", "tkinter.ttk", "tkinter.messagebox",
    "update_security",
    "cryptography",
    "cryptography.hazmat.primitives.serialization",
    "cryptography.hazmat.primitives.asymmetric.ed25519",
]
hiddenimports += collect_submodules("cryptography")
binaries = collect_dynamic_libs("cryptography")

a = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    pathex=[str(_app_dir)],
    binaries=binaries,
    datas=[],
    hiddenimports=hiddenimports,
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
    upx=False,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)
