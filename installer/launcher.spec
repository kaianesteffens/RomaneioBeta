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

# Localiza app/ para empacotar update_security.py (verificação de assinatura do
# update). A chave pública Ed25519 já é embutida em update_security.py pelo passo
# "Embed update public key" do build-release.yml ANTES do build do launcher.
_app_src = ""
for _cand in (Path(SPECPATH).parent / "app", Path(SPECPATH).parent.parent / "app"):
    if (_cand / "update_security.py").exists():
        _app_src = str(_cand)
        break

a = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    pathex=[_app_src] if _app_src else [],
    binaries=[],
    datas=[],
    hiddenimports=[
        "tkinter", "tkinter.ttk", "tkinter.messagebox",
        "update_security",
        "cryptography",
        "cryptography.hazmat.primitives.asymmetric.ed25519",
        "cryptography.hazmat.primitives.serialization",
    ],
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
