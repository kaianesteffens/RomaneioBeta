"""Cliente best-effort para jobs de cotação."""

from __future__ import annotations

from typing import Any
import re
import threading

from . import deps
from .common import (
    KNOWN_CARRIERS,
    MODO_FOCO_TRANSPORTADORA,
    ResultadoCotacao,
    _log_diag,
    normalize_carrier_name,
    provider_progress_from_resultado,
)
from .error_context import sanitize_context
from .romaneio_parser import _normalizar_romaneio_colado
from .telemetry import _usage_status_from_result, _value_cents_from_frete, _carrier_usage_defaults

def _safe_outbound_message(value: Any) -> str:
    # Mensagem por transportadora vai para /api/quotations/jobs. Remove tags
    # inline e passa pelo sanitizador forte para não carregar HTML/DOM cru do
    # portal, e-mail, CNPJ/CPF ou chave de NF-e (o sanitizador de contexto só
    # derruba blocos HTML completos, não tags soltas).
    sem_tags = re.sub(r"(?is)<[^>]+>", " ", str(value or ""))
    cleaned = sanitize_context(sem_tags)
    return cleaned if isinstance(cleaned, str) else ""


def _coerce_enabled_flag(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "sim", "yes", "on", "enabled", "habilitado"}:
            return True
        if normalized in {"0", "false", "nao", "não", "no", "off", "disabled", "desabilitado"}:
            return False
    return bool(value)


