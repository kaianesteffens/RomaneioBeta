"""Cotação de transportadoras para integração com romaneio."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
import asyncio
from datetime import datetime
import inspect
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
    from error_reporter import report_error, report_error_message, report_error_payload
except Exception:
    def report_error(*a, **kw): pass
    def report_error_message(*a, **kw): pass
    def report_error_payload(*a, **kw): pass

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
    from remote_config import apply_safe_runtime_overrides
except Exception:
    def apply_safe_runtime_overrides(config):
        return dict(config) if isinstance(config, dict) else {}

try:
    from remote_permissions import (
        CARRIER_DISABLED_MESSAGE,
        KNOWN_CARRIERS,
        carrier_enabled_or_message,
        normalize_carrier_name,
    )
except Exception:
    CARRIER_DISABLED_MESSAGE = "Esta transportadora foi desabilitada pela configuração da licença."
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
from fretio.quotation_contract import (
    QuoteResponse,
    quote_request_from_legacy_kwargs,
    quote_response_to_resultado_cotacao,
)


CEP_ORIGEM_PADRAO = "99740000"
MODO_FOCO_TRANSPORTADORA = ""  # Vazio = sem foco; cota todas as transportadoras habilitadas.
_CONFIG_FALLBACK = """[fretio]
fator_cubagem = 6000
cache_dir = "cache"

[romaneio]
cep_origem = "99740000"
cnpj_pagador_padrao = ""

[transportadoras.braspress]
habilitado = true
cnpj = ""
senha = ""
ufs_atendidas = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]

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
headless = false
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

