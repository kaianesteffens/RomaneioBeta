"""Fachada compatível para cotação de transportadoras.

A implementação foi dividida em ``app.cotacao``. Este módulo mantém os
imports antigos usados pelo restante do aplicativo e por integrações externas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import asyncio
import sys
import time

from cotacao.common import *
from cotacao.validation import *
from cotacao.telemetry import *
from cotacao.config import *
from cotacao.romaneio_parser import *
from cotacao.session_manager import *
from cotacao.error_context import *
from cotacao import deps
from cotacao import session_manager as _session_mod
from cotacao import orchestrator as _orchestrator_mod


TransportadoraSession = _session_mod.TransportadoraSession


async def _executar_cotacoes_com_dados(*args, **kwargs):
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
    config = sessao.config if sessao else _carregar_config(config_path=config_path)
    dados = _dados_envio(extrator=extrator, pedidos=pedidos)
    if not dados:
        _log_diag("Sem dados de envio para cotação")
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes="Nenhum pedido disponível para cotação")]
    return await _executar_cotacoes_com_dados(
        config=config,
        dados=dados,
        cep_origem=cep_origem,
        sessao=sessao,
        progresso_callback=progresso_callback,
        source_type="romaneio",
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
    try:
        dados = _dados_envio_romaneio_colado(romaneio_colado)
    except ValueError as e:
        _log_diag(f"Romaneio colado inválido: {e}")
        return [ResultadoCotacao(transportadora="GERAL", status="erro", detalhes=str(e))]
    return await _executar_cotacoes_com_dados(
        config=config,
        dados=dados,
        cep_origem=cep_origem,
        sessao=sessao,
        progresso_callback=progresso_callback,
        cnpj_remetente=cnpj_remetente,
        tipo_frete=tipo_frete,
        source_type="manual",
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
