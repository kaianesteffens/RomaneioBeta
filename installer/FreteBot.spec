# -*- mode: python ; coding: utf-8 -*-
"""
FreteBot — PyInstaller spec (one-folder mode)
Gera: dist/FreteBot/ com FreteBot.exe + dependências
"""

from pathlib import Path
import os

project_root = Path(SPECPATH).parent.parent / "app"  # Diretório do app com romaneio_app.py

# ── Hidden imports (módulos que PyInstaller não detecta automaticamente) ───
hiddenimports = [
    # Providers
    "fretebot.providers.braspress_playwright",
    "fretebot.providers.trd",
    "fretebot.providers.agex",
    "fretebot.providers.eucatur",
    "fretebot.providers.rodonaves",
    "fretebot.providers.alfa",
    "fretebot.providers.coopex",
    "fretebot.providers._win_taskbar",
    "fretebot.providers.base",
    # Fretebot core
    "fretebot.models",
    "fretebot.logging_conf",
    "fretebot.config",
    "fretebot.cache",
    "fretebot.calc",
    # Dependências externas
    "bs4",
    "httpx",
    "httpx._transports",
    "httpx._transports.default",
    "httpcore",
    "pdfplumber",
    "toml",
    "tomli",
    "playwright",
    "playwright.async_api",
    "playwright.sync_api",
    "PySide6",
    "PIL",
    # Módulos locais na raiz
    "extrator_pedidos",
    "cotacao_transportadoras",
    "updater",
    "license",
    "error_reporter",
    "ui_components",
]

# ── Data files ────────────────────────────────────────────────────────────
datas = []

# CONFIG.example.toml (template para o usuário)
config_example = project_root / "fretebot" / "CONFIG.example.toml"
if config_example.exists():
    datas.append((str(config_example), "fretebot"))

config_root = project_root / "CONFIG.example.toml"
if config_root.exists():
    datas.append((str(config_root), "."))

# CONFIG.toml real (com credenciais) para o instalador copiar
config_real = project_root / "CONFIG.toml"
if config_real.exists():
    datas.append((str(config_real), "."))

# Romaneio exemplo
romaneio_ex = project_root / "romaneio_exemplo.csv"
if romaneio_ex.exists():
    datas.append((str(romaneio_ex), "."))

# Versão exibida na interface (Romaneio Beta X.Y)
version_file = project_root / "version.txt"
if version_file.exists():
    datas.append((str(version_file), "."))

# Assets da interface (ícones)
assets_dir = project_root / "assets"
for asset_name in ("romaneio.ico", "fretebot.ico"):
    asset_path = assets_dir / asset_name
    if asset_path.exists():
        datas.append((str(asset_path), "assets"))

# Logos das transportadoras
logos_dir = assets_dir / "logos"
if logos_dir.is_dir():
    for logo_file in logos_dir.iterdir():
        if logo_file.is_file():
            datas.append((str(logo_file), "assets/logos"))

# Fontes locais da UI
fonts_dir = assets_dir / "fonts"
if fonts_dir.is_dir():
    for font_file in fonts_dir.iterdir():
        if font_file.is_file():
            datas.append((str(font_file), "assets/fonts"))

# ── Analysis ──────────────────────────────────────────────────────────────
a = Analysis(
    [str(project_root / "romaneio_app.py")],
    pathex=[
        str(project_root),
        str(project_root / "fretebot" / "src"),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "IPython",
        "jupyter",
        "pytest",
        "unittest",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# ── EXE (one-folder) ─────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # one-folder mode
    name="FreteBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # GUI app, sem console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "assets" / "romaneio.ico")
        if (project_root / "assets" / "romaneio.ico").exists()
        else (str(project_root / "assets" / "fretebot.ico")
              if (project_root / "assets" / "fretebot.ico").exists() else None),
)

# ── COLLECT (junta tudo numa pasta) ───────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FreteBot",
)
