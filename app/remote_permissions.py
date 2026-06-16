from __future__ import annotations

import logging
import unicodedata
from collections.abc import Iterable
from typing import Any

from remote_config import get_effective_remote_config, is_carrier_enabled, is_feature_allowed


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

FEATURE_MESSAGES = {
    "cotacao": "Este módulo foi desabilitado pela configuração da licença.",
    "rastreio": "Este módulo foi desabilitado pela configuração da licença.",
    "nfe": "Este módulo foi desabilitado pela configuração da licença.",
    "romaneio": "Este módulo foi desabilitado pela configuração da licença.",
}

CARRIER_DISABLED_MESSAGE = "Esta transportadora foi desabilitada pela configuração da licença."

_LOGGER = logging.getLogger("remote_permissions")


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
    feature_name = normalize_feature_name(feature)
    if not feature_name:
        return True
    try:
        return bool(is_feature_allowed(feature_name))
    except Exception as exc:
        _LOGGER.warning("Falha ao ler permissao remota da feature %s: %s", feature_name, exc)
        return True


def feature_message(feature: str) -> str:
    feature_name = normalize_feature_name(feature)
    return FEATURE_MESSAGES.get(
        feature_name,
        "Este módulo foi desabilitado pela configuração da licença.",
    )


def ensure_feature_allowed(feature: str, parent=None) -> bool:
    feature_name = normalize_feature_name(feature)
    if feature_allowed_or_default(feature_name):
        return True

    _LOGGER.info("Feature bloqueada por configuracao remota: %s", feature_name)
    # A UI (web) exibe a mensagem de feature_message(); este helper apenas decide.
    return False


def carrier_enabled_or_message(carrier: Any) -> tuple[bool, str]:
    carrier_name = normalize_carrier_name(carrier)
    if not carrier_name:
        return True, ""
    try:
        enabled = bool(is_carrier_enabled(carrier_name))
    except Exception as exc:
        _LOGGER.warning("Falha ao ler permissao remota da transportadora %s: %s", carrier_name, exc)
        return True, ""
    if enabled:
        return True, ""
    _LOGGER.info("Transportadora bloqueada por configuracao remota: %s", carrier_name)
    return False, CARRIER_DISABLED_MESSAGE


def filter_enabled_carriers(carriers: Iterable[Any]) -> list[Any]:
    enabled: list[Any] = []
    for carrier in carriers:
        allowed, _message = carrier_enabled_or_message(carrier)
        if allowed:
            enabled.append(carrier)
    return enabled


def enabled_carriers_from_config() -> list[str]:
    config = get_effective_remote_config()
    carriers = config.get("carriers_enabled", {}) if isinstance(config, dict) else {}
    if not isinstance(carriers, dict):
        return list(KNOWN_CARRIERS)
    return [name for name in KNOWN_CARRIERS if bool(carriers.get(name, True))]


def disabled_carriers_from_config() -> list[str]:
    config = get_effective_remote_config()
    carriers = config.get("carriers_enabled", {}) if isinstance(config, dict) else {}
    if not isinstance(carriers, dict):
        return []
    return [name for name in KNOWN_CARRIERS if not bool(carriers.get(name, True))]
