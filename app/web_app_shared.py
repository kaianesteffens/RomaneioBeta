"""Fretio — helpers/constantes compartilhados entre web_app.py e seus mixins.

Extraído de web_app.py para quebrar o ciclo de import: os mixins de domínio
(web_app_config, web_app_startup, …) importam daqui, nunca de web_app.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import webview

import company_config as cc
from web_presenters import (
    chave_nota,
    montar_romaneio_fornecedor,
    validar_local_entrega,
)

try:
    import tomllib as _toml_reader  # py311+
except ModuleNotFoundError:  # pragma: no cover
    _toml_reader = None
    import toml as _toml_fallback


_APP_DIR = Path(__file__).resolve().parent


# ── Helpers de config/versão (reusam o backend real) ───────────────────────
def _load_config(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        if _toml_reader is not None:
            return _toml_reader.loads(raw)
        return _toml_fallback.loads(raw)
    except Exception:
        return {}


class _ConfigUnsafeToWrite(Exception):
    """Config existente que nao pode ser lida/parseada com seguranca.

    Usada para abortar um ciclo read-modify-write antes de sobrescrever o
    CONFIG.toml com um dict vazio (o que apagaria credenciais e a secao
    [fretio])."""


def _load_config_for_write(path: Path) -> dict[str, Any]:
    """Carrega config para um ciclo read-modify-write.

    Diferente de _load_config, distingue 'arquivo inexistente' (novo, ok) de
    'arquivo existe mas ilegivel/corrompido'. No segundo caso levanta
    _ConfigUnsafeToWrite, evitando que um Salvar sobrescreva e apague as demais
    secoes (credenciais das transportadoras, [fretio]) quando a leitura falha."""
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _ConfigUnsafeToWrite(str(exc)) from exc
    if not raw.strip():
        return {}
    try:
        if _toml_reader is not None:
            return _toml_reader.loads(raw)
        return _toml_fallback.loads(raw)
    except Exception as exc:
        raise _ConfigUnsafeToWrite("CONFIG.toml ilegivel/corrompido") from exc


def _carregar_versao() -> str:
    candidates = [
        Path(getattr(sys, "_MEIPASS", "") or "") / "version.txt",
        _APP_DIR / "version.txt",
    ]
    for c in candidates:
        try:
            if c and c.exists():
                return c.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def _resolver_tema_efetivo(modo: str) -> str:
    modo = (modo or "sistema").lower()
    if modo == "claro":
        return "claro"
    if modo == "escuro":
        return "escuro"
    return "escuro"  # 'sistema' no POC assume a identidade escura padrão


# Campos de credenciais por transportadora — derivados do registro único
# (ProviderSpec.credential_fields em factory.py), preservando ordem e rótulos.
from fretio.providers.factory import _PROVIDER_SPECS

_CARRIER_FIELDS: dict[str, list[tuple[str, str, str]]] = {
    key: list(spec.credential_fields) for key, spec in _PROVIDER_SPECS.items()
}
