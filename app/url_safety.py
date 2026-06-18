"""Validação de URLs de endpoints de API configuráveis.

CONFIG.toml é gravável pelo usuário; sem validar o esquema, uma URL http://
injetada faria a telemetria/licença trafegar em claro e burlaria o SSLContext
(CWE-918/CWE-319). Aceita apenas https, ou http para localhost (dev).
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

_logger = logging.getLogger(__name__)

_DEV_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_safe_api_url(url: str) -> bool:
    """True se a URL é https (qualquer host) ou http apenas para localhost."""
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False
    if parsed.scheme == "https" and parsed.netloc:
        return True
    if parsed.scheme == "http" and (parsed.hostname or "") in _DEV_HTTP_HOSTS:
        return True
    return False


def require_https_url(url: str, default: str) -> str:
    """Retorna *url* se for segura; caso contrário loga e retorna *default*."""
    if is_safe_api_url(url):
        return url
    if str(url or "").strip():
        _logger.warning("URL de API rejeitada (esquema/host inseguro): %r — usando default", url)
    return default
