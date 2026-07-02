"""Registro e normalização de transportadoras (sem dependência de servidor).

Antes lia permissões/feature-flags da configuração remota do servidor; sem o
servidor, tudo fica habilitado. A normalização de nomes de transportadora (com
aliases) é lógica pura e permanece — vários módulos dependem dela para casar o
nome do provider com a seção de configuração.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from typing import Any


KNOWN_CARRIERS = (
    "braspress",
    "trd",
    "agex",
    "eucatur",
    "rodonaves",
    "alfa",
    "coopex",
    "translovato",
)

CARRIER_DISABLED_MESSAGE = "Esta transportadora foi desabilitada pela configuração."


def _fold(value: Any) -> str:
    text = str(value or "").strip().casefold()
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(ascii_text.replace("-", " ").replace("_", " ").split())


def normalize_carrier_name(name: Any) -> str:
    folded = _fold(name)
    if not folded:
        return ""
    aliases = {
        "brasp": "braspress",
        "braspress": "braspress",
        "trd": "trd",
        "agex": "agex",
        "eucatur": "eucatur",
        "rte": "rodonaves",
        "rodonaves": "rodonaves",
        "alfa": "alfa",
        "alfa transportes": "alfa",
        "coopex": "coopex",
        "translovato": "translovato",
        "trans lovato": "translovato",
        "transportes translovato": "translovato",
    }
    if folded in aliases:
        return aliases[folded]
    for marker, canonical in aliases.items():
        if marker and marker in folded:
            return canonical
    return folded.replace(" ", "_")


def normalize_feature_name(feature: Any) -> str:
    folded = _fold(feature).replace(" ", "_")
    if folded.startswith("allow_"):
        folded = folded[6:]
    return folded


def feature_allowed_or_default(feature: str) -> bool:
    return True


def feature_message(feature: str) -> str:
    return "Este módulo foi desabilitado pela configuração."


def ensure_feature_allowed(feature: str, parent=None) -> bool:
    return True


def carrier_enabled_or_message(carrier: Any) -> tuple[bool, str]:
    return True, ""


def filter_enabled_carriers(carriers: Iterable[Any]) -> list[Any]:
    return list(carriers)


def enabled_carriers_from_config() -> list[str]:
    return list(KNOWN_CARRIERS)


def disabled_carriers_from_config() -> list[str]:
    return []
