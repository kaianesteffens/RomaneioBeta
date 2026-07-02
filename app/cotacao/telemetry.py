"""Detecção de suporte ao contrato de cotação (cotar(QuoteRequest))."""

from __future__ import annotations

from typing import Any
import inspect


def _provider_supports_quote_request_cotar(provider: Any) -> bool:
    provider_class_dict = getattr(type(provider), "__dict__", {})
    if "cotar" not in provider_class_dict:
        # Herdou somente o fallback de ProviderBase; manter fluxo legado.
        return False
    cotar_method = getattr(provider, "cotar", None)
    if not callable(cotar_method):
        return False
    try:
        signature = inspect.signature(cotar_method)
    except (TypeError, ValueError):
        return False

    params = list(signature.parameters.values())
    if not params:
        return False
    first = params[0]
    if first.name != "request":
        return False

    for param in params[1:]:
        if param.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        if param.default is inspect.Parameter.empty:
            return False
    return True


__all__ = ["_provider_supports_quote_request_cotar"]