def _quotation_job_carrier_lists(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    transportadoras_cfg = config.get("transportadoras", {}) if isinstance(config, dict) else {}
    if not isinstance(transportadoras_cfg, dict):
        transportadoras_cfg = {}
    foco = str(MODO_FOCO_TRANSPORTADORA or "").strip().lower()
    enabled: list[str] = []
    disabled: list[str] = []

    for carrier in KNOWN_CARRIERS:
        canonical = normalize_carrier_name(carrier)
        section = transportadoras_cfg.get(canonical, {})
        if not isinstance(section, dict):
            section = {}
        configured = _coerce_enabled_flag(section.get("habilitado"), True)
        if foco:
            configured = canonical == foco
        try:
            remote_allowed, _message = deps.carrier_enabled_or_message(canonical)
        except Exception:
            remote_allowed = True
        if configured and remote_allowed:
            enabled.append(canonical)
        else:
            disabled.append(canonical)
    return enabled, disabled


def _count_non_empty_lines(text: str) -> int:
    try:
        normalized = _normalizar_romaneio_colado(text)
    except Exception:
        normalized = str(text or "")
    return sum(1 for line in normalized.splitlines() if line.strip())


def _quotation_job_start_payload(
    config: dict[str, Any],
    *,
    modo: str,
    quantidade_pedidos: int | None = None,
    quantidade_linhas: int | None = None,
) -> dict[str, Any]:
    enabled, disabled = _quotation_job_carrier_lists(config)
    payload: dict[str, Any] = {
        "modo": modo,
        "transportadoras_habilitadas": enabled,
        "transportadoras_desabilitadas": disabled,
    }
    if quantidade_pedidos is not None:
        payload["quantidade_pedidos"] = max(0, int(quantidade_pedidos))
    if quantidade_linhas is not None:
        payload["quantidade_linhas"] = max(0, int(quantidade_linhas))
    return payload


# Tempo máximo (s) que o início da cotação espera pela criação remota do job.
# A chamada HTTP é best-effort, mas é síncrona e o timeout de socket não cobre
# resolução de DNS — em redes lentas/instáveis ela pode travar por minutos e
# atrasar o início das cotações. Limitamos a espera e seguimos sem job_id quando
# estourar (a criação continua em background, apenas sem rastreamento de status).
_JOB_CREATE_WAIT_S = 4.0


def _create_quotation_job_best_effort(source_type: str, payload: dict[str, Any]) -> Any:
    holder: dict[str, Any] = {}

    def _criar() -> None:
        try:
            holder["result"] = deps.create_quotation_job(source_type, payload=payload, wait=True)
        except Exception as exc:  # noqa: BLE001 - best-effort, nunca propaga
            holder["error"] = exc

    thread = threading.Thread(target=_criar, name="FretioQuotationJobCreate", daemon=True)
    thread.start()
    thread.join(_JOB_CREATE_WAIT_S)

    if thread.is_alive():
        _log_diag(
            f"Criação do job de cotação excedeu {_JOB_CREATE_WAIT_S:.0f}s; "
            "iniciando cotação sem aguardar (job seguirá em background)"
        )
        return None

    if "error" in holder:
        _log_diag(f"Falha ao criar job de cotação; cotação local continuará: {holder['error']}")
        return None

    result = holder.get("result")
    job_id = result.get("job_id") if isinstance(result, dict) else None
    if not job_id:
        status_code = result.get("status_code") if isinstance(result, dict) else None
        if status_code:
            _log_diag(f"Job de cotação não criado (HTTP {status_code})")
        else:
            _log_diag("Job de cotação não criado; cotação local continuará")
    return job_id


def _quotation_job_provider_status(status: Any) -> str:
    normalized = _usage_status_from_result(status)
    if normalized == "ok":
        return "ok"
    if normalized == "desabilitada":
        return "disabled"
    return "error"


def _quotation_job_result_payload(
    config: dict[str, Any],
    resultados: list[ResultadoCotacao] | None,
) -> dict[str, Any]:
    carrier_results = _carrier_usage_defaults(config)
    prazo_por_provider: dict[str, int | None] = {provider: None for provider in carrier_results}

    for result in resultados or []:
        canonical = normalize_carrier_name(getattr(result, "transportadora", ""))
        if canonical not in carrier_results:
            continue
        progress = provider_progress_from_resultado(result)
        carrier_results[canonical] = {
            "status": _usage_status_from_result(getattr(result, "status", "")),
            "duration_ms": getattr(result, "duration_ms", None),
            "value_cents": _value_cents_from_frete(getattr(result, "valor_frete", None)),
            "progress_status": progress.status,
            "stage": progress.stage,
            "message": _safe_outbound_message(progress.mensagem),
            "error_code": getattr(result, "error_code", None),
        }
        try:
            prazo = getattr(result, "prazo_dias", None)
            prazo_por_provider[canonical] = int(prazo) if prazo is not None else None
        except Exception:
            prazo_por_provider[canonical] = None

    transportadoras: list[dict[str, Any]] = []
    success_count = 0
    error_count = 0
    disabled_count = 0

    for provider, data in carrier_results.items():
        status = _quotation_job_provider_status(data.get("status"))
        if status == "ok":
            success_count += 1
        elif status == "disabled":
            disabled_count += 1
        else:
            error_count += 1

        item: dict[str, Any] = {
            "provider": provider,
            "status": status,
        }
        if data.get("value_cents") is not None:
            item["value_cents"] = data.get("value_cents")
        if data.get("duration_ms") is not None:
            item["duration_ms"] = data.get("duration_ms")
        if data.get("progress_status"):
            item["progress_status"] = data.get("progress_status")
        if data.get("stage"):
            item["stage"] = data.get("stage")
        if data.get("message"):
            item["message"] = data.get("message")
        if data.get("error_code"):
            item["error_code"] = data.get("error_code")
        if prazo_por_provider.get(provider) is not None:
            item["prazo_dias"] = prazo_por_provider[provider]
        transportadoras.append(item)

    summary = {
        "status": "ok" if success_count > 0 else "error",
        "total_providers": len(transportadoras),
        "success_count": success_count,
        "error_count": error_count,
        "disabled_count": disabled_count,
    }

    return {
        "summary": summary,
        "total_providers": summary["total_providers"],
        "success_count": summary["success_count"],
        "error_count": summary["error_count"],
        "disabled_count": summary["disabled_count"],
        "transportadoras": transportadoras,
    }


def _quotation_job_has_success(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    try:
        return int(result.get("success_count") or result.get("summary", {}).get("success_count") or 0) > 0
    except Exception:
        return False


def _quotation_job_final_status(
    result: dict[str, Any] | None,
    *,
    cancelled: bool = False,
    general_error: bool = False,
) -> str:
    if cancelled:
        return "cancelled"
    if _quotation_job_has_success(result):
        return "finished"
    return "error"


def _mark_quotation_job_running_best_effort(job_id: Any) -> None:
    _finish_quotation_job_best_effort(
        job_id,
        status="running",
        result=None,
    )


def _quotation_results_indicate_general_error(resultados: list[ResultadoCotacao] | None) -> bool:
    if resultados is None:
        return True
    if not resultados:
        return False
    provider_results = [
        r for r in resultados
        if normalize_carrier_name(getattr(r, "transportadora", "")) in set(KNOWN_CARRIERS)
    ]
    if provider_results:
        return False
    return any(str(getattr(r, "status", "") or "").startswith("erro") for r in resultados)


def _quotation_job_error_message(resultados: list[ResultadoCotacao] | None) -> str:
    for result in resultados or []:
        if normalize_carrier_name(getattr(result, "transportadora", "")) in set(KNOWN_CARRIERS):
            continue
        detalhe = str(getattr(result, "detalhes", "") or "").strip()
        if detalhe:
            return _safe_outbound_message(re.sub(r"\s+", " ", detalhe))[:240]
    return ""


def _finish_quotation_job_best_effort(
    job_id: Any,
    *,
    status: str,
    result: dict[str, Any] | None,
    error_message: str | None = None,
) -> None:
    if not job_id:
        return
    try:
        update_result = deps.update_quotation_job_result(
            job_id,
            status,
            result=result,
            error_message=error_message,
            wait=False,
        )
        if isinstance(update_result, dict) and not update_result.get("queued") and not update_result.get("updated"):
            _log_diag("Atualização do job de cotação não foi enfileirada")
    except Exception as exc:
        _log_diag(f"Falha ao atualizar job de cotação; app seguirá normalmente: {exc}")



__all__ = [name for name in globals() if not name.startswith("__")]