[transportadoras.translovato]
habilitado = false
cnpj = ""
usuario = ""
senha = ""
cnpj_remetente = ""
produto = "CONFECCAO"
headless = true
cotacao_url = "https://www.translovato.com.br/fale-conosco/solicitacao-de-cotacao#portal-do-cliente"
ufs_atendidas = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]
"""


@dataclass
class ResultadoCotacao:
    transportadora: str
    status: str
    valor_frete: float | None = None
    prazo_dias: int | None = None
    detalhes: str | None = None
    duration_ms: int | None = None
    stage: str | None = None
    error_code: str | None = None
    raw: Any = None


PROVIDER_PROGRESS_STATUSES = {
    "aguardando",
    "login",
    "cotando",
    "finalizada",
    "erro",
    "desabilitada",
    "nao_atendido",
}

PROVIDER_PROGRESS_STAGE_LABELS = {
    "aguardando": "Aguardando",
    "login": "Fazendo login",
    "cotacao": "Cotando",
    "resultado": "Resultado",
    "finalizado": "Finalizada",
    "validacao": "Validacao",
    "configuracao": "Configuracao",
    "licenca": "Licenca",
}

PROVIDER_PROGRESS_MESSAGES = {
    "desabilitada": "Transportadora desabilitada pela licença",
    "nao_atendido": "UF não atendida",
    "configuracao_incompleta": "Configuração incompleta",
    "timeout": "Tempo limite aguardando resultado",
    "sem_cotacao": "Sem cotação retornada",
}


@dataclass
class ProviderCotacaoStatus:
    provider: str
    stage: str
    status: str
    mensagem: str
    duration_ms: int | None = None
    resultado: ResultadoCotacao | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": self.provider,
            "stage": self.stage,
            "status": self.status,
            "mensagem": self.mensagem,
        }
        if self.duration_ms is not None:
            payload["duration_ms"] = int(self.duration_ms)
        if self.resultado is not None:
            payload["resultado"] = self.resultado
        return payload


def normalize_provider_progress_status(status: Any) -> str:
    normalized = str(status or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "pending": "aguardando",
        "waiting": "aguardando",
        "wait": "aguardando",
        "logging_in": "login",
        "fazendo_login": "login",
        "quoting": "cotando",
        "quote": "cotando",
        "ok": "finalizada",
        "done": "finalizada",
        "finished": "finalizada",
        "finalizado": "finalizada",
        "error": "erro",
        "failed": "erro",
        "falha": "erro",
        "disabled": "desabilitada",
        "desabilitado": "desabilitada",
        "not_served": "nao_atendido",
        "nao_atendida": "nao_atendido",
        "sem_cotacao": "erro",
        "no_quote": "erro",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in PROVIDER_PROGRESS_STATUSES:
        return normalized
    if normalized.startswith("erro"):
        return "erro"
    return "erro"


def normalize_provider_progress_message(status: Any, mensagem: Any = "", *, stage: Any = "") -> str:
    progress_status = normalize_provider_progress_status(status)
    raw = str(mensagem or "").strip()
    raw_lower = raw.lower()
    stage_lower = str(stage or "").strip().lower()

    if progress_status == "desabilitada":
        return PROVIDER_PROGRESS_MESSAGES["desabilitada"]
    if progress_status == "nao_atendido":
        return PROVIDER_PROGRESS_MESSAGES["nao_atendido"]
    if "configura" in raw_lower or "configuracao" in stage_lower or "configuração" in raw_lower:
        return PROVIDER_PROGRESS_MESSAGES["configuracao_incompleta"]
    if "timeout" in raw_lower or "tempo limite" in raw_lower:
        return PROVIDER_PROGRESS_MESSAGES["timeout"]
    if progress_status == "erro" and (
        not raw or "sem resultado" in raw_lower or "sem cotação" in raw_lower or "sem cotacao" in raw_lower
    ):
        return PROVIDER_PROGRESS_MESSAGES["sem_cotacao"]
    if progress_status == "aguardando":
        return raw or "Aguardando início"
    if progress_status == "login":
        return raw or "Fazendo login"
    if progress_status == "cotando":
        return raw or "Cotando frete"
    if progress_status == "finalizada":
        return raw or "Cotação finalizada"
    return raw or "Falha ao cotar"


def provider_progress_from_resultado(
    resultado: ResultadoCotacao,
    *,
    stage: str | None = None,
    duration_ms: int | None = None,
) -> ProviderCotacaoStatus:
    status = str(getattr(resultado, "status", "") or "").strip()
    if status == "ok":
        progress_status = "finalizada"
    elif status == "desabilitada":
        progress_status = "desabilitada"
    elif status == "nao_atendido":
        progress_status = "nao_atendido"
    else:
        progress_status = "erro"

    provider = str(getattr(resultado, "transportadora", "") or "GERAL").strip().upper()
    result_stage = stage or getattr(resultado, "stage", None) or "resultado"
    result_duration = duration_ms if duration_ms is not None else getattr(resultado, "duration_ms", None)
    if status == "ok" and getattr(resultado, "valor_frete", None) is not None:
        prazo = int(getattr(resultado, "prazo_dias", None) or 0)
        mensagem = f"R$ {float(resultado.valor_frete):.2f} | {prazo} dia(s)"
    else:
        mensagem = normalize_provider_progress_message(
            progress_status,
            getattr(resultado, "detalhes", None) or status,
            stage=result_stage,
        )
    return ProviderCotacaoStatus(
        provider=provider,
        stage=str(result_stage or "resultado"),
        status=progress_status,
        mensagem=mensagem,
        duration_ms=result_duration,
        resultado=resultado,
    )


def carrier_login_indicator_from_progress_payload(payload: dict[str, Any] | None) -> tuple[str, str] | None:
    """Promove o indicador visual para OK quando uma cotação já voltou válida."""
    if not isinstance(payload, dict):
        return None

    resultado = payload.get("resultado")
    provider = str(payload.get("provider") or "").strip().upper()
    if not provider and isinstance(resultado, ResultadoCotacao):
        provider = str(getattr(resultado, "transportadora", "") or "").strip().upper()
    if not provider:
        return None

    if isinstance(resultado, ResultadoCotacao) and str(getattr(resultado, "status", "") or "").strip().lower() == "ok":
        return provider, "ok"

    if normalize_provider_progress_status(payload.get("status")) == "finalizada":
        return provider, "ok"

    return None

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




__all__ = [name for name in globals() if not name.startswith("__")]
