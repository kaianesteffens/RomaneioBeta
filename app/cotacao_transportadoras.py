"""Cotação de transportadoras para integração com romaneio."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
import asyncio
from datetime import datetime
import logging
import os
import time
import re
import sys
import threading

# Adiciona a pasta 'src' ao sys.path para encontrar os módulos do Fretio
def _add_fretio_src_to_path() -> None:
    repo_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    src = repo_root / "fretio" / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_fretio_src_to_path()

# Error reporting remoto
try:
    from error_reporter import report_error, report_error_message
except Exception:
    def report_error(*a, **kw): pass
    def report_error_message(*a, **kw): pass

try:
    from usage_reporter import (
        report_carrier_quotation_result,
        report_quotation_finished,
        report_quotation_started,
    )
except Exception:
    def report_carrier_quotation_result(*a, **kw): return {"sent": False}
    def report_quotation_finished(*a, **kw): return {"sent": False}
    def report_quotation_started(*a, **kw): return {"sent": False}

try:
    from quotation_jobs_client import create_quotation_job, update_quotation_job_result
except Exception:
    def create_quotation_job(*a, **kw): return {"created": False, "job_id": None}
    def update_quotation_job_result(*a, **kw): return {"updated": False}

try:
    from quotation_normalization_shadow import run_shadow_normalization
except Exception:
    def run_shadow_normalization(*a, **kw): return None

try:
    from remote_permissions import (
        CARRIER_DISABLED_MESSAGE,
        KNOWN_CARRIERS,
        carrier_enabled_or_message,
        normalize_carrier_name,
    )
except Exception:
    CARRIER_DISABLED_MESSAGE = "Transportadora desabilitada pela configuração remota."
    KNOWN_CARRIERS = (
        "braspress",
        "bauer",
        "trd",
        "agex",
        "eucatur",
        "rodonaves",
        "alfa",
        "coopex",
    )

    def carrier_enabled_or_message(carrier):
        return True, ""

    def normalize_carrier_name(carrier):
        return str(carrier or "").strip().lower()

# Inicializar logging dos providers
try:
    from fretio.logging_conf import get_logger, setup_logging
    setup_logging()
    _logger = get_logger(__name__)
except Exception:
    _logger = logging.getLogger(__name__)

try:
    import tomllib  # py311+
except Exception:  # pragma: no cover
    tomllib = None


from fretio.config_manager import ConfigManager
from fretio.providers.factory import ProviderFactory


CEP_ORIGEM_PADRAO = "99740000"
MODO_FOCO_TRANSPORTADORA = ""  # Vazio = sem foco; cota todas as transportadoras habilitadas.
_CONFIG_FALLBACK = """[fretio]
fator_cubagem = 6000
cache_dir = "cache"

[romaneio]
cep_origem = "99740000"

[transportadoras.braspress]
habilitado = true
cnpj = ""
senha = ""
ufs_atendidas = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]

[transportadoras.bauer]
habilitado = true
cotacao_url = ""
cnpj_pagador = ""
cnpj_remetente = ""
cnpj_destinatario = ""
headless = true
quantidade = 1
ufs_atendidas = ["PR", "RS", "SC"]

[transportadoras.trd]
habilitado = true
email = ""
senha = ""
headless = true
volumes = 1
altura = 0.1
largura = 0.1
comprimento = 0.1
ufs_atendidas = ["RS", "SC", "PR", "SP", "MG", "ES", "RJ"]

[transportadoras.agex]
habilitado = false
email = ""
senha = ""
cnpj_remetente = ""
cnpj_destinatario = ""
ufs_atendidas = ["PR", "SP", "GO", "DF", "TO", "PA", "MT", "MS"]

[transportadoras.eucatur]
habilitado = false
dominio = ""
usuario = ""
senha = ""
ufs_atendidas = ["RR", "AM", "AC", "RO", "MT", "MS"]

[transportadoras.rodonaves]
habilitado = false
dominio = "RTE"
usuario = ""
senha = ""
cnpj_pagador = ""
login_url = "https://cliente.rte.com.br/?showLogin=true"
cotacao_url = "https://sistema.rte.com.br/bin/ssw1608"
headless = true
ufs_atendidas = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]

[transportadoras.alfa]
habilitado = false
login = ""
senha = ""
cnpj_remetente = ""
login_url = "https://arearestrita.alfatransportes.com.br/login/"
cotacao_url = "https://arearestrita.alfatransportes.com.br/cotacao/api/"
headless = false
ufs_atendidas = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]

