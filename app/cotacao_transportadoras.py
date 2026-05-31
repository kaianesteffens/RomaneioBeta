"""Fachada compatível para cotação de transportadoras.

A implementação foi dividida em ``app.cotacao``. Este módulo mantém os
imports antigos usados pelo restante do aplicativo e por integrações externas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import asyncio
import sys

from cotacao.common import *
from cotacao.validation import *
from cotacao.telemetry import *
from cotacao.jobs_client import *
from cotacao.config import *
from cotacao.romaneio_parser import *
from cotacao.session_manager import *
from cotacao.error_context import *
from cotacao import common as _common_mod
from cotacao import config as _config_mod
from cotacao import jobs_client as _jobs_mod
from cotacao import session_manager as _session_mod
from cotacao import telemetry as _telemetry_mod
from cotacao import orchestrator as _orchestrator_mod

try:
    from quotation_normalization_client import normalize_quotation_remote_shadow
except Exception:
    def normalize_quotation_remote_shadow(*a, **kw):
        return {"queued": False}


def _run_shadow_normalization_compat(
    source_type: str,
    config: dict,
    dados: dict,
    *,
    cep_origem: str = "",
    modo: str = "",
    log_func=None,
) -> None:
    try:
        payload = {
            "modo": str(modo or ""),
            "cep_origem": _cep(cep_origem),
            "destino_cep": dados.get("destino_cep") if isinstance(dados, dict) else None,
            "uf_destino": dados.get("uf_destino") if isinstance(dados, dict) else None,
            "volumes": dados.get("volumes") if isinstance(dados, dict) else None,
            "peso": dados.get("peso") if isinstance(dados, dict) else None,
            "valor": dados.get("valor") if isinstance(dados, dict) else None,
            "cubagem_m3": dados.get("cubagem_m3") if isinstance(dados, dict) else None,
            "cubagens": dados.get("cubagens") if isinstance(dados, dict) else None,
        }
        normalize_quotation_remote_shadow(source_type, payload=payload, wait=False)
    except Exception as exc:
        try:
            if log_func is not None:
                log_func(f"shadow normalization skipped/fail: {exc}")
        except Exception:
            pass


def _sync_legacy_overrides() -> None:
    for mod in (_common_mod, _config_mod, _jobs_mod, _telemetry_mod, _session_mod, _orchestrator_mod):
        for name in (
            "ProviderFactory",
            "apply_safe_runtime_overrides",
            "report_error",
            "report_error_message",
            "report_error_payload",
            "report_provider_error",
            "report_quotation_started",
            "report_quotation_finished",
            "report_carrier_quotation_result",
            "create_quotation_job",
            "update_quotation_job_result",
            "carrier_enabled_or_message",
            "normalize_carrier_name",
            "MODO_FOCO_TRANSPORTADORA",
        ):
            if name in globals():
                setattr(mod, name, globals()[name])
    for mod in (_jobs_mod, _telemetry_mod, _session_mod, _orchestrator_mod):
        for name in ("_carregar_config", "_kill_orphan_Fretio_chromes", "_diag_log_enabled", "_log_diag"):
            if name in globals():
                setattr(mod, name, globals()[name])


class TransportadoraSession(_session_mod.TransportadoraSession):
    def __init__(self, config_path: Path | None = None):
        _sync_legacy_overrides()
        super().__init__(config_path=config_path)

    async def inicializar(self, callback=None, login_status_callback=None):
        _sync_legacy_overrides()
        return await super().inicializar(callback=callback, login_status_callback=login_status_callback)


async def _executar_cotacoes_com_dados(*args, **kwargs):
    _sync_legacy_overrides()
    if kwargs.get("sessao") is not None and isinstance(kwargs.get("sessao"), TransportadoraSession):
        pass
    return await _orchestrator_mod._executar_cotacoes_com_dados(*args, **kwargs)

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
    _sync_legacy_overrides()
    config = sessao.config if sessao else _carregar_config(config_path=config_path)
    started_at = time.monotonic()
    dados: dict[str, Any] | None = None
    resultados: list[ResultadoCotacao] | None = None
    job_payload = _quotation_job_start_payload(
        config,
        modo="pdf",
        quantidade_pedidos=len(pedidos or []),
    )
    job_id = _create_quotation_job_best_effort("romaneio", job_payload)
    report_quotation_started(metadata=_quotation_usage_metadata(None, modo="pdf", job_id=job_id))
    cancelled = False
    try:
        dados = _dados_envio(extrator=extrator, pedidos=pedidos)
        if not dados:
            _log_diag("Sem dados de envio para cotação")
            resultados = [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes="Nenhum pedido disponível para cotação")]
            return resultados
        _run_shadow_normalization_compat(
            "romaneio",
            config,
            dados,
            cep_origem=cep_origem,
            modo="pdf",
            log_func=_log_diag,
        )
        _mark_quotation_job_running_best_effort(job_id)
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
        job_result = _quotation_job_result_payload(config, resultados)
        job_status = _quotation_job_final_status(
            job_result,
            cancelled=cancelled,
            general_error=general_error,
        )
        _finish_quotation_job_best_effort(
            job_id,
            status=job_status,
            result=job_result,
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
    _sync_legacy_overrides()
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
    job_id = _create_quotation_job_best_effort("manual", job_payload)
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
        _run_shadow_normalization_compat(
            "manual",
            config,
            dados,
            cep_origem=cep_origem,
            modo=modo,
            log_func=_log_diag,
        )
        _mark_quotation_job_running_best_effort(job_id)
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
        job_result = _quotation_job_result_payload(config, resultados)
        job_status = _quotation_job_final_status(
            job_result,
            cancelled=cancelled,
            general_error=general_error,
        )
        _finish_quotation_job_best_effort(
            job_id,
            status=job_status,
            result=job_result,
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
    _sync_legacy_overrides()
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
    incompletas = [r for r in resultados if r.status == "Configuração incompleta"]
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

    if incompletas:
        linhas.append("")
        linhas.append("Transportadoras com configuração incompleta:")
        for item in incompletas:
            detalhe = item.detalhes or "Preencha os campos obrigatórios em Configurações > Credenciais."
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
