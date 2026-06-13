"""Telemetria, error reporting e metadados de cotação."""

from __future__ import annotations

from typing import Any
import inspect

from . import deps
from .common import (
    KNOWN_CARRIERS,
    ResultadoCotacao,
    normalize_carrier_name,
)

def _quotation_usage_metadata(
    dados: dict[str, Any] | None,
    *,
    modo: str,
    quantidade_transportadoras: int | None = None,
    job_id: Any = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"modo": modo}
    if job_id:
        metadata["job_id"] = job_id
    if quantidade_transportadoras is not None:
        metadata["quantidade_transportadoras"] = int(quantidade_transportadoras)
    if not isinstance(dados, dict):
        return metadata

    uf_destino = str(dados.get("uf_destino", "") or "").strip().upper()
    if len(uf_destino) == 2 and uf_destino.isalpha():
        metadata["uf_destino"] = uf_destino
    try:
        volumes = int(dados.get("volumes", 0) or 0)
        if volumes >= 0:
            metadata["volumes"] = volumes
    except Exception:
        pass
    try:
        peso = float(dados.get("peso", 0.0) or 0.0)
        if peso >= 0:
            metadata["peso_total_kg"] = round(peso, 3)
    except Exception:
        pass
    return metadata


def _usage_status_from_result(status: Any) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "ok":
        return "ok"
    if normalized == "desabilitada":
        return "desabilitada"
    if normalized in {"nao_atendido", "não_atendido", "sem_cotacao", "sem_cotação"}:
        return "sem_cotacao"
    return "erro"


def _value_cents_from_frete(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(round(float(value) * 100))
    except Exception:
        return None


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


def _carrier_usage_defaults(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    transportadoras_cfg = config.get("transportadoras", {}) if isinstance(config, dict) else {}
    if not isinstance(transportadoras_cfg, dict):
        transportadoras_cfg = {}

    defaults: dict[str, dict[str, Any]] = {}
    for carrier in KNOWN_CARRIERS:
        canonical = normalize_carrier_name(carrier)
        section = transportadoras_cfg.get(canonical, {})
        status = "sem_cotacao"
        if isinstance(section, dict) and bool(section.get("habilitado", True)) is False:
            status = "desabilitada"
        defaults[canonical] = {
            "status": status,
            "duration_ms": None,
            "value_cents": None,
        }
    return defaults


def _report_quotation_usage_results(
    *,
    config: dict[str, Any],
    dados: dict[str, Any] | None,
    resultados: list[ResultadoCotacao] | None,
    modo: str,
    duration_ms: int | None,
    job_id: Any = None,
) -> None:
    try:
        results = resultados or []
        carrier_results = _carrier_usage_defaults(config)
        for result in results:
            canonical = normalize_carrier_name(getattr(result, "transportadora", ""))
            if canonical not in carrier_results:
                continue
            carrier_results[canonical] = {
                "status": _usage_status_from_result(getattr(result, "status", "")),
                "duration_ms": getattr(result, "duration_ms", None),
                "value_cents": _value_cents_from_frete(getattr(result, "valor_frete", None)),
            }

        metadata = _quotation_usage_metadata(
            dados,
            modo=modo,
            quantidade_transportadoras=len(carrier_results),
            job_id=job_id,
        )
        finished_status = "ok" if any(getattr(r, "status", "") == "ok" for r in results) else "error"
        deps.report_quotation_finished(finished_status, duration_ms=duration_ms, metadata=metadata)
        for provider, payload in carrier_results.items():
            deps.report_carrier_quotation_result(
                provider,
                payload["status"],
                duration_ms=payload["duration_ms"],
                value_cents=payload["value_cents"],
                metadata=metadata,
            )
    except Exception:
        pass



__all__ = [name for name in globals() if not name.startswith("__")]