[transportadoras.coopex]
habilitado = false
dominio = ""
usuario = ""
senha = ""
ufs_atendidas = []
"""


@dataclass
class ResultadoCotacao:
    transportadora: str
    status: str
    valor_frete: float | None = None
    prazo_dias: int | None = None
    detalhes: str | None = None
    duration_ms: int | None = None


@lru_cache(maxsize=4096)
def _digits_cached(value: str) -> str:
    return re.sub(r"\D", "", value)


def _digits(value: Any) -> str:
    return _digits_cached(str(value or ""))


@lru_cache(maxsize=4096)
def _cep_cached(value: str) -> str:
    return _digits_cached(value)[:8]


def _cep(value: Any) -> str:
    return _cep_cached(str(value or ""))


# Mapeamento faixa de CEPs → UF (Correios)
_CEP_UF_FAIXAS: list[tuple[int, int, str]] = [
    (1000000, 19999999, "SP"),
    (20000000, 28999999, "RJ"),
    (29000000, 29999999, "ES"),
    (30000000, 39999999, "MG"),
    (40000000, 48999999, "BA"),
    (49000000, 49999999, "SE"),
    (50000000, 56999999, "PE"),
    (57000000, 57999999, "AL"),
    (58000000, 58999999, "PB"),
    (59000000, 59999999, "RN"),
    (60000000, 63999999, "CE"),
    (64000000, 64999999, "PI"),
    (65000000, 65999999, "MA"),
    (66000000, 68899999, "PA"),
    (68900000, 68999999, "AP"),
    (69000000, 69299999, "AM"),
    (69300000, 69399999, "RR"),
    (69400000, 69899999, "AM"),
    (69900000, 69999999, "AC"),
    (70000000, 72799999, "DF"),
    (72800000, 72999999, "GO"),
    (73000000, 73699999, "DF"),
    (73700000, 76799999, "GO"),
    (76800000, 76999999, "RO"),
    (77000000, 77999999, "TO"),
    (78000000, 78899999, "MT"),
    (78900000, 78999999, "MS"),
    (79000000, 79999999, "MS"),
    (80000000, 87999999, "PR"),
    (88000000, 89999999, "SC"),
    (90000000, 99999999, "RS"),
]


@lru_cache(maxsize=4096)
def _cep_para_uf_cached(cep_digits: str) -> str | None:
    """Retorna a UF correspondente a um CEP de 8 dígitos."""
    if len(cep_digits) != 8:
        return None
    try:
        cep_num = int(cep_digits)
    except ValueError:
        return None
    for inicio, fim, uf in _CEP_UF_FAIXAS:
        if inicio <= cep_num <= fim:
            return uf
    return None


def _cep_para_uf(cep: Any) -> str | None:
    return _cep_para_uf_cached(_cep(cep))


def _ufs_cache_key(ufs_config: list[str] | tuple[str, ...] | str | None) -> str | tuple[str, ...] | None:
    if ufs_config is None:
        return None
    if isinstance(ufs_config, str):
        return ufs_config
    return tuple(str(u or "") for u in ufs_config)


@lru_cache(maxsize=512)
def _normalizar_ufs_atendidas_cached(
    ufs_key: str | tuple[str, ...] | None,
) -> tuple[str, ...]:
    if not ufs_key:
        return ()
    if isinstance(ufs_key, str):
        values = ufs_key.split(",")
    else:
        values = ufs_key
    return tuple(str(u).strip().upper() for u in values if str(u).strip())


def _uf_atendida(ufs_config: list[str] | str | None, uf_destino: str | None) -> bool:
    """Verifica se a UF de destino está na lista de UFs atendidas."""
    if not ufs_config:
        return True  # sem filtro = atende tudo
    if not uf_destino:
        return True  # sem UF = tenta mesmo assim
    ufs_config = _normalizar_ufs_atendidas_cached(_ufs_cache_key(ufs_config))
    if not ufs_config:
        return True
    return uf_destino.upper() in ufs_config


@lru_cache(maxsize=512)
def _resolver_cep_origem_cached(
    cep_informado: str,
    cep_romaneio: str,
    transportadora_ceps: tuple[str, ...],
) -> str:
    if cep_informado:
        return cep_informado
    if cep_romaneio:
        return cep_romaneio
    for cep_sec in transportadora_ceps:
        if cep_sec:
            return cep_sec
    return CEP_ORIGEM_PADRAO


def _clear_validation_caches() -> None:
    _digits_cached.cache_clear()
    _cep_cached.cache_clear()
    _cep_para_uf_cached.cache_clear()
    _normalizar_ufs_atendidas_cached.cache_clear()
    _resolver_cep_origem_cached.cache_clear()


ConfigManager.register_cache_clearer(_clear_validation_caches)


def _base_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def _diag_log_enabled() -> bool:
    return not bool(getattr(sys, "frozen", False))


def _trd_headless_config_value(tcfg: dict[str, Any], foco_trd: bool) -> bool:
    """TRD sempre roda headless (invisível)."""
    return True


def _log_path() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        log_dir = Path(appdata) / "Fretio"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "romaneio_cotacao.log"
    return _base_dir() / "romaneio_cotacao.log"


def _log_diag(msg: str) -> None:
    try:
        _logger.info(msg, extra={"operation": "cotacao_diagnostico"})
    except Exception:
        pass
    if not _diag_log_enabled():
        return
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _provider_browser_state(
    provider: Any,
    *,
    timeout_ms: int | None = None,
    wait_until: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    state = getattr(provider, "_last_browser_state", None)
    browser_state: dict[str, Any] = dict(state) if isinstance(state, dict) else {}
    page = getattr(provider, "_page", None)
    browser = getattr(provider, "_browser", None)

    if url:
        browser_state.setdefault("url", url)
    if timeout_ms is not None:
        browser_state.setdefault("timeout_ms", timeout_ms)
    if wait_until:
        browser_state.setdefault("wait_until", wait_until)

    try:
        if page is not None:
            browser_state.setdefault("current_url", str(page.url or ""))
    except Exception:
        pass
    try:
        if page is not None:
            browser_state.setdefault("page_closed", bool(page.is_closed()))
    except Exception:
        pass
    try:
        if browser is not None:
            browser_state.setdefault("browser_connected", bool(browser.is_connected()))
    except Exception:
        pass
    return browser_state


def _safe_cotacao_error_context(
    nome: str,
    kwargs: dict[str, Any] | None,
    *,
    duration_ms: int | None = None,
    uf_destino: str | None = None,
) -> dict[str, Any]:
    data = kwargs if isinstance(kwargs, dict) else {}
    cubagens = data.get("cubagens")
    context: dict[str, Any] = {
        "provider": normalize_carrier_name(nome),
        "source": "cotacao_usuario",
        "uf_destino": str(uf_destino or "").upper() or None,
        "duration_ms": duration_ms,
        "volumes": data.get("volumes"),
        "cubagens_count": len(cubagens) if isinstance(cubagens, list) else 0,
        "preencher_cep_origem": bool(data.get("preencher_cep_origem")),
    }
    for field, precision in (("peso", 3), ("valor", 2), ("cubagem_m3", 4)):
        try:
            value = data.get(field)
            context[field] = round(float(value), precision) if value is not None else None
        except Exception:
            context[field] = None
    return {key: value for key, value in context.items() if value is not None}


def _is_rodonaves_quotation_goto_timeout(nome: str, detail: Any) -> bool:
    if normalize_carrier_name(nome) != "rodonaves":
        return False
    text = str(detail or "").lower()
    return (
        "page.goto" in text
        and "timeout" in text
        and ("cliente.rte.com.br/quotation" in text or "/quotation" in text)
    )


def _report_rodonaves_goto_timeout(
    provider: Any,
    kwargs: dict[str, Any],
    *,
    detail: Any,
    duration_ms: int | None,
    uf_destino: str | None,
) -> None:
    try:
        report_error_message(
            f"RODONAVES retornou None: {detail}",
            context="cotacao_RODONAVES",
            module="cotacao",
            provider="rodonaves",
            stage="cotacao",
            event="rodonaves_quotation_goto_timeout",
            severity="error",
            source="cotacao_usuario",
            carrier_enabled=True,
            context_json=_safe_cotacao_error_context(
                "RODONAVES",
                kwargs,
                duration_ms=duration_ms,
                uf_destino=uf_destino,
            ),
            browser_state_json=_provider_browser_state(
                provider,
                timeout_ms=30000,
                wait_until="domcontentloaded",
                url="https://cliente.rte.com.br/Quotation",
            ),
        )
    except Exception:
        pass


def _remote_disabled_results_for_config(config: dict[str, Any], *, contexto: str) -> tuple[dict[str, Any], list[ResultadoCotacao]]:
    effective_config = dict(config) if isinstance(config, dict) else {}
    transportadoras_cfg = effective_config.get("transportadoras", {}) if isinstance(effective_config, dict) else {}
    if not isinstance(transportadoras_cfg, dict):
        transportadoras_cfg = {}
    transportadoras_cfg = dict(transportadoras_cfg)

    skipped: list[ResultadoCotacao] = []
    for carrier in KNOWN_CARRIERS:
        canonical = normalize_carrier_name(carrier)
        allowed, message = carrier_enabled_or_message(canonical)
        if allowed:
            continue
        section = transportadoras_cfg.get(canonical)
        if not isinstance(section, dict):
            section = {}
        section = dict(section)
        section["habilitado"] = False
        transportadoras_cfg[canonical] = section
        display = canonical.upper()
        _log_diag(f"{display} ignorada em {contexto}: {message or CARRIER_DISABLED_MESSAGE}")
        skipped.append(
            ResultadoCotacao(
                transportadora=display,
                status="desabilitada",
                detalhes=message or CARRIER_DISABLED_MESSAGE,
            )
        )

    effective_config["transportadoras"] = transportadoras_cfg
    return effective_config, skipped


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
        report_quotation_finished(finished_status, duration_ms=duration_ms, metadata=metadata)
        for provider, payload in carrier_results.items():
            report_carrier_quotation_result(
                provider,
                payload["status"],
                duration_ms=payload["duration_ms"],
                value_cents=payload["value_cents"],
                metadata=metadata,
            )
    except Exception:
        pass


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
            remote_allowed, _message = carrier_enabled_or_message(canonical)
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


def _create_quotation_job_best_effort(source_type: str, payload: dict[str, Any]) -> Any:
    try:
        result = create_quotation_job(source_type, payload=payload, wait=True)
        job_id = result.get("job_id") if isinstance(result, dict) else None
        if not job_id:
            status_code = result.get("status_code") if isinstance(result, dict) else None
            if status_code:
                _log_diag(f"Job de cotação não criado (HTTP {status_code})")
            else:
                _log_diag("Job de cotação não criado; cotação local continuará")
        return job_id
    except Exception as exc:
        _log_diag(f"Falha ao criar job de cotação; cotação local continuará: {exc}")
        return None


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
        carrier_results[canonical] = {
            "status": _usage_status_from_result(getattr(result, "status", "")),
            "duration_ms": getattr(result, "duration_ms", None),
            "value_cents": _value_cents_from_frete(getattr(result, "valor_frete", None)),
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
        if prazo_por_provider.get(provider) is not None:
            item["prazo_dias"] = prazo_por_provider[provider]
        transportadoras.append(item)

    return {
        "summary": {
            "status": "ok" if success_count > 0 else "error",
            "total_providers": len(transportadoras),
            "success_count": success_count,
            "error_count": error_count,
            "disabled_count": disabled_count,
        },
        "transportadoras": transportadoras,
    }


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
            return re.sub(r"\s+", " ", detalhe)[:240]
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
        update_result = update_quotation_job_result(
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


def _config_template_path() -> Path | None:
    base = _base_dir()
    candidates = [
        base / "Fretio" / "CONFIG.example.toml",
        base / "CONFIG.example.toml",
        Path.cwd() / "Fretio" / "CONFIG.example.toml",
        Path.cwd() / "CONFIG.example.toml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _default_config_path() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / "Fretio" / "CONFIG.toml"
    return _base_dir() / "CONFIG.toml"


def _criar_config_padrao() -> Path | None:
    destino = _default_config_path()
    if destino.exists():
        return destino
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        template_path = _config_template_path()
        if template_path:
            destino.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            destino.write_text(_CONFIG_FALLBACK, encoding="utf-8")
        _log_diag(f"CONFIG.toml criado em: {destino}")
        return destino
    except Exception as error:
        _log_diag(f"Falha ao criar CONFIG.toml em {destino}: {error}")
        return None


def _candidatos_config(config_path: Path | None = None) -> list[Path]:
    if config_path:
        return [config_path]

    base = _base_dir()
    candidates = [
        base / "Fretio" / "CONFIG.toml",
        base / "CONFIG.toml",
        Path.cwd() / "Fretio" / "CONFIG.toml",
        Path.cwd() / "CONFIG.toml",
    ]

    appdata = os.getenv("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Fretio" / "CONFIG.toml")

    programdata = os.getenv("PROGRAMDATA")
    if programdata:
        candidates.append(Path(programdata) / "Fretio" / "CONFIG.toml")

    return candidates


def _empresa_from_config_path(config_path: Path | None) -> str:
    if not config_path:
        return "default"
    try:
        path = Path(config_path)
        if path.name.lower() == "config.toml" and path.parent.name:
            return path.parent.name
    except Exception:
        pass
    return "default"


def _aplicar_credenciais_seguras(config: dict[str, Any], empresa: str) -> dict[str, Any]:
    try:
        import secure_credentials

        secure_credentials.migrate_plaintext_credentials(config, empresa)
        return secure_credentials.overlay_secure_credentials(config, empresa)
    except Exception as error:
        _log_diag(f"Credenciais seguras indisponíveis; usando CONFIG.toml: {error}")
        return config


def _carregar_config(config_path: Path | None = None) -> dict[str, Any]:
    if config_path is None:
        manager = ConfigManager.get_instance("default")
        config = manager.load_config()
        if manager.get_loaded_path() is None:
            criado = _criar_config_padrao()
            if criado:
                config = manager.reload()
        return config if isinstance(config, dict) else {}

    if tomllib is None:
        _log_diag("tomllib indisponível; usando configuração vazia")
        return {}

    candidates = _candidatos_config(config_path=config_path)
    for cfg_path in candidates:
        if not cfg_path.exists():
            continue
        try:
            with cfg_path.open("rb") as file:
                data = tomllib.load(file)
                if isinstance(data, dict):
                    _log_diag(f"CONFIG carregado de: {cfg_path}")
                    return _aplicar_credenciais_seguras(data, _empresa_from_config_path(cfg_path))
        except Exception as error:
            _log_diag(f"Falha ao ler CONFIG em {cfg_path}: {error}")

    if config_path is None:
        criado = _criar_config_padrao()
        if criado:
            try:
                with criado.open("rb") as file:
                    data = tomllib.load(file)
                    if isinstance(data, dict):
                        return _aplicar_credenciais_seguras(data, _empresa_from_config_path(criado))
            except Exception as error:
                _log_diag(f"Falha ao ler CONFIG criado em {criado}: {error}")

    _log_diag("Nenhum CONFIG.toml encontrado; usando configuração vazia")
    return {}


def obter_cep_origem_default(config_path: Path | None = None) -> str:
    config = _carregar_config(config_path=config_path)
    if not isinstance(config, dict):
        return CEP_ORIGEM_PADRAO
    romaneio_cfg = config.get("romaneio", {})
    if not isinstance(romaneio_cfg, dict):
        return CEP_ORIGEM_PADRAO
    cep_cfg = _cep(str(romaneio_cfg.get("cep_origem", "") or ""))
    return cep_cfg or CEP_ORIGEM_PADRAO


def _resolver_cep_origem(config: dict[str, Any], cep_origem_informado: str) -> str:
    cep_informado = _cep(cep_origem_informado)

    romaneio_cfg = config.get("romaneio", {}) if isinstance(config, dict) else {}
    cep_romaneio = ""
    if isinstance(romaneio_cfg, dict):
        cep_romaneio = _cep(str(romaneio_cfg.get("cep_origem", "") or ""))

    transportadoras_cfg = config.get("transportadoras", {}) if isinstance(config, dict) else {}
    transportadora_ceps: list[str] = []
    if isinstance(transportadoras_cfg, dict):
        for nome in ("braspress", "bauer", "trd"):
            sec = transportadoras_cfg.get(nome, {})
            if isinstance(sec, dict):
                cep_sec = _cep(str(sec.get("cep_origem", "") or ""))
                transportadora_ceps.append(cep_sec)

    resolved = _resolver_cep_origem_cached(
        cep_informado,
        cep_romaneio,
        tuple(transportadora_ceps),
    )
    if cep_informado:
        return resolved
    if cep_romaneio and resolved == cep_romaneio:
        _log_diag(f"Usando CEP origem do romaneio: {cep_romaneio}")
        return resolved
    for nome, cep_sec in zip(("braspress", "bauer", "trd"), transportadora_ceps):
        if cep_sec and resolved == cep_sec:
            _log_diag(f"Usando CEP origem de transportadoras.{nome}: {cep_sec}")
            return resolved
    _log_diag(f"Usando CEP origem padrão fixo: {CEP_ORIGEM_PADRAO}")
    return resolved


def _dados_envio(extrator, pedidos: list[Any]) -> dict[str, Any]:
    if not pedidos:
        return {}

    grupos_caixa, caixas_complementares, total_boxes, total_volume, total_weight, total_valor = extrator._calcular_caixas_agrupadas(pedidos)

    destino_cep = extrator.obter_cep_local_entrega(pedidos[0].local_entrega or "")
    uf_destino = ""
    try:
        if hasattr(extrator, "obter_uf_local_entrega"):
            uf_destino = str(extrator.obter_uf_local_entrega(pedidos[0].local_entrega or "") or "").strip().upper()
    except Exception:
        uf_destino = ""
    cnpj_destinatario = _digits(getattr(pedidos[0], "cnpj_cliente", ""))

    def _parse_dims_cm(dims_str: str) -> tuple[int, int, int]:
        parts = re.split(r"[xX×]", str(dims_str))
        if len(parts) < 3:
            return 0, 0, 0
        try:
            # PDF traz dimensões na ordem A×L×C (altura × largura × comprimento)
            a = int(float(re.sub(r"[^\d.,]", "", parts[0].strip()).replace(",", ".") or "0"))
            l = int(float(re.sub(r"[^\d.,]", "", parts[1].strip()).replace(",", ".") or "0"))
            c = int(float(re.sub(r"[^\d.,]", "", parts[2].strip()).replace(",", ".") or "0"))
            return a, l, c
        except (ValueError, IndexError):
            return 0, 0, 0

    # Cubagens reais usadas no romaneio (inclui caixas complementares).
    # Consolidamos por dimensão para enviar múltiplas linhas quando necessário.
    cubagens_map: dict[tuple[int, int, int], int] = {}

    def _add_cubagem(dims_str: str, quantidade: int = 1) -> None:
        a, l, c = _parse_dims_cm(dims_str)
        if a <= 0 or l <= 0 or c <= 0:
            return
        try:
            qtd = int(quantidade or 0)
        except Exception:
            return
        if qtd <= 0:
            return
        key = (a, l, c)
        cubagens_map[key] = cubagens_map.get(key, 0) + qtd

    # 1) Caixas completas realmente usadas
    if isinstance(grupos_caixa, dict):
        for info in grupos_caixa.values():
            if not isinstance(info, dict):
                continue
            calc = info.get("calculated", {}) if isinstance(info.get("calculated", {}), dict) else {}
            full_boxes = int(calc.get("full_boxes", 0) or 0)
            if full_boxes <= 0:
                continue
            d = str(info.get("dims", "") or "").strip()
            if d:
                _add_cubagem(d, full_boxes)

    # 2) Caixas complementares realmente usadas
    if caixas_complementares:
        for cx in caixas_complementares:
            if not isinstance(cx, dict):
                continue
            d = str(cx.get("dims", "") or "").strip()
            if d:
                qtd_comp = cx.get("quantidade", 0)
                _add_cubagem(d, int(qtd_comp or 0))

    altura_cm = 0
    largura_cm = 0
    comprimento_cm = 0
    for (a, l, c), _q in cubagens_map.items():
        if a * l * c > altura_cm * largura_cm * comprimento_cm:
            altura_cm, largura_cm, comprimento_cm = a, l, c

    cubagens = [
        {
            "quantidade": int(q),
            "altura_cm": int(a),
            "largura_cm": int(l),
            "comprimento_cm": int(c),
        }
        for (a, l, c), q in cubagens_map.items()
    ]

    descricoes_itens = []
    for p in pedidos:
        for item in p.itens:
            if item.produto:
                descricoes_itens.append(item.produto.strip())
            if item.descricao:
                descricoes_itens.append(item.descricao.strip())

    return {
        "destino_cep": _cep(destino_cep),
        "uf_destino": uf_destino,
        "cnpj_destinatario": cnpj_destinatario,
        "peso": float(total_weight or 0.0),
        "valor": float(total_valor or 0.0),
        "volumes": int(total_boxes or 0),
        "cubagem_m3": float(total_volume or 0.0),
        "comprimento_cm": comprimento_cm,
        "largura_cm": largura_cm,
        "altura_cm": altura_cm,
        "cubagens": cubagens,
        "descricoes_itens": descricoes_itens,
    }


def _to_float_br(value: str) -> float:
    txt = re.sub(r"[^\d,.\-]", "", str(value or "").strip())
    if not txt:
        return 0.0
    if "," in txt and "." in txt:
        txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt:
        txt = txt.replace(",", ".")
    return float(txt)


def _normalizar_romaneio_colado(texto: str) -> str:
    normalizado = str(texto or "").replace("\r\n", "\n").replace("\r", "\n")
    normalizado = re.sub(r"(?i)<br\s*/?>", "\n", normalizado)
    normalizado = re.sub(r"(?i)</p>", "\n", normalizado)
    normalizado = re.sub(r"(?i)<[^>]+>", " ", normalizado)
    normalizado = normalizado.replace("&nbsp;", " ")
    linhas = [re.sub(r"\s+", " ", linha).strip() for linha in normalizado.split("\n")]
    linhas = [linha for linha in linhas if linha]
    return "\n".join(linhas)


def _parse_dim_cm(raw: str) -> int:
    try:
        val = _to_float_br(raw)
    except ValueError:
        return 0
    # Aceita tanto dimensões em cm (31) quanto em metros (0,31).
    if 0 < val <= 3.5:
        return int(round(val * 100))
    return int(round(val))


def _extrair_uf_hint_texto(texto: str, pos_referencia: int = -1) -> str:
    """Tenta extrair uma UF (cidade/UF) próxima ao bloco do destinatário."""
    pattern = re.compile(
        r"\b([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]{2,})\s*/\s*([A-Za-z]{2})\b",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(texto or ""))
    if not matches:
        return ""

    if pos_referencia >= 0:
        depois = [m for m in matches if m.start() >= pos_referencia]
        if depois:
            return str(depois[0].group(2) or "").strip().upper()
    return str(matches[0].group(2) or "").strip().upper()


def _selecionar_cep_destino(texto: str, pos_referencia: int = -1, uf_hint: str = "") -> str:
    """
    Seleciona o CEP mais provável do destinatário.
    Regras:
    1) Preferir CEP após o CNPJ/CPF do destinatário.
    2) Se houver UF hint, priorizar CEPs compatíveis com essa UF.
    3) Priorizar CEP com rótulo explícito "CEP:".
    4) Em empate, usar o mais próximo da referência.
    """
    raw = str(texto or "")
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()

    label_pat = re.compile(r"\bCEP\s*:\s*(\d{2}\.?\d{3}-?\d{3}|\d{5}-?\d{3})\b", re.IGNORECASE)
    generic_pat = re.compile(r"\b(\d{5}-?\d{3})\b")

    for m in label_pat.finditer(raw):
        cep_digits = _cep(m.group(1))
        if len(cep_digits) != 8:
            continue
        key = (m.start(), cep_digits)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "cep": cep_digits,
                "pos": m.start(),
                "labeled": True,
                "uf": _cep_para_uf(cep_digits) or "",
            }
        )

    for m in generic_pat.finditer(raw):
        cep_digits = _cep(m.group(1))
        if len(cep_digits) != 8:
            continue
        key = (m.start(), cep_digits)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "cep": cep_digits,
                "pos": m.start(),
                "labeled": False,
                "uf": _cep_para_uf(cep_digits) or "",
            }
        )

    if not candidates:
        return ""

    ref = int(pos_referencia or 0)
    uf_ref = str(uf_hint or "").strip().upper()

    def _rank(c: dict[str, Any]) -> tuple[int, int, int, int]:
        pos = int(c.get("pos", 0) or 0)
        after = 0 if pos >= ref else 1
        dist = abs(pos - ref)
        labeled_penalty = 0 if bool(c.get("labeled")) else 1
        uf_penalty = 0
        if uf_ref:
            uf_penalty = 0 if str(c.get("uf", "")).upper() == uf_ref else 1
        return (uf_penalty, after, labeled_penalty, dist)

    candidates.sort(key=_rank)
    return str(candidates[0].get("cep", "") or "")


def _dados_envio_romaneio_colado(romaneio_colado: str) -> dict[str, Any]:
    texto = _normalizar_romaneio_colado(romaneio_colado)
    if not texto:
        raise ValueError("Romaneio colado vazio")

    m_cnpj = re.search(r"\bCNPJ/CPF\s*:\s*([0-9./-]{11,18})", texto, re.IGNORECASE)
    cnpj_destinatario = _digits(m_cnpj.group(1)) if m_cnpj else ""
    pos_ref = int(m_cnpj.end()) if m_cnpj else -1
    uf_hint = _extrair_uf_hint_texto(texto, pos_referencia=pos_ref)
    destino_cep = _selecionar_cep_destino(texto, pos_referencia=pos_ref, uf_hint=uf_hint)
    uf_destino = str(uf_hint or "").strip().upper()
    if not uf_destino and len(destino_cep) == 8:
        uf_destino = str(_cep_para_uf(destino_cep) or "").strip().upper()

    m_volumes = re.search(r"-\s*VOL(?:UME)?\s*:\s*(\d+)", texto, re.IGNORECASE)
    m_cubagem = re.search(r"-\s*CUBAGEM\s*:\s*([\d.,]+)\s*m3", texto, re.IGNORECASE)
    m_peso = re.search(r"-\s*PESO\s*:\s*([\d.,]+)\s*kg", texto, re.IGNORECASE)
    m_total = re.search(r"-\s*TOTAL\s*:\s*R\$\s*([\d.,]+)", texto, re.IGNORECASE)

    missing: list[str] = []
    if len(cnpj_destinatario) != 14:
        missing.append("CNPJ")
    if len(destino_cep) != 8:
        missing.append("CEP")
    if not m_volumes:
        missing.append("VOL")
    if not m_cubagem:
        missing.append("CUBAGEM")
    if not m_peso:
        missing.append("PESO")
    if not m_total:
        missing.append("TOTAL")
    if missing:
        raise ValueError(f"Romaneio colado inválido. Campos ausentes: {', '.join(missing)}")

    volumes = int(m_volumes.group(1))
    cubagem_m3 = _to_float_br(m_cubagem.group(1))
    peso = _to_float_br(m_peso.group(1))
    valor = _to_float_br(m_total.group(1))
    if volumes <= 0:
        raise ValueError("Romaneio colado inválido. Campo VOL deve ser maior que zero.")
    if peso <= 0:
        raise ValueError("Romaneio colado inválido. Campo PESO deve ser maior que zero.")
    if cubagem_m3 <= 0:
        raise ValueError("Romaneio colado inválido. Campo CUBAGEM deve ser maior que zero.")
    if valor < 0:
        raise ValueError("Romaneio colado inválido. Campo TOTAL não pode ser negativo.")

    cubagens: list[dict[str, Any]] = []

    # Ex.: "2 x Caixas fechadas - 1,650 kg - 0,044 m3 - 31x31x45"
    for m in re.finditer(
        r"(?im)^\s*(\d+)\s*x\s+.+?-\s*([\d.,]+)\s*kg\s*-\s*[\d.,]+\s*m3\s*-\s*([\d.,]+)\s*[xX×]\s*([\d.,]+)\s*[xX×]\s*([\d.,]+)\b",
        texto,
    ):
        try:
            qtd = int(m.group(1) or 0)
        except Exception:
            qtd = 0
        peso_por_volume_kg = _to_float_br(m.group(2))
        # Texto colado traz dimensões na ordem A×L×C (altura × largura × comprimento)
        a = _parse_dim_cm(m.group(3))
        l = _parse_dim_cm(m.group(4))
        c = _parse_dim_cm(m.group(5))
        if qtd <= 0 or a <= 0 or l <= 0 or c <= 0 or peso_por_volume_kg <= 0:
            continue
        cubagens.append(
            {
                "quantidade": qtd,
                "comprimento_cm": c,
                "largura_cm": l,
                "altura_cm": a,
                "peso_por_volume_kg": peso_por_volume_kg,
            }
        )

    comprimento_cm = 0
    largura_cm = 0
    altura_cm = 0
    for cub in cubagens:
        c = int(cub.get("comprimento_cm", 0) or 0)
        l = int(cub.get("largura_cm", 0) or 0)
        a = int(cub.get("altura_cm", 0) or 0)
        if c * l * a > comprimento_cm * largura_cm * altura_cm:
            comprimento_cm, largura_cm, altura_cm = c, l, a

    descricoes_itens = []
    for m_desc in re.finditer(r"(?im)^\s*(\S+.*?):\s*\d+\s*und\b", texto):
        descricoes_itens.append(m_desc.group(1).strip())

    return {
        "destino_cep": destino_cep,
        "uf_destino": uf_destino,
        "cnpj_destinatario": cnpj_destinatario,
        "peso": peso,
        "valor": valor,
        "volumes": volumes,
        "cubagem_m3": cubagem_m3,
        "comprimento_cm": comprimento_cm,
        "largura_cm": largura_cm,
        "altura_cm": altura_cm,
        "cubagens": cubagens,
        "descricoes_itens": descricoes_itens,
    }


def _cubagens_validas(cubagens_raw: Any) -> list[dict[str, Any]]:
    validas: list[dict[str, Any]] = []
    if not isinstance(cubagens_raw, list):
        return validas
    for row in cubagens_raw:
        if not isinstance(row, dict):
            continue
        try:
            qtd = int(row.get("quantidade", 0) or 0)
            c = int(row.get("comprimento_cm", 0) or 0)
            l = int(row.get("largura_cm", 0) or 0)
            a = int(row.get("altura_cm", 0) or 0)
        except Exception:
            continue
        if qtd <= 0 or c <= 0 or l <= 0 or a <= 0:
            continue
        peso_por_volume_kg = None
        try:
            peso_raw = row.get("peso_por_volume_kg", None)
            if peso_raw is not None:
                peso_val = float(peso_raw)
                if peso_val > 0:
                    peso_por_volume_kg = peso_val
        except Exception:
            peso_por_volume_kg = None
        validas.append(
            {
                "quantidade": qtd,
                "comprimento_cm": c,
                "largura_cm": l,
                "altura_cm": a,
                "peso_por_volume_kg": peso_por_volume_kg,
            }
        )
    return validas


def _kill_orphan_Fretio_chromes() -> None:
    """Mata processos Chrome órfãos de sessões anteriores do Fretio.

    Procura por processos chrome.exe cujo command-line contenha
    o diretório .Fretio (user-data-dir dos providers Alfa e Rodonaves).
    Tenta wmic primeiro (rápido); se falhar usa Get-CimInstance (Windows 11+).
    """
    if sys.platform != "win32":
        return
    import subprocess as _sp
    fretio_marker = os.path.join(os.path.expanduser("~"), ".fretio").replace("/", "\\").lower()
    fretio_temp_marker = "fretio_chrome_"

    def _kill_pids_from_lines(lines: list[str]) -> None:
        pid = None
        cmd = ""
        for line in lines:
            line = line.strip()
            if not line:
                if pid is not None and (fretio_marker in cmd.lower() or fretio_temp_marker in cmd.lower()):
                    try:
                        _sp.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True, timeout=10,
                            creationflags=_sp.CREATE_NO_WINDOW,
                        )
                        _log_diag(f"Matou Chrome órfão do Fretio PID={pid} (tree kill)")
                    except Exception:
                        try:
                            os.kill(pid, 9)
                            _log_diag(f"Matou Chrome órfão do Fretio PID={pid} (os.kill)")
                        except OSError:
                            pass
                pid = None
                cmd = ""
                continue
            if line.startswith("CommandLine="):
                cmd = line[len("CommandLine="):]
            elif line.startswith("ProcessId="):
                try:
                    pid = int(line[len("ProcessId="):])
                except ValueError:
                    pid = None
        if pid is not None and (fretio_marker in cmd.lower() or fretio_temp_marker in cmd.lower()):
            try:
                os.kill(pid, 9)
                _log_diag(f"Matou Chrome órfão do Fretio PID={pid}")
            except OSError:
                pass

    # Tenta wmic (rápido, disponível em Windows 10 e versões antigas do 11)
    try:
        result = _sp.run(
            ["wmic", "process", "where", "Name='chrome.exe'", "get",
             "ProcessId,CommandLine", "/FORMAT:LIST"],
            capture_output=True, text=True, timeout=10,
            creationflags=_sp.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            _kill_pids_from_lines(result.stdout.splitlines())
            return
    except Exception:
        pass

    # Fallback: PowerShell Get-CimInstance (Windows 11 22H2+)
    try:
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" "
            "| ForEach-Object { \"CommandLine=$($_.CommandLine)\"; \"ProcessId=$($_.ProcessId)\"; \"\" }"
        )
        result = _sp.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15,
            creationflags=_sp.CREATE_NO_WINDOW,
        )
        _kill_pids_from_lines(result.stdout.splitlines())
    except Exception as e:
        _log_diag(f"_kill_orphan_Fretio_chromes falhou: {e}")


# Prioridade de lentidão: maior = mais lento (baseado em testes reais).
# Usado para iniciar os mais lentos primeiro e para ordenar resultados.
_PRIORIDADE_LENTIDAO: dict[str, int] = {
    "TRD": 700,
    "ALFA": 600,
    "BRASPRESS": 500,
    "EUCATUR": 400,
    "COOPEX": 350,
    "RODONAVES": 300,
    "AGEX": 100,
}

# Timeouts por provider (fluxos reais medidos):
# - TRD: login 2×60s + etapas + modais → mínimo ~45-50s, pior caso >150s
# - RODONAVES: CAPTCHA 45s + polling resultado 30s → mínimo ~75s
# - AGEX: wait_for_url resultado 60s + login 30s → mínimo ~53s
_TIMEOUT_COTACAO_S: dict[str, int] = {
    "ALFA": 60,
    "TRD": 120,
    "RODONAVES": 120,
    "AGEX": 90,
    # SSW providers: polling até 25s + fallbacks → dar margem para completar internamente
    "COOPEX": 90,
    "EUCATUR": 90,
}
_TIMEOUT_COTACAO_PADRAO_S = 45

_TIMEOUT_PRELOGIN_S: dict[str, int] = {
    "ALFA": 90,
    "TRD": 90,
    "RODONAVES": 60,
    "AGEX": 60,
}
_TIMEOUT_PRELOGIN_PADRAO_S = 45


class _ProviderSessionRegistry:
    """Mantém providers ativos e timestamps de uso sob lock único."""

    def __init__(self) -> None:
        self._providers: dict[str, Any] = {}
        self._ultimo_uso: dict[str, float] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def providers(self) -> dict[str, Any]:
        return self._providers

    async def items(self) -> list[tuple[str, Any]]:
        async with self._lock:
            return list(self._providers.items())

    async def get(self, nome: str) -> Any | None:
        async with self._lock:
            return self._providers.get(nome)

    async def register(self, nome: str, provider: Any) -> Any | None:
        async with self._lock:
            anterior = self._providers.get(nome)
            self._providers[nome] = provider
            self._ultimo_uso[nome] = time.monotonic()
            return anterior

    async def ensure(self, nome: str, factory: Callable[[], Any]) -> tuple[Any, bool]:
        async with self._lock:
            provider = self._providers.get(nome)
            created = provider is None
            if provider is None:
                provider = factory()
                self._providers[nome] = provider
            self._ultimo_uso[nome] = time.monotonic()
            return provider, created

    async def touch(self, nome: str) -> None:
        async with self._lock:
            if nome in self._providers:
                self._ultimo_uso[nome] = time.monotonic()

    async def touch_all(self) -> None:
        agora = time.monotonic()
        async with self._lock:
            for nome in self._providers:
                self._ultimo_uso[nome] = agora

    async def pop(self, nome: str, *, expected: Any | None = None) -> Any | None:
        async with self._lock:
            provider = self._providers.get(nome)
            if provider is None:
                return None
            if expected is not None and provider is not expected:
                return None
            self._providers.pop(nome, None)
            self._ultimo_uso.pop(nome, None)
            return provider

    async def pop_all(self, *, exclude: set[str] | None = None) -> list[tuple[str, Any]]:
        nomes_excluidos = {str(nome).strip().lower() for nome in (exclude or set())}
        async with self._lock:
            removidos: list[tuple[str, Any]] = []
            for nome in list(self._providers):
                if str(nome).strip().lower() in nomes_excluidos:
                    continue
                provider = self._providers.pop(nome, None)
                self._ultimo_uso.pop(nome, None)
                if provider is not None:
                    removidos.append((nome, provider))
            return removidos

    async def pop_idle(self, idle_timeout_s: float) -> list[tuple[str, Any, float]]:
        agora = time.monotonic()
        async with self._lock:
            removidos: list[tuple[str, Any, float]] = []
            for nome in list(self._providers):
                tempo_ocioso = agora - self._ultimo_uso.get(nome, agora)
                if tempo_ocioso <= idle_timeout_s:
                    continue
                provider = self._providers.pop(nome, None)
                self._ultimo_uso.pop(nome, None)
                if provider is not None:
                    removidos.append((nome, provider, tempo_ocioso))
            return removidos


class TransportadoraSession:
    """Gerencia sessões persistentes dos providers (browsers já logados)."""

    IDLE_TIMEOUT_S: float = 600.0  # 10 minutos
    _IDLE_CHECK_INTERVAL_S: float = 60.0
    _LAZY_PRELOGIN_PROVIDERS: set[str] = {"trd", "rodonaves"}

    def __init__(self, config_path: Path | None = None):
        self.config = _carregar_config(config_path=config_path)
        self.provider_factory = ProviderFactory(config=self.config)
        self._provider_sessions = _ProviderSessionRegistry()
        self._inicializado = False
        self._idle_task: asyncio.Task | None = None
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()

    @property
    def providers(self) -> dict[str, Any]:
        return self._provider_sessions.providers

    async def __aenter__(self) -> "TransportadoraSession":
        await self.inicializar()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.cleanup()

    async def listar_providers(self) -> list[tuple[str, Any]]:
        return await self._provider_sessions.items()

    async def obter_provider(self, nome: str) -> Any | None:
        return await self._provider_sessions.get(nome)

    async def registrar_provider(self, nome: str, provider: Any) -> None:
        anterior = await self._provider_sessions.register(nome, provider)
        _logger.debug(
            "Provider registrado na sessão",
            extra={"operation": "session_register", "provider": nome},
        )
        if anterior is not None and anterior is not provider:
            await self._cleanup_provider_instance(
                anterior,
                success_message=f"Cleanup {nome} OK (substituição)",
                failure_message=f"Cleanup {nome} falhou na substituição",
            )

    async def assegurar_provider(self, nome: str, factory: Callable[[], Any]) -> Any:
        provider, created = await self._provider_sessions.ensure(nome, factory)
        if created:
            await self._executar_lazy_prelogin(nome, provider)
        _logger.debug(
            "Provider obtido da sessão",
            extra={"operation": "session_ensure", "provider": nome},
        )
        return provider

    async def _executar_lazy_prelogin(self, nome: str, provider: Any) -> None:
        nome_normalizado = str(nome).strip().lower()
        if nome_normalizado not in self._LAZY_PRELOGIN_PROVIDERS:
            return
        pre_login = getattr(provider, "pre_login", None)
        if not callable(pre_login):
            return
        if getattr(provider, "_logged_in", False):
            return

        timeout_s = _TIMEOUT_PRELOGIN_S.get(nome_normalizado.upper(), _TIMEOUT_PRELOGIN_PADRAO_S)
        try:
            _log_diag(f"Lazy pre-login {nome_normalizado.upper()}...")
            resultado = await asyncio.wait_for(asyncio.shield(pre_login()), timeout=timeout_s)
            if resultado is False:
                detalhe = str(getattr(provider, "last_error", "") or "").strip()
                _log_diag(
                    f"Lazy pre-login {nome_normalizado.upper()} retornou False"
                    + (f": {detalhe}" if detalhe else "")
                )
                return
            _log_diag(f"Lazy pre-login {nome_normalizado.upper()} OK")
        except asyncio.TimeoutError:
            _log_diag(
                f"Lazy pre-login {nome_normalizado.upper()} timeout ({timeout_s}s) — "
                "login continuará na cotação"
            )
        except Exception as exc:
            _log_diag(f"Lazy pre-login {nome_normalizado.upper()} falhou: {exc}")

    async def _cleanup_provider_instance(
        self,
        provider: Any,
        *,
        success_message: str,
        failure_message: str,
    ) -> None:
        try:
            await provider.cleanup()
            _log_diag(success_message)
        except Exception as e:
            _log_diag(f"{failure_message}: {e}")

    async def fechar_provider(
        self,
        nome: str,
        *,
        success_message: str | None = None,
        failure_message: str | None = None,
        expected: Any | None = None,
    ) -> bool:
        provider = await self._provider_sessions.pop(nome, expected=expected)
        if provider is None:
            _logger.debug(
                "Provider não encontrado para encerramento",
                extra={"operation": "session_close", "provider": nome},
            )
            return False
        await self._cleanup_provider_instance(
            provider,
            success_message=success_message or f"Cleanup {nome} OK",
            failure_message=failure_message or f"Cleanup {nome} falhou",
        )
        return True

    async def fechar_providers_exceto(self, nomes_preservados: set[str], *, contexto: str) -> None:
        removidos = await self._provider_sessions.pop_all(exclude=nomes_preservados)
        for nome, provider in removidos:
            await self._cleanup_provider_instance(
                provider,
                success_message=f"{contexto}: cleanup {nome} OK",
                failure_message=f"{contexto}: cleanup {nome} falhou",
            )

    async def inicializar(self, callback=None, login_status_callback=None):
        """Cria providers e faz pre-login em todos. callback(msg) para status.
        login_status_callback(nome, status) para status individual ('pending','ok','fail')."""
        async with self._lifecycle_lock:
            self.provider_factory.preload()
            _kill_orphan_Fretio_chromes()
            if self._inicializado:
                if MODO_FOCO_TRANSPORTADORA and self.providers:
                    foco = str(MODO_FOCO_TRANSPORTADORA).strip().lower()
                    await self.fechar_providers_exceto(
                        {foco},
                        contexto=f"Modo foco {foco.upper()} ativo",
                    )
                return

            # Verifica Chrome uma única vez antes de iniciar qualquer prelogin.
            # Se não encontrado, reporta um único erro e aborta sem tentar cada transportadora.
            try:
                from fretio.providers.base import find_chrome as _find_chrome_global
                _find_chrome_global()
            except FileNotFoundError as _chrome_err:
                _chrome_msg = str(_chrome_err)
                _log_diag(f"Chrome não encontrado — prelogin cancelado: {_chrome_msg}")
                if callback:
                    callback("Google Chrome não encontrado. Instale o Chrome para usar o Fretio.")
                try:
                    report_error_message(_chrome_msg, context="chrome_missing")
                except Exception:
                    pass
                return
            except Exception:
                pass

            effective_config = dict(self.config) if isinstance(self.config, dict) else {}
            transportadoras_cfg = effective_config.get("transportadoras", {}) if isinstance(effective_config, dict) else {}
            if MODO_FOCO_TRANSPORTADORA:
                if not isinstance(transportadoras_cfg, dict):
                    transportadoras_cfg = {}
                transportadoras_cfg = dict(transportadoras_cfg)
                foco = str(MODO_FOCO_TRANSPORTADORA).strip().lower()
                for nome_cfg in ("braspress", "bauer", "trd", "agex", "eucatur", "rodonaves", "alfa", "coopex"):
                    sec = transportadoras_cfg.get(nome_cfg)
                    if not isinstance(sec, dict):
                        sec = {}
                    sec = dict(sec)
                    sec["habilitado"] = (nome_cfg == foco)
                    transportadoras_cfg[nome_cfg] = sec
                _log_diag(f"Modo foco {foco.upper()} ativo: apenas essa transportadora fará pre-login.")
            if isinstance(effective_config, dict):
                effective_config["transportadoras"] = transportadoras_cfg if isinstance(transportadoras_cfg, dict) else {}
            effective_config, _remote_skipped = _remote_disabled_results_for_config(
                effective_config,
                contexto="pre-login",
            )
            provider_factory = ProviderFactory(config=effective_config)

            bcfg = provider_factory.get_provider_config("braspress")
            if bcfg.get("habilitado", True):
                headless_braspress = bool(bcfg.get("headless", True))
                provider = provider_factory.create("braspress", headless=headless_braspress)
                if provider is not None:
                    await self.registrar_provider("braspress", provider)
                    _log_diag(f"BRASPRESS sessão criada com headless={headless_braspress}")

            baucfg = provider_factory.get_provider_config("bauer")
            if baucfg.get("habilitado", True):
                if not provider_factory.is_available("bauer"):
                    _log_diag("BAUER ignorada: provider bauer_auto não está disponível neste build")
                else:
                    provider = provider_factory.create("bauer")
                    if provider is not None:
                        await self.registrar_provider("bauer", provider)

            tcfg = provider_factory.get_provider_config("trd")
            if tcfg.get("habilitado", True):
                foco_trd = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "trd"
                headless_trd = _trd_headless_config_value(tcfg, foco_trd)
                provider = provider_factory.create("trd", headless=headless_trd)
                if provider is not None:
                    await self.registrar_provider("trd", provider)
                    _log_diag(f"TRD sessão criada com headless={headless_trd}")

            if provider_factory.is_available("agex"):
                acfg = provider_factory.get_provider_config("agex")
                if acfg.get("habilitado", True):
                    foco_agex = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "agex"
                    headless_agex = False if foco_agex else bool(acfg.get("headless", True))
                    provider = provider_factory.create("agex", headless=headless_agex)
                    if provider is not None:
                        await self.registrar_provider("agex", provider)
                        _log_diag(f"AGEX sessão criada com headless={headless_agex}")

            if provider_factory.is_available("eucatur"):
                ecfg = provider_factory.get_provider_config("eucatur")
                if ecfg.get("habilitado", True):
                    foco_eucatur = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "eucatur"
                    headless_eucatur = False if foco_eucatur else bool(ecfg.get("headless", True))
                    provider = provider_factory.create("eucatur", headless=headless_eucatur)
                    if provider is not None:
                        await self.registrar_provider("eucatur", provider)
                        _log_diag(f"EUCATUR sessão criada com headless={headless_eucatur}")

            if provider_factory.is_available("rodonaves"):
                rcfg = provider_factory.get_provider_config("rodonaves")
                if rcfg.get("habilitado", True):
                    if not _uf_atendida(rcfg.get("ufs_atendidas"), None):
                        _log_diag("RODONAVES ignorada por filtro de UF inválido")
                    else:
                        foco_rodonaves = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "rodonaves"
                        headless_rodonaves = False if foco_rodonaves else bool(rcfg.get("headless", True))
                        provider = provider_factory.create("rodonaves", headless=headless_rodonaves)
                        if provider is not None:
                            await self.registrar_provider("rodonaves", provider)
                            _log_diag(f"RODONAVES sessão criada com headless={headless_rodonaves}")

            if provider_factory.is_available("alfa"):
                alcfg = provider_factory.get_provider_config("alfa")
                if alcfg.get("habilitado", True):
                    if not _uf_atendida(alcfg.get("ufs_atendidas"), None):
                        _log_diag("ALFA ignorada por filtro de UF inválido")
                    else:
                        headless_alfa = bool(alcfg.get("headless", False))
                        provider = provider_factory.create("alfa", headless=headless_alfa)
                        if provider is not None:
                            await self.registrar_provider("alfa", provider)
                            _log_diag(f"ALFA sessão criada com headless={headless_alfa}")

            if provider_factory.is_available("coopex"):
                cocfg = provider_factory.get_provider_config("coopex")
                if cocfg.get("habilitado", True):
                    foco_coopex = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "coopex"
                    headless_coopex = False if foco_coopex else bool(cocfg.get("headless", True))
                    provider = provider_factory.create("coopex", headless=headless_coopex)
                    if provider is not None:
                        await self.registrar_provider("coopex", provider)
                        _log_diag(f"COOPEX sessão criada com headless={headless_coopex}")

            _pre_login_semaforo = asyncio.Semaphore(2)
            providers_snapshot = await self.listar_providers()
            total_providers = len(providers_snapshot)
            _log_diag(f"Iniciando pre-login em {total_providers} transportadoras (máx 2 simultâneos)...")
            if callback:
                callback(f"Fazendo login em {total_providers} transportadoras...")

            def _carrier_enabled_for_report(nome_carrier: str) -> bool:
                try:
                    cfg = provider_factory.get_provider_config(normalize_carrier_name(nome_carrier))
                    if isinstance(cfg, dict):
                        return bool(cfg.get("habilitado", True))
                except Exception:
                    pass
                return True

            async def _pre_login_one(nome, prov):
                is_alfa = nome.lower() == "alfa"
                timeout_s = _TIMEOUT_PRELOGIN_S.get(nome.upper(), _TIMEOUT_PRELOGIN_PADRAO_S)
                max_retries = 0 if is_alfa else 1
                backoff = 3

                if login_status_callback:
                    login_status_callback(nome, "pending")

                if not is_alfa:
                    await _pre_login_semaforo.acquire()
                    _log_diag(f"Pre-login semáforo adquirido: {nome}")

                try:
                    for attempt in range(max_retries + 1):
                        try:
                            _log_diag(f"Pre-login {nome}..." if attempt == 0 else f"Pre-login {nome} tentativa {attempt + 1}...")
                            if callback:
                                callback(f"Login: {nome}..." if attempt == 0 else f"Login: {nome} (tentativa {attempt + 1})...")
                            try:
                                pre_login_result = await asyncio.wait_for(asyncio.shield(prov.pre_login()), timeout=timeout_s)
                            except asyncio.TimeoutError:
                                _log_diag(f"Pre-login {nome} timeout ({timeout_s}s) — login continuará na cotação")
                                if login_status_callback:
                                    login_status_callback(nome, "fail")
                                return nome, False
                            if pre_login_result is False:
                                detalhe = str(getattr(prov, "last_error", "") or "").strip()
                                raise RuntimeError(detalhe or f"Pre-login {nome} retornou False")
                            _log_diag(f"Pre-login {nome} OK")
                            if login_status_callback:
                                login_status_callback(nome, "ok")
                            return nome, True
                        except Exception as e:
                            is_connection_error = any(k in str(e) for k in ("ERR_CONNECTION", "ERR_NAME", "ERR_TIMED_OUT", "net::"))
                            if is_connection_error and attempt < max_retries:
                                wait = backoff * (attempt + 1)
                                _log_diag(f"Pre-login {nome} erro de rede ({e}), retry em {wait}s...")
                                await asyncio.sleep(wait)
                                continue
                            _log_diag(f"Pre-login {nome} falhou: {e}")
                            # Chrome ausente já foi reportado globalmente com module="chrome_missing"
                            _e_str = str(e)
                            if "Google Chrome" not in _e_str and "chrome" not in _e_str.lower():
                                carrier_enabled = _carrier_enabled_for_report(nome)
                                report_error_message(
                                    f"Pre-login {nome} falhou: {_e_str}",
                                    context=f"prelogin_{nome}",
                                    module="prelogin",
                                    provider=normalize_carrier_name(nome),
                                    stage="prelogin",
                                    event=f"{normalize_carrier_name(nome)}_prelogin_failed",
                                    severity="error" if carrier_enabled else "warning",
                                    source="prelogin_background",
                                    carrier_enabled=carrier_enabled,
                                    context_json={
                                        "attempt": attempt + 1,
                                        "timeout_s": timeout_s,
                                        "max_retries": max_retries,
                                    },
                                    browser_state_json=_provider_browser_state(
                                        prov,
                                        timeout_ms=int(timeout_s * 1000),
                                    ),
                                )
                            if login_status_callback:
                                login_status_callback(nome, "fail")
                            return nome, False
                finally:
                    if not is_alfa:
                        _pre_login_semaforo.release()

            results = await asyncio.gather(
                *[_pre_login_one(n, p) for n, p in providers_snapshot],
                return_exceptions=True,
            )

            ok_count = sum(1 for r in results if not isinstance(r, Exception) and r[1])
            _log_diag(f"Pre-login concluído: {ok_count}/{total_providers} OK")

            try:
                from fretio.providers._win_taskbar import ocultar_janelas_ime
                n = ocultar_janelas_ime()
                if n:
                    _log_diag(f"Ocultou {n} janela(s) IME residual(is)")
            except Exception:
                pass

            if callback:
                callback(f"Login concluído: {ok_count}/{total_providers} transportadoras prontas")
            self._inicializado = True
            await self._provider_sessions.touch_all()
            self._iniciar_verificador_ocioso()

    async def registrar_uso(self, nome: str) -> None:
        """Atualiza timestamp de último uso de um provider."""
        await self._provider_sessions.touch(nome)

    async def fechar_ociosos(self) -> None:
        """Fecha browsers de providers ociosos por mais de IDLE_TIMEOUT_S."""
        a_finalizar = await self._provider_sessions.pop_idle(self.IDLE_TIMEOUT_S)
        for nome, prov, tempo_ocioso in a_finalizar:
            await self._cleanup_provider_instance(
                prov,
                success_message=f"Idle cleanup {nome} OK (ocioso por {tempo_ocioso:.0f}s)",
                failure_message=f"Idle cleanup {nome} falhou",
            )

    def _iniciar_verificador_ocioso(self) -> None:
        """Inicia task em background que verifica providers ociosos."""
        if self._idle_task is not None and not self._idle_task.done():
            return
        _logger.debug(
            "Iniciando verificador de providers ociosos",
            extra={"operation": "session_idle_monitor"},
        )

        async def _loop():
            # CancelledError externo (cleanup()) é re-levantado imediatamente.
            # Erros genéricos apenas logam e reiniciam o ciclo.
            try:
                while True:
                    try:
                        await asyncio.sleep(self._IDLE_CHECK_INTERVAL_S)
                        await self.fechar_ociosos()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        _log_diag(f"Erro no verificador de ociosidade: {e}")
            except asyncio.CancelledError:
                pass

        self._idle_task = asyncio.ensure_future(_loop())

    async def cleanup(self):
        """Fecha todos os browsers."""
        async with self._lifecycle_lock:
            idle_task = self._idle_task
            self._idle_task = None
            if idle_task is not None:
                idle_task.cancel()
                try:
                    await idle_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    _log_diag(f"Erro ao encerrar verificador de ociosidade: {e}")
            removidos = await self._provider_sessions.pop_all()
            _logger.info(
                "Encerrando providers ativos",
                extra={"operation": "session_cleanup"},
            )
            for nome, prov in removidos:
                await self._cleanup_provider_instance(
                    prov,
                    success_message=f"Cleanup {nome} OK",
                    failure_message=f"Cleanup {nome} falhou",
                )
            self._inicializado = False

    @property
    def pronto(self) -> bool:
        return self._inicializado


async def _executar_cotacoes_com_dados(
    *,
    config: dict[str, Any],
    dados: dict[str, Any],
    cep_origem: str,
    sessao: "TransportadoraSession | None" = None,
    progresso_callback: "Callable[[dict[str, Any]], None] | None" = None,
    cnpj_remetente: str = "",
    tipo_frete: str = "",
) -> list[ResultadoCotacao]:
    def _emitir_progresso(
        *,
        concluidas: int,
        total: int,
        resultado: ResultadoCotacao | None = None,
    ) -> None:
        if progresso_callback is None:
            return
        try:
            progresso_callback(
                {
                    "concluidas": int(concluidas),
                    "total": int(total),
                    "resultado": resultado,
                }
            )
        except Exception as cb_error:
            _log_diag(f"Falha ao notificar progresso de cotação: {cb_error}")

    effective_config = dict(config) if isinstance(config, dict) else {}
    transportadoras_cfg = effective_config.get("transportadoras", {}) if isinstance(effective_config, dict) else {}
    if MODO_FOCO_TRANSPORTADORA:
        if not isinstance(transportadoras_cfg, dict):
            transportadoras_cfg = {}
        transportadoras_cfg = dict(transportadoras_cfg)
        foco = str(MODO_FOCO_TRANSPORTADORA).strip().lower()
        for nome_cfg in ("braspress", "bauer", "trd", "agex", "eucatur", "rodonaves", "coopex"):
            sec = transportadoras_cfg.get(nome_cfg)
            if not isinstance(sec, dict):
                sec = {}
            sec = dict(sec)
            sec["habilitado"] = (nome_cfg == foco)
            transportadoras_cfg[nome_cfg] = sec
        _log_diag(f"Modo foco {foco.upper()} ativo: apenas essa transportadora será cotada.")
        if sessao and getattr(sessao, "providers", None):
            await sessao.fechar_providers_exceto(
                {foco},
                contexto=f"Modo foco {foco.upper()} ativo",
            )
    if isinstance(effective_config, dict):
        effective_config["transportadoras"] = transportadoras_cfg if isinstance(transportadoras_cfg, dict) else {}
    effective_config, remote_skipped_results = _remote_disabled_results_for_config(
        effective_config,
        contexto="cotacao",
    )
    provider_factory = ProviderFactory(config=effective_config)

    async def _obter_provider_sessao(
        nome: str,
        *,
        create_kwargs: dict[str, Any] | None = None,
        desired_headless: bool | None = None,
        log_label: str,
    ):
        if sessao is None:
            return provider_factory.create(nome, **(create_kwargs or {}))

        provider = await sessao.obter_provider(nome)
        if provider is not None and desired_headless is not None:
            headless_atual = bool(getattr(provider, "headless", desired_headless))
            if headless_atual != desired_headless:
                _log_diag(
                    f"{log_label}: headless alterado ({headless_atual} -> {desired_headless}), "
                    "reiniciando sessão do provider."
                )
                await sessao.fechar_provider(
                    nome,
                    success_message=f"{log_label} cleanup ao trocar headless OK",
                    failure_message=f"{log_label} cleanup ao trocar headless falhou",
                    expected=provider,
                )

        return await sessao.assegurar_provider(
            nome,
            lambda: provider_factory.create(nome, **(create_kwargs or {})),
        )

    origem = _resolver_cep_origem(config=effective_config, cep_origem_informado=cep_origem)
    destino = _cep(str(dados.get("destino_cep", "") or ""))
    uf_destino_informada = str(dados.get("uf_destino", "") or "").strip().upper()
    if len(uf_destino_informada) != 2 or not uf_destino_informada.isalpha():
        uf_destino_informada = ""
    cnpj_destinatario = _digits(str(dados.get("cnpj_destinatario", "") or ""))
    try:
        peso = float(dados.get("peso", 0.0) or 0.0)
    except Exception:
        peso = 0.0
    try:
        valor = float(dados.get("valor", 0.0) or 0.0)
    except Exception:
        valor = 0.0

    if len(origem) != 8:
        _log_diag(f"CEP origem inválido: {origem}")
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes="CEP de origem inválido (use 8 dígitos)")]
    if len(destino) != 8:
        _log_diag(f"CEP destino inválido: {destino}")
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes="CEP de destino não encontrado nos pedidos")]
    if len(cnpj_destinatario) != 14:
        msg = "Cotação bloqueada: CNPJ do destinatário ausente ou inválido no romaneio."
        _log_diag(msg)
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)]
    if peso <= 0:
        msg = "Cotação bloqueada: peso total ausente ou inválido no romaneio."
        _log_diag(msg)
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)]

    cubagens_validas = _cubagens_validas(dados.get("cubagens"))
    if not cubagens_validas:
        msg = "Cotação bloqueada: romaneio sem cubagens válidas (tamanhos de caixa)."
        _log_diag(msg)
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)]

    try:
        volumes = int(dados.get("volumes", 0) or 0)
    except Exception:
        volumes = 0
    if volumes <= 0:
        volumes = sum(int(cub["quantidade"]) for cub in cubagens_validas)
    if volumes <= 0:
        msg = "Cotação bloqueada: quantidade de volumes inválida no romaneio."
        _log_diag(msg)
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)]
    volumes_cubagens = sum(int(cub["quantidade"]) for cub in cubagens_validas)
    if volumes_cubagens > 0 and volumes != volumes_cubagens:
        msg = (
            "Cotação bloqueada: volume total do romaneio diverge da soma das cubagens "
            f"(VOL={volumes} vs cubagens={volumes_cubagens})."
        )
        _log_diag(msg)
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)]

    try:
        cubagem_m3 = float(dados.get("cubagem_m3", 0.0) or 0.0)
    except Exception:
        cubagem_m3 = 0.0
    if cubagem_m3 <= 0:
        msg = "Cotação bloqueada: cubagem total ausente ou inválida no romaneio."
        _log_diag(msg)
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)]

    tasks: list[tuple[str, Any, dict[str, Any]]] = []  # (nome, provider, kwargs_coteir)
    erros_setup: list[ResultadoCotacao] = list(remote_skipped_results)
    uf_destino_cep = _cep_para_uf(destino)
    uf_destino = uf_destino_informada or uf_destino_cep
    if uf_destino_informada and uf_destino_cep and uf_destino_informada != uf_destino_cep:
        msg = (
            f"CEP de destino ({destino[:5]}-{destino[5:]}) pertence à UF {uf_destino_cep}, "
            f"mas o romaneio informa UF {uf_destino_informada}.\n\n"
            "Verifique se o CEP ou a cidade/UF do destinatário estão corretos no romaneio."
        )
        _log_diag(f"BLOQUEIO: divergência CEP/UF — {msg}")
        return [ResultadoCotacao(
            transportadora="GERAL",
            status="erro_divergencia_uf",
            detalhes=msg,
        )]
    _log_diag(
        f"Preparando cotações: origem={origem}, destino={destino}, peso={peso}, "
        f"valor={valor}, volumes={volumes}, cubagem={cubagem_m3:.4f}m³, "
        f"linhas_cubagem={len(cubagens_validas)}, UF={uf_destino or '?'}"
    )

    # BRASPRESS
    try:
        bcfg = provider_factory.get_provider_config("braspress")
        if bcfg.get("habilitado", True):
            if not _uf_atendida(bcfg.get("ufs_atendidas"), uf_destino):
                _log_diag(f"BRASPRESS ignorada (UF {uf_destino} não atendida)")
            else:
                cnpj = str(bcfg.get("cnpj", "")).strip()
                senha = str(bcfg.get("senha", "")).strip()
                if cnpj and senha:
                    headless_braspress = bool(bcfg.get("headless", True))
                    provider = await _obter_provider_sessao(
                        "braspress",
                        create_kwargs={"headless": headless_braspress},
                        desired_headless=headless_braspress,
                        log_label="BRASPRESS",
                    )
                    primeira_cub = cubagens_validas[0]
                    _log_diag(
                        f"BRASPRESS preparada (cnpj={cnpj[:6]}..., linhas_cubagem={len(cubagens_validas)}, "
                        f"headless={headless_braspress})"
                    )
                    _bp_kwargs = dict(
                        origem=origem,
                        destino=destino,
                        peso=peso,
                        valor=valor,
                        cnpj_destinatario=cnpj_destinatario,
                        volumes=volumes,
                        comprimento_cm=int(primeira_cub["comprimento_cm"]),
                        largura_cm=int(primeira_cub["largura_cm"]),
                        altura_cm=int(primeira_cub["altura_cm"]),
                        cubagens=cubagens_validas,
                    )
                    if cnpj_remetente:
                        _bp_kwargs["cnpj_remetente"] = cnpj_remetente
                        _bp_kwargs["tipo_frete"] = tipo_frete or "2"
                    tasks.append(("BRASPRESS", provider, _bp_kwargs))
                else:
                    _log_diag("BRASPRESS não configurada (CNPJ/senha ausentes)")
    except Exception as e:
        _log_diag(f"Erro ao preparar BRASPRESS: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="BRASPRESS", status="erro", detalhes=str(e)))

    # BAUER
    try:
        baucfg = provider_factory.get_provider_config("bauer")
        if baucfg.get("habilitado", True):
            if not provider_factory.is_available("bauer"):
                _log_diag("BAUER ignorada: provider bauer_auto não está disponível neste build")
            elif not _uf_atendida(baucfg.get("ufs_atendidas"), uf_destino):
                _log_diag(f"BAUER ignorada (UF {uf_destino} não atendida)")
            else:
                cotacao_url = str(baucfg.get("cotacao_url", "")).strip()
                bau_cnpj_pag = str(baucfg.get("cnpj_pagador", "")).strip()
                bau_cnpj_rem = str(baucfg.get("cnpj_remetente", "")).strip()
                cnpj_dest = cnpj_destinatario
                if cotacao_url and bau_cnpj_pag and bau_cnpj_rem and cnpj_dest:
                    cubagens_bauer = []
                    for cub in cubagens_validas:
                        qtd = int(cub["quantidade"])
                        if qtd <= 0:
                            continue
                        cubagens_bauer.append(
                            {
                                "quantidade": qtd,
                                "altura_m": int(cub["altura_cm"]) / 100.0,
                                "largura_m": int(cub["largura_cm"]) / 100.0,
                                "profundidade_m": int(cub["comprimento_cm"]) / 100.0,
                            }
                        )
                    if not cubagens_bauer:
                        msg = "BAUER bloqueada: romaneio sem cubagens válidas."
                        _log_diag(msg)
                        erros_setup.append(
                            ResultadoCotacao(transportadora="BAUER", status="erro", detalhes=msg)
                        )
                    else:
                        vol = sum(int(c["quantidade"]) for c in cubagens_bauer)
                        primeira = cubagens_bauer[0]
                        alt_m = float(primeira["altura_m"])
                        larg_m = float(primeira["largura_m"])
                        prof_m = float(primeira["profundidade_m"])
                        provider = await _obter_provider_sessao(
                            "bauer",
                            create_kwargs={
                                "cotacao_url": cotacao_url,
                                "cnpj_pagador": bau_cnpj_pag,
                                "cnpj_remetente": bau_cnpj_rem,
                                "cnpj_destinatario": cnpj_dest,
                                "headless": bool(baucfg.get("headless", True)),
                                "quantidade": vol,
                                "altura_m": alt_m,
                                "largura_m": larg_m,
                                "profundidade_m": prof_m,
                                "cubagens": cubagens_bauer,
                            },
                            log_label="BAUER",
                        )
                        provider.quantidade = vol
                        provider.altura_m = alt_m
                        provider.largura_m = larg_m
                        provider.profundidade_m = prof_m
                        if hasattr(provider, "cubagens"):
                            provider.cubagens = cubagens_bauer
                        if hasattr(provider, "cnpj_destinatario"):
                            provider.cnpj_destinatario = re.sub(r"\D", "", cnpj_dest or "")
                        _log_diag(
                            f"BAUER preparada: linhas_cubagem={len(cubagens_bauer)}, volumes={vol}"
                        )
                        _bauer_kwargs = dict(
                            origem=origem,
                            destino=destino,
                            peso=peso,
                            valor=valor,
                            cubagens=cubagens_bauer,
                        )
                        if cnpj_remetente:
                            provider.cnpj_remetente = re.sub(r"\D", "", cnpj_remetente)
                            provider.cnpj_destinatario = re.sub(r"\D", "", bau_cnpj_pag)
                            _bauer_kwargs["destino"] = _resolver_cep_origem(config, "")
                            _bauer_kwargs["tipo_frete"] = "fob"
                        tasks.append(("BAUER", provider, _bauer_kwargs))
                else:
                    _log_diag("BAUER não configurada (parâmetros ausentes)")
    except Exception as e:
        _log_diag(f"Erro ao preparar BAUER: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="BAUER", status="erro", detalhes=str(e)))

    # TRD
    try:
        tcfg = provider_factory.get_provider_config("trd")
        if tcfg.get("habilitado", True):
            if not _uf_atendida(tcfg.get("ufs_atendidas"), uf_destino):
                _log_diag(f"TRD ignorada (UF {uf_destino} não atendida)")
            else:
                email = str(tcfg.get("email", "")).strip()
                senha = str(tcfg.get("senha", "")).strip()
                if email and senha:
                    foco_trd = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "trd"
                    headless_trd = _trd_headless_config_value(tcfg, foco_trd)
                    provider = await _obter_provider_sessao(
                        "trd",
                        create_kwargs={"headless": headless_trd},
                        desired_headless=headless_trd,
                        log_label="TRD",
                    )
                    _log_diag(f"TRD preparada (headless={headless_trd})")
                    _trd_kwargs = dict(
                        origem=origem,
                        destino=destino,
                        peso=peso,
                        valor=valor,
                        volumes=volumes,
                        cubagens=cubagens_validas,
                        cnpj_destinatario=cnpj_destinatario,
                    )
                    if cnpj_remetente:
                        _trd_kwargs["cnpj_remetente"] = cnpj_remetente
                        _trd_kwargs["cep_remetente"] = origem
                    tasks.append(("TRD", provider, _trd_kwargs))
                else:
                    _log_diag("TRD não configurada (email/senha ausentes)")
    except Exception as e:
        _log_diag(f"Erro ao preparar TRD: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="TRD", status="erro", detalhes=str(e)))

    # AGEX — ignorada no modo fornecedor
    if cnpj_remetente:
        _log_diag("AGEX ignorada no modo fornecedor")
    else:
      try:
        if provider_factory.is_available("agex"):
            acfg = provider_factory.get_provider_config("agex")
            if acfg.get("habilitado", True):
                if (uf_destino or "").upper() in {"RS", "SC"}:
                    _log_diag(f"AGEX bloqueada para UF {uf_destino} (não atende este estado)")
                elif not _uf_atendida(acfg.get("ufs_atendidas"), uf_destino):
                    _log_diag(f"AGEX ignorada (UF {uf_destino} não atendida)")
                else:
                    email = str(acfg.get("email", "")).strip()
                    if not email:
                        legacy_login = str(acfg.get("cnpj", "")).strip()
                        if "@" in legacy_login:
                            email = legacy_login
                    senha = str(acfg.get("senha", "")).strip()
                    if email and senha:
                        foco_agex = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "agex"
                        headless_agex = False if foco_agex else bool(acfg.get("headless", True))
                        cnpj_cfg = str(acfg.get("cnpj", "")).strip()
                        cnpj_rem = _digits(str(acfg.get("cnpj_remetente", "")).strip() or cnpj_cfg)
                        cnpj_dest = cnpj_destinatario
                        descricao_mercadoria = str(acfg.get("descricao_mercadoria", "Mercadoria"))
                        tipo_produto = str(acfg.get("tipo_produto", "Artigos Esportivos"))
                        cubagens_agex = []
                        for cub in cubagens_validas:
                            qtd = int(cub["quantidade"])
                            c_cm = int(cub["comprimento_cm"])
                            l_cm = int(cub["largura_cm"])
                            a_cm = int(cub["altura_cm"])
                            cubagens_agex.append(
                                {
                                    "quantidade": qtd,
                                    "comprimento_m": c_cm / 100.0,
                                    "largura_m": l_cm / 100.0,
                                    "altura_m": a_cm / 100.0,
                                }
                            )
                        if not cubagens_agex:
                            msg = "AGEX bloqueada: romaneio sem tamanhos de caixa (cubagens) válidos."
                            _log_diag(msg)
                            erros_setup.append(ResultadoCotacao(transportadora="AGEX", status="erro", detalhes=msg))
                        else:
                            vol = sum(int(c["quantidade"]) for c in cubagens_agex)
                            primeira = cubagens_agex[0]
                            alt_m = float(primeira["altura_m"])
                            larg_m = float(primeira["largura_m"])
                            comp_m = float(primeira["comprimento_m"])
                            provider = await _obter_provider_sessao(
                                "agex",
                                create_kwargs={
                                    "cnpj": cnpj_cfg,
                                    "email": email,
                                    "senha": senha,
                                    "cnpj_remetente": cnpj_rem,
                                    "cnpj_destinatario": cnpj_dest,
                                    "cep_origem": origem,
                                    "cep_destino": destino,
                                    "descricao_mercadoria": descricao_mercadoria,
                                    "tipo_produto": tipo_produto,
                                    "volumes": vol,
                                    "altura_m": alt_m,
                                    "largura_m": larg_m,
                                    "comprimento_m": comp_m,
                                    "cubagens": cubagens_agex,
                                    "headless": headless_agex,
                                },
                                desired_headless=headless_agex,
                                log_label="AGEX",
                            )
                            # Sessão pré-logada: atualizar sempre os dados da carga corrente.
                            if hasattr(provider, "atualizar_carga"):
                                provider.atualizar_carga(
                                    volumes=vol,
                                    altura_m=alt_m,
                                    largura_m=larg_m,
                                    comprimento_m=comp_m,
                                    cnpj_remetente=cnpj_rem,
                                    cnpj_destinatario=cnpj_dest,
                                    cep_origem=origem,
                                    cep_destino=destino,
                                    descricao_mercadoria=descricao_mercadoria,
                                    tipo_produto=tipo_produto,
                                    cubagens=cubagens_agex,
                                )
                            _log_diag(
                                f"AGEX preparada: peso={peso:.3f}kg, vol={vol}, "
                                f"dims={comp_m:.2f}x{larg_m:.2f}x{alt_m:.2f}m, "
                                f"linhas_cubagem={len(cubagens_agex)}, headless={headless_agex}"
                            )
                            _agex_kwargs = dict(
                                origem=cnpj_rem,
                                destino=cnpj_dest,
                                peso=peso,
                                valor=valor,
                            )
                            tasks.append(("AGEX", provider, _agex_kwargs))
                    else:
                        _log_diag("AGEX não configurada (email/senha ausentes)")
      except Exception as e:
        _log_diag(f"Erro ao preparar AGEX: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="AGEX", status="erro", detalhes=str(e)))

    # Eucatur (SSW)
    try:
        if provider_factory.is_available("eucatur"):
            ecfg = provider_factory.get_provider_config("eucatur")
            if ecfg.get("habilitado", True):
                if not _uf_atendida(ecfg.get("ufs_atendidas"), uf_destino):
                    _log_diag(f"Eucatur ignorada (UF {uf_destino} não atendida)")
                else:
                    dominio = str(ecfg.get("dominio", "")).strip()
                    usuario = str(ecfg.get("usuario", "")).strip()
                    senha_euc = str(ecfg.get("senha", "")).strip()
                    if dominio and usuario and senha_euc:
                        foco_eucatur = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "eucatur"
                        headless_eucatur = False if foco_eucatur else bool(ecfg.get("headless", True))
                        provider = await _obter_provider_sessao(
                            "eucatur",
                            create_kwargs={"headless": headless_eucatur},
                            desired_headless=headless_eucatur,
                            log_label="EUCATUR",
                        )
                        _log_diag(f"EUCATUR preparada (headless={headless_eucatur})")
                        _euc_kwargs = dict(
                            origem=origem,
                            destino=destino,
                            peso=peso,
                            valor=valor,
                            volumes=volumes,
                            cubagem_m3=cubagem_m3,
                            cubagens=cubagens_validas,
                            cnpj_remetente="40223106000179",
                            cnpj_destinatario=cnpj_destinatario,
                        )
                        if cnpj_remetente:
                            _euc_kwargs["cnpj_pagador"] = "40223106000179"
                            _euc_kwargs["cnpj_remetente"] = cnpj_remetente
                            _euc_kwargs["cnpj_destinatario"] = "40223106000179"
                            _euc_kwargs["destino"] = _resolver_cep_origem(config, "")
                            _euc_kwargs["tipo_frete"] = "2"
                        tasks.append(("EUCATUR", provider, _euc_kwargs))
                    else:
                        _log_diag("Eucatur não configurada (domínio/usuário/senha ausentes)")
    except Exception as e:
        _log_diag(f"Erro ao preparar Eucatur: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="EUCATUR", status="erro", detalhes=str(e)))

    # Rodonaves (SSW) — ignorada no modo fornecedor
    if cnpj_remetente:
        _log_diag("RODONAVES ignorada no modo fornecedor")
    else:
        try:
            if provider_factory.is_available("rodonaves"):
                rcfg = provider_factory.get_provider_config("rodonaves")
                if rcfg.get("habilitado", True):
                    if not _uf_atendida(rcfg.get("ufs_atendidas"), uf_destino):
                        _log_diag(f"Rodonaves ignorada (UF {uf_destino} não atendida)")
                    else:
                        dominio = str(rcfg.get("dominio", "RTE") or "RTE").strip()
                        usuario = str(rcfg.get("usuario", "")).strip()
                        senha = str(rcfg.get("senha", "")).strip()
                        cnpj_pagador = _digits(str(rcfg.get("cnpj_pagador", "") or ""))
                        if dominio and usuario and senha and len(cnpj_pagador) == 14:
                            foco_rodonaves = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "rodonaves"
                            headless_rodonaves = False if foco_rodonaves else bool(rcfg.get("headless", True))
                            provider = await _obter_provider_sessao(
                                "rodonaves",
                                create_kwargs={
                                    "dominio": dominio,
                                    "usuario": usuario,
                                    "senha": senha,
                                    "cnpj_pagador": cnpj_pagador,
                                    "login_url": str(rcfg.get("login_url", "") or "").strip(),
                                    "cotacao_url": str(rcfg.get("cotacao_url", "") or "").strip(),
                                    "headless": headless_rodonaves,
                                },
                                desired_headless=headless_rodonaves,
                                log_label="RODONAVES",
                            )
                            _log_diag(f"RODONAVES preparada (headless={headless_rodonaves})")
                            _rodo_kwargs = dict(
                                origem=origem,
                                destino=destino,
                                peso=peso,
                                valor=valor,
                                volumes=volumes,
                                cubagem_m3=cubagem_m3,
                                cubagens=cubagens_validas,
                                cnpj_remetente=cnpj_pagador,
                                cnpj_destinatario=cnpj_destinatario,
                                preencher_cep_origem=bool(_cep(cep_origem)),
                            )
                            tasks.append(("RODONAVES", provider, _rodo_kwargs))
                        else:
                            _log_diag("RODONAVES não configurada (domínio/usuário/senha/cnpj_pagador ausentes)")
        except Exception as e:
            _log_diag(f"Erro ao preparar RODONAVES: {e}")
            erros_setup.append(ResultadoCotacao(transportadora="RODONAVES", status="erro", detalhes=str(e)))

    # Alfa
    try:
        if provider_factory.is_available("alfa"):
            alcfg = provider_factory.get_provider_config("alfa")
            if alcfg.get("habilitado", True):
                descricoes_itens = dados.get("descricoes_itens", [])
                if any("PICOLO" in d.upper() for d in descricoes_itens):
                    _log_diag("ALFA ignorada (item PICOLO encontrado no romaneio)")
                elif not _uf_atendida(alcfg.get("ufs_atendidas"), uf_destino):
                    _log_diag(f"ALFA ignorada (UF {uf_destino} não atendida)")
                else:
                    login = str(alcfg.get("login", "") or "").strip()
                    senha = str(alcfg.get("senha", "") or "").strip()
                    cnpj_rem = str(alcfg.get("cnpj_remetente", "") or "").strip()
                    if login and senha and cnpj_rem:
                        headless_alfa = bool(alcfg.get("headless", False))
                        provider = await _obter_provider_sessao(
                            "alfa",
                            create_kwargs={
                                "login": login,
                                "senha": senha,
                                "login_url": str(alcfg.get("login_url", "") or "").strip(),
                                "cotacao_url": str(alcfg.get("cotacao_url", "") or "").strip(),
                                "headless": headless_alfa,
                            },
                            desired_headless=headless_alfa,
                            log_label="ALFA",
                        )
                        _log_diag(f"ALFA preparada (headless={headless_alfa})")
                        _alfa_kwargs = dict(
                            origem=origem,
                            destino=destino,
                            peso=peso,
                            valor=valor,
                            volumes=volumes,
                            cubagem_m3=cubagem_m3,
                            cubagens=cubagens_validas,
                            cnpj_remetente=cnpj_rem,
                            cnpj_destinatario=cnpj_destinatario,
                        )
                        if cnpj_remetente:
                            _alfa_kwargs["cnpj_remetente"] = cnpj_remetente
                            _alfa_kwargs["cnpj_destinatario"] = cnpj_rem
                            _alfa_kwargs["destino"] = _resolver_cep_origem(config, "")
                            _alfa_kwargs["tipo_pagador"] = "2"
                        tasks.append(("ALFA", provider, _alfa_kwargs))
                    else:
                        _log_diag("ALFA não configurada (login/senha/cnpj_remetente ausentes)")
    except Exception as e:
        _log_diag(f"Erro ao preparar ALFA: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="ALFA", status="erro", detalhes=str(e)))

    # COOPEX (SSW)
    try:
        if provider_factory.is_available("coopex"):
            cocfg = provider_factory.get_provider_config("coopex")
            if cocfg.get("habilitado", True):
                if not _uf_atendida(cocfg.get("ufs_atendidas"), uf_destino):
                    _log_diag(f"COOPEX ignorada (UF {uf_destino} não atendida)")
                else:
                    dominio = str(cocfg.get("dominio", "")).strip()
                    usuario = str(cocfg.get("usuario", "")).strip()
                    senha_co = str(cocfg.get("senha", "")).strip()
                    if dominio and usuario and senha_co:
                        foco_coopex = str(MODO_FOCO_TRANSPORTADORA).strip().lower() == "coopex"
                        headless_coopex = False if foco_coopex else bool(cocfg.get("headless", True))
                        provider = await _obter_provider_sessao(
                            "coopex",
                            create_kwargs={"headless": headless_coopex},
                            desired_headless=headless_coopex,
                            log_label="COOPEX",
                        )
                        _log_diag(f"COOPEX preparada (headless={headless_coopex})")
                        _co_kwargs = dict(
                            origem=origem,
                            destino=destino,
                            peso=peso,
                            valor=valor,
                            volumes=volumes,
                            cubagem_m3=cubagem_m3,
                            cubagens=cubagens_validas,
                            cnpj_remetente="40223106000179",
                            cnpj_destinatario=cnpj_destinatario,
                        )
                        if cnpj_remetente:
                            _co_kwargs["cnpj_pagador"] = "40223106000179"
                            _co_kwargs["cnpj_remetente"] = cnpj_remetente
                            _co_kwargs["cnpj_destinatario"] = "40223106000179"
                            _co_kwargs["destino"] = _resolver_cep_origem(config, "")
                            _co_kwargs["tipo_frete"] = "2"
                        tasks.append(("COOPEX", provider, _co_kwargs))
                    else:
                        _log_diag("COOPEX não configurada (domínio/usuário/senha ausentes)")
    except Exception as e:
        _log_diag(f"Erro ao preparar COOPEX: {e}")
        erros_setup.append(ResultadoCotacao(transportadora="COOPEX", status="erro", detalhes=str(e)))

    # Executa primeiro as transportadoras mais lentas para reduzir tempo total.
    # Maior número = tendência de maior duração (baseado em testes reais).
    tasks.sort(key=lambda t: _PRIORIDADE_LENTIDAO.get(str(t[0]).upper(), 0), reverse=True)

    # Cotações em paralelo (configurável, padrão 3)
    fb_cfg = config.get("fretio", {}) if isinstance(config, dict) else {}
    max_paralelo = max(1, min(7, int(fb_cfg.get("max_paralelo", 3) or 3)))
    nomes_tasks = ", ".join(nome for nome, _provider, _kwargs in tasks)
    _log_diag(f"Executando {len(tasks)} cotações em paralelo (máx {max_paralelo}): {nomes_tasks}")
    resultados: list[ResultadoCotacao] = []
    total_cotacoes = len(tasks) + len(erros_setup)
    concluidas = 0
    if total_cotacoes > 0:
        _emitir_progresso(concluidas=concluidas, total=total_cotacoes)
    for erro_setup in erros_setup:
        resultados.append(erro_setup)
        concluidas += 1
        _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=erro_setup)
    semaforo = asyncio.Semaphore(max_paralelo)

    async def _run_cotacao(i: int, nome: str, provider: Any, kwargs: dict[str, Any], is_alfa: bool):
        effective_timeout = _TIMEOUT_COTACAO_S.get(nome.upper(), _TIMEOUT_COTACAO_PADRAO_S)
        started_at = time.monotonic()

        def _duration_ms() -> int:
            return int((time.monotonic() - started_at) * 1000)

        try:
            coro = provider.coteir(**kwargs)
            cotacao = await asyncio.wait_for(coro, timeout=effective_timeout)
            return i, nome, provider, kwargs, cotacao, None, _duration_ms()
        except asyncio.TimeoutError:
            last_step = getattr(provider, '_passo_atual', 'desconhecido')
            return i, nome, provider, kwargs, None, TimeoutError(
                f"Timeout de {effective_timeout}s na cotação {nome} (passo: {last_step})"
            ), _duration_ms()
        except asyncio.CancelledError as exc:
            detalhe = str(exc).strip() or "sem detalhe"
            return i, nome, provider, kwargs, None, RuntimeError(
                f"Cotação {nome} cancelada: {detalhe}"
            ), _duration_ms()
        except Exception as exc:
            return i, nome, provider, kwargs, None, exc, _duration_ms()

    async def _exec(i: int, nome: str, provider: Any, kwargs: dict[str, Any]):
        is_alfa = nome.upper() == "ALFA"
        # `async with semaforo` garante release mesmo se a Task for cancelada
        # exatamente entre o retorno de acquire() e o try interno — janela
        # cancellation-unsafe que existia no padrão acquire/try/finally.
        # ALFA continua fora do semáforo (login manual com Turnstile).
        if is_alfa:
            return await _run_cotacao(i, nome, provider, kwargs, is_alfa)
        async with semaforo:
            _log_diag(f"Semáforo adquirido: {nome} (posição {i})")
            return await _run_cotacao(i, nome, provider, kwargs, is_alfa)

    def _processar_resultado(res, resultados, falhas_para_retry):
        """Processa resultado de _exec, retorna (ResultadoCotacao|None, ok: bool)."""
        nonlocal concluidas

        if not isinstance(res, tuple) or len(res) != 7:
            msg = f"Executor retornou formato inesperado de resultado: {type(res).__name__}"
            _log_diag(msg)
            r = ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=msg)
            concluidas += 1
            resultados.append(r)
            _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
            return

        _i, nome_task, provider_task, kwargs_task, cotacao, erro, duration_ms = res

        if isinstance(erro, BaseException):
            erro_str = str(erro)
            # Erros de negócio não devem ser reportados nem gerar retry
            if _is_business_error(erro_str):
                _log_diag(f"{nome_task}: destino não atendido (erro de negócio, ignorando)")
                r = ResultadoCotacao(
                    transportadora=nome_task, status="nao_atendido", detalhes=erro_str,
                    duration_ms=duration_ms,
                )
                concluidas += 1
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
                return
            import traceback
            tb = ''.join(traceback.format_exception(type(erro), erro, erro.__traceback__))
            _log_diag(f"Erro em cotação {nome_task}: {type(erro).__name__}: {erro}\n{tb}")
            # Falhas transitórias de provider (timeout, rede, browser fechado) são esperadas
            # e não devem poluir a API de erros com ruído técnico.
            reported_structured = False
            if _is_rodonaves_quotation_goto_timeout(nome_task, erro_str):
                _report_rodonaves_goto_timeout(
                    provider_task,
                    kwargs_task,
                    detail=erro_str,
                    duration_ms=duration_ms,
                    uf_destino=uf_destino,
                )
                reported_structured = True
            if not reported_structured and not _is_expected_transient_failure(erro):
                report_error(type(erro), erro, erro.__traceback__, context=f"cotacao_{nome_task}")
            if falhas_para_retry is not None:
                falhas_para_retry.append((nome_task, provider_task, kwargs_task))
                _log_diag(f"{nome_task} enfileirada para retry após as demais completarem")
            else:
                r = ResultadoCotacao(
                    transportadora=nome_task, status="erro",
                    detalhes=f"{type(erro).__name__}: {erro}",
                    duration_ms=duration_ms,
                )
                concluidas += 1
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
            return

        if erro is not None:
            erro_str = str(erro)
            # Erros de negócio não devem ser reportados nem gerar retry
            if _is_business_error(erro_str):
                _log_diag(f"{nome_task}: destino não atendido (erro de negócio, ignorando)")
                r = ResultadoCotacao(
                    transportadora=nome_task, status="nao_atendido", detalhes=erro_str,
                    duration_ms=duration_ms,
                )
                concluidas += 1
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
                return
            _log_diag(f"Erro em cotação {nome_task}: {erro}")
            if falhas_para_retry is not None:
                falhas_para_retry.append((nome_task, provider_task, kwargs_task))
                _log_diag(f"{nome_task} enfileirada para retry após as demais completarem")
            else:
                r = ResultadoCotacao(
                    transportadora=nome_task, status="erro", detalhes=str(erro),
                    duration_ms=duration_ms,
                )
                concluidas += 1
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
            return

        if cotacao is not None:
            try:
                transportadora = str(getattr(cotacao, "transportadora", nome_task))
                valor_frete = float(getattr(cotacao, "valor_frete", 0.0))
                prazo_dias = int(getattr(cotacao, "prazo_dias", 0))
                detalhes = getattr(cotacao, "restricoes", None)
            except Exception as parse_exc:
                _log_diag(f"Resultado inválido em {nome_task}: {parse_exc}")
                r = ResultadoCotacao(
                    transportadora=nome_task, status="erro",
                    detalhes=f"Resultado inválido: {parse_exc}",
                    duration_ms=duration_ms,
                )
                concluidas += 1
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
                return

            r = ResultadoCotacao(
                transportadora=transportadora, status="ok",
                valor_frete=valor_frete, prazo_dias=prazo_dias, detalhes=detalhes,
                duration_ms=duration_ms,
            )
            resultados.append(r)
            concluidas += 1
            _log_diag(f"✅ {transportadora}: R$ {valor_frete:.2f} - {prazo_dias} dias")
            _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
        else:
            detalhe = None
            if provider_task is not None:
                detalhe = getattr(provider_task, "last_error", None)
            if detalhe:
                _log_diag(f"{nome_task} retornou None: {detalhe}")
            else:
                _log_diag(f"{nome_task} retornou None (sem resultado)")
                detalhe = "Sem resultado"
            # Erros de negócio (destino não atendido) são normais:
            # não reportar, não fazer retry, apenas registrar como "não atendido"
            if _is_business_error(detalhe):
                _log_diag(f"{nome_task}: destino não atendido (erro de negócio, ignorando)")
                r = ResultadoCotacao(
                    transportadora=nome_task, status="nao_atendido", detalhes=str(detalhe),
                    duration_ms=duration_ms,
                )
                concluidas += 1
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
                return

            # Falhas transitórias de rede/browser capturadas internamente pelo provider
            # (retornaram None em vez de levantar exceção) — não reportar à API,
            # mas agendar retry exatamente como fazemos para exceções transitórias.
            if _is_expected_transient_failure_str(detalhe or ""):
                _log_diag(f"{nome_task} falha transitória (sem report): {detalhe}")
                if _is_rodonaves_quotation_goto_timeout(nome_task, detalhe):
                    _report_rodonaves_goto_timeout(
                        provider_task,
                        kwargs_task,
                        detail=detalhe,
                        duration_ms=duration_ms,
                        uf_destino=uf_destino,
                    )
                if falhas_para_retry is not None:
                    falhas_para_retry.append((nome_task, provider_task, kwargs_task))
                    _log_diag(f"{nome_task} enfileirada para retry (transitória)")
                else:
                    r = ResultadoCotacao(
                        transportadora=nome_task, status="erro", detalhes=str(detalhe),
                        duration_ms=duration_ms,
                    )
                    concluidas += 1
                    resultados.append(r)
                    _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)
                return

            # Normaliza a mensagem removendo partes variáveis (ex: paths de diagnóstico TRD)
            # para que o rate-limiter do error_reporter deduplique corretamente entre execuções.
            detalhe_report = re.sub(r'\s*\(diagnóstico salvo em:[^)]*\)', '', str(detalhe or "")).strip()
            if not detalhe_report:
                detalhe_report = str(detalhe or "Sem resultado")
            report_error_message(f"{nome_task} retornou None: {detalhe_report}", context=f"cotacao_{nome_task}")
            if falhas_para_retry is not None:
                falhas_para_retry.append((nome_task, provider_task, kwargs_task))
                _log_diag(f"{nome_task} enfileirada para retry após as demais completarem")
            else:
                r = ResultadoCotacao(
                    transportadora=nome_task, status="erro", detalhes=str(detalhe),
                    duration_ms=duration_ms,
                )
                concluidas += 1
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)

    def _is_business_error(detail: str) -> bool:
        """Detecta erros de negócio (destino não atendido, rota fora de cobertura).

        Esses erros são normais e não devem ser reportados nem gerar retry."""
        if not detail:
            return False
        d = str(detail).lower()
        patterns = (
            "destino fora da cobertura",
            "cepdestino não atendido",
            "cep destino não atendido",
            "não atendemos esse cep",
            "destino possivelmente não atendido",
            "destino possìvelmente não atendido",
            "rota não atendida",
            "cidade de destino",
            "transportadora não atende",
            "transportadora n o atende",
            "cidade de destino n o",
            "n o atendida",
            "não atendido",
            "nao atendido",
            "fora de cobertura",
            "fora da cobertura",
            "não atendemos",
            "cepnão atendemos",
            "sem precificação automática no ssw",
            "sem precificacao automatica no ssw",
            "não cadastrada",
            "nao cadastrada",
            "rota:",
            # Limitação estrutural Eucatur/SSW — não é erro técnico
            "não suporta mais de 11 volumes",
            "nao suporta mais de 11 volumes",
        )
        return any(p in d for p in patterns)

    _TRANSIENT_PATTERNS = (
        "target page, context or browser has been closed",
        "target closed",
        "frame was detached",
        "net::err_aborted",
        "net::err_connection",
        "net::err_name",
        "net::err_timed_out",
        "net::err_internet",
        "net::err_network",
        "formulário de cotação não carregou",
        "formulario de cotacao nao carregou",
        "page.goto",
        "valor de frete nao encontrado",
        "valor de frete não encontrado",
        "timeout aguardando resultado",
    )

    def _is_expected_transient_failure(erro: BaseException) -> bool:
        """Detecta falhas transitórias esperadas de provider que NÃO devem ir para report_error.

        Timeouts do provider e erros de rede/browser são falhas controladas — não bugs no código."""
        if isinstance(erro, TimeoutError):
            return True
        err_str = str(erro).lower()
        return any(p in err_str for p in _TRANSIENT_PATTERNS)

    def _is_expected_transient_failure_str(detail: str) -> bool:
        """Mesmos critérios de _is_expected_transient_failure, mas para strings de last_error.

        Usado quando o provider capturou a exceção internamente e retornou None."""
        if not detail:
            return False
        d = detail.lower()
        if "timeout" in d or "timed out" in d:
            return True
        return any(p in d for p in _TRANSIENT_PATTERNS)

    # ── Rodada 1: executa todas as cotações ──
    falhas_para_retry: list[tuple[str, Any, dict[str, Any]]] = []
    futuros = []
    for i, (nome, prov, kwargs) in enumerate(tasks):
        t = asyncio.ensure_future(_exec(i, nome, prov, kwargs))
        futuros.append(t)

    for fut in asyncio.as_completed(futuros):
        try:
            res = await fut
            _processar_resultado(res, resultados, falhas_para_retry)
        except Exception as loop_exc:
            import traceback
            tb = ''.join(traceback.format_exception(type(loop_exc), loop_exc, loop_exc.__traceback__))
            _log_diag(f"Falha ao processar resultado de cotação: {loop_exc}\n{tb}")
            concluidas += 1
            r = ResultadoCotacao(
                transportadora="GERAL", status="erro",
                detalhes=f"Falha interna ao processar cotação: {loop_exc}",
            )
            resultados.append(r)
            _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)

    # ── Rodada 2: retry das que falharam (máx 1 retry, sem enfileirar de novo) ──
    if falhas_para_retry:
        nomes_retry = ", ".join(n for n, _, _ in falhas_para_retry)
        total_cotacoes += len(falhas_para_retry)
        _log_diag(f"Retentando {len(falhas_para_retry)} cotação(ões) que falharam: {nomes_retry}")
        _emitir_progresso(concluidas=concluidas, total=total_cotacoes)

        futuros_retry = []
        for i, (nome, prov, kwargs) in enumerate(falhas_para_retry):
            t = asyncio.ensure_future(_exec(i, nome, prov, kwargs))
            futuros_retry.append(t)

        for fut in asyncio.as_completed(futuros_retry):
            try:
                res = await fut
                _processar_resultado(res, resultados, None)  # None = não enfileira de novo
            except Exception as loop_exc:
                import traceback
                tb = ''.join(traceback.format_exception(type(loop_exc), loop_exc, loop_exc.__traceback__))
                _log_diag(f"Falha ao processar retry de cotação: {loop_exc}\n{tb}")
                concluidas += 1
                r = ResultadoCotacao(
                    transportadora="GERAL", status="erro",
                    detalhes=f"Falha interna no retry: {loop_exc}",
                )
                resultados.append(r)
                _emitir_progresso(concluidas=concluidas, total=total_cotacoes, resultado=r)

    # Cleanup de providers criados ad-hoc (quando sessao=None)
    if sessao is None and tasks:
        async def _cleanup_adhoc(nome: str, prov):
            try:
                await asyncio.wait_for(prov.cleanup(), timeout=8)
                _log_diag(f"Cleanup ad-hoc {nome} OK")
            except Exception as e:
                _log_diag(f"Cleanup ad-hoc {nome} falhou: {e}")
        cleanup_tasks = [_cleanup_adhoc(n, p) for n, p, _ in tasks]
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)

    validas = [r for r in resultados if r.status == "ok" and r.valor_frete is not None]
    _log_diag(f"Cotações válidas: {len(validas)} de {len(tasks)}")

    return resultados


async def cotar_transportadoras(
    *,
    extrator,
    pedidos: list[Any],
    cep_origem: str = "",
    config_path: Path | None = None,
    sessao: TransportadoraSession | None = None,
    progresso_callback: "Callable[[dict[str, Any]], None] | None" = None,
) -> list[ResultadoCotacao]:
    """Executa cotação em todas as transportadoras configuradas."""
    config = sessao.config if sessao else _carregar_config(config_path=config_path)
    started_at = time.monotonic()
    dados: dict[str, Any] | None = None
    resultados: list[ResultadoCotacao] | None = None
    job_payload = _quotation_job_start_payload(
        config,
        modo="pdf",
        quantidade_pedidos=len(pedidos or []),
    )
    job_id = await asyncio.to_thread(_create_quotation_job_best_effort, "romaneio", job_payload)
    report_quotation_started(metadata=_quotation_usage_metadata(None, modo="pdf", job_id=job_id))
    cancelled = False
    try:
        dados = _dados_envio(extrator=extrator, pedidos=pedidos)
        if not dados:
            _log_diag("Sem dados de envio para cotação")
            resultados = [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes="Nenhum pedido disponível para cotação")]
            return resultados
        run_shadow_normalization(
            "romaneio",
            config,
            dados,
            cep_origem=cep_origem,
            modo="pdf",
            log_func=_log_diag,
        )
        resultados = await _executar_cotacoes_com_dados(
            config=config,
            dados=dados,
            cep_origem=cep_origem,
            sessao=sessao,
            progresso_callback=progresso_callback,
        )
        return resultados
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        _report_quotation_usage_results(
            config=config,
            dados=dados,
            resultados=resultados,
            modo="pdf",
            duration_ms=duration_ms,
            job_id=job_id,
        )
        general_error = _quotation_results_indicate_general_error(resultados)
        job_status = "cancelled" if cancelled else ("error" if general_error else "finished")
        _finish_quotation_job_best_effort(
            job_id,
            status=job_status,
            result=_quotation_job_result_payload(config, resultados),
            error_message=_quotation_job_error_message(resultados) if general_error else None,
        )


async def cotar_transportadoras_romaneio_colado(
    *,
    romaneio_colado: str,
    cep_origem: str = "",
    config_path: Path | None = None,
    sessao: "TransportadoraSession | None" = None,
    progresso_callback: "Callable[[dict[str, Any]], None] | None" = None,
    cnpj_remetente: str = "",
    tipo_frete: str = "",
) -> list[ResultadoCotacao]:
    config = sessao.config if sessao else _carregar_config(config_path=config_path)
    started_at = time.monotonic()
    dados: dict[str, Any] | None = None
    resultados: list[ResultadoCotacao] | None = None
    modo = "fornecedor" if cnpj_remetente else "romaneio_colado"
    job_payload = _quotation_job_start_payload(
        config,
        modo=modo,
        quantidade_linhas=_count_non_empty_lines(romaneio_colado),
    )
    job_id = await asyncio.to_thread(_create_quotation_job_best_effort, "manual", job_payload)
    report_quotation_started(metadata=_quotation_usage_metadata(None, modo=modo, job_id=job_id))
    cancelled = False
    try:
        dados = _dados_envio_romaneio_colado(romaneio_colado)
    except ValueError as e:
        _log_diag(f"Romaneio colado inválido: {e}")
        resultados = [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=str(e))]
        duration_ms = int((time.monotonic() - started_at) * 1000)
        _report_quotation_usage_results(
            config=config,
            dados=dados,
            resultados=resultados,
            modo=modo,
            duration_ms=duration_ms,
            job_id=job_id,
        )
        _finish_quotation_job_best_effort(
            job_id,
            status="error",
            result=_quotation_job_result_payload(config, resultados),
            error_message=_quotation_job_error_message(resultados),
        )
        return resultados
    try:
        run_shadow_normalization(
            "manual",
            config,
            dados,
            cep_origem=cep_origem,
            modo=modo,
            log_func=_log_diag,
        )
        resultados = await _executar_cotacoes_com_dados(
            config=config,
            dados=dados,
            cep_origem=cep_origem,
            sessao=sessao,
            progresso_callback=progresso_callback,
            cnpj_remetente=cnpj_remetente,
            tipo_frete=tipo_frete,
        )
        return resultados
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        _report_quotation_usage_results(
            config=config,
            dados=dados,
            resultados=resultados,
            modo=modo,
            duration_ms=duration_ms,
            job_id=job_id,
        )
        general_error = _quotation_results_indicate_general_error(resultados)
        job_status = "cancelled" if cancelled else ("error" if general_error else "finished")
        _finish_quotation_job_best_effort(
            job_id,
            status=job_status,
            result=_quotation_job_result_payload(config, resultados),
            error_message=_quotation_job_error_message(resultados) if general_error else None,
        )


async def diagnosticar_transportadoras(
    *,
    destino_cep: str,
    cnpj_destinatario: str,
    peso: float,
    valor: float,
    volumes: int = 1,
    cep_origem: str = "",
    config_path: Path | None = None,
    progresso_callback: "Callable[[dict[str, Any]], None] | None" = None,
) -> list[ResultadoCotacao]:
    config = _carregar_config(config_path=config_path)
    dados = {
        "destino_cep": _cep(destino_cep),
        "cnpj_destinatario": _digits(cnpj_destinatario),
        "peso": float(peso),
        "valor": float(valor),
        "volumes": int(volumes or 1),
    }
    return await _executar_cotacoes_com_dados(
        config=config,
        dados=dados,
        cep_origem=cep_origem,
        progresso_callback=progresso_callback,
    )


def formatar_resultados_cotacao(resultados: list[ResultadoCotacao]) -> str:
    linhas: list[str] = []

    # Verificar erros de divergência CEP/UF (bloqueio)
    for r in resultados:
        if r.status == "erro_divergencia_uf":
            linhas.append(f"COTACAO BLOQUEADA:\n{r.detalhes}")
            return "\n".join(linhas)

    validas = sorted(
        [r for r in resultados if r.status == "ok" and r.valor_frete is not None],
        key=lambda r: (float(r.valor_frete or 0.0), int(r.prazo_dias or 0), r.transportadora),
    )
    desabilitadas = [r for r in resultados if r.status == "desabilitada"]
    for item in validas:
        val = f"{item.valor_frete:.2f}".replace(".", ",")
        linhas.append(
            f"{item.transportadora}   R$ {val}   {item.prazo_dias} dia(s)"
        )

    if validas:
        melhor = validas[0]
        val_melhor = f"{melhor.valor_frete:.2f}".replace(".", ",")
        linhas.append("")
        linhas.append(f"Melhor frete: {melhor.transportadora}   R$ {val_melhor}")
    else:
        linhas.append("Nenhuma cotacao valida retornada")
        if _diag_log_enabled():
            linhas.append("Diagnostico: verifique o arquivo romaneio_cotacao.log")

    if desabilitadas:
        linhas.append("")
        linhas.append("Transportadoras ignoradas:")
        for item in desabilitadas:
            detalhe = item.detalhes or CARRIER_DISABLED_MESSAGE
            linhas.append(f"- {item.transportadora}: {detalhe}")

    return "\n".join(linhas)


import traceback


def setup_global_exception_handler():
    """Configura um manipulador global de exceções para logar erros não tratados."""
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        error_message = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        _log_diag(f"Unhandled exception:\n{error_message}")
        try:
            import logging
            logging.getLogger("unhandled").critical("Exceção não tratada:\n%s", error_message)
        except Exception:
            pass

    sys.excepthook = handle_exception

    # Captura exceções em coroutines asyncio que não são awaited
    def _asyncio_exception_handler(loop, context):
        msg = context.get("message", "")
        exc = context.get("exception")
        details = f"asyncio exception: {msg}"
        if exc:
            details += f"\n{''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}"
        _log_diag(details)
        try:
            import logging
            logging.getLogger("asyncio").error(details)
        except Exception:
            pass

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_asyncio_exception_handler)
    except Exception:
        pass


def formatar_resultados_diagnostico(resultados: list[ResultadoCotacao]) -> str:
    linhas: list[str] = []
    linhas.append("=== DIAGNÓSTICO DE COTAÇÕES ===")

    validas = sorted(
        [r for r in resultados if r.status == "ok" and r.valor_frete is not None],
        key=lambda r: (float(r.valor_frete or 0.0), int(r.prazo_dias or 0), r.transportadora),
    )
    invalidas = [r for r in resultados if not (r.status == "ok" and r.valor_frete is not None)]

    if validas:
        linhas.append("- Válidas:")
        for item in validas:
            linhas.append(
                f"  * {item.transportadora}: R$ {item.valor_frete:.2f} | {item.prazo_dias} dia(s)"
                + (f" | {item.detalhes}" if item.detalhes else "")
            )
    else:
        linhas.append("- Válidas: nenhuma")

    if invalidas:
        linhas.append("- Falhas/Não configuradas:")
        for item in invalidas:
            linhas.append(
                f"  * {item.transportadora}: {item.status}"
                + (f" | {item.detalhes}" if item.detalhes else "")
            )

    return "\n".join(linhas)
