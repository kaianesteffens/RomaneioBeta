"""Helpers e tipos compartilhados do modulo de rastreamento."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ResultadoRastreio:
    """Resultado do rastreamento de uma NF-e."""
    numero_nfe: str
    transportadora: str
    entregue: bool = False
    previsao_entrega: str = ""
    link_rastreio: str = ""
    screenshot_path: str = ""
    status_texto: str = ""
    erro: str = ""


def _download_dir() -> Path:
    """Diretório para salvar screenshots de rastreamento."""
    cfg_dir = (os.getenv("FRETEBOT_RASTREIO_DIR") or "").strip()
    if cfg_dir:
        d = Path(cfg_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d
    appdata = os.getenv("APPDATA")
    if appdata:
        d = Path(appdata) / "Fretio" / "rastreamento"
    else:
        d = Path.cwd() / "Fretio_rastreamento"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _gerar_path_screenshot(numero_nfe: str, transportadora: str = "") -> Path:
    safe_nfe = re.sub(r"[^\d]", "", numero_nfe) or "sem_numero"
    filename = f"NF{safe_nfe}.png"
    return _download_dir() / filename
