"""Characterization tests for the orchestrator's per-result DISPATCH closures.

These drive the REAL ``_processar_resultado`` / ``_run_cotacao`` closures
end-to-end through ``cotacao_transportadoras._executar_cotacoes_com_dados`` and
pin every dispatch shape that is reachable from the public entrypoint:

  * QuoteResponse OK  -> status 'ok', valor/prazo mapped, sessao success recorded.
  * QuoteResponse ERROR -> status mapping + report + sessao failure recorded.
  * legacy result object -> status 'ok' via getattr.
  * coteir returns None, TRANSIENT last_error -> retry, NO report, NO failure record.
  * coteir returns None, TERMINAL last_error -> report + failure record + retry.
  * business error via exception -> status 'nao_atendido', NO report, NO record.
  * business error via None last_error -> status 'nao_atendido', NO report.
  * raised non-transient exception -> report + status 'erro' + failure record.
  * progress callback wiring driven from the dispatch.

WHY this file exists: ``test_char_orchestrator_builders.py`` covers only the PURE
transforms (``_build_*_kwargs``, ``quote_response_to_resultado_cotacao``, the
error classifiers). The dispatch closures themselves had NO end-to-end coverage
for the QuoteResponse branches, the report/record side effects, or the in-closure
business-error classification. This locks that contract BEFORE the Phase 6
decomposition (replace the ``_run_cotacao`` 7-tuple with a ``CotacaoOutcome``
dataclass; split ``_processar_resultado`` into per-shape handlers).

Assertions encode what the code does TODAY. The malformed-result guard and the
``as_completed`` loop-level exception handler are defensive branches not reachable
through the public entrypoint; they are preserved by review, not pinned here.
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import cotacao_transportadoras as ct
from cotacao import deps
from cotacao import orchestrator as cotacao_orchestrator
from cotacao.common import QuoteResponse


# --- Smallest valid inputs that pass every validation guard ------------------
# Exactly one enabled carrier (TRD) and serial execution for deterministic
# ordering. uf_destino RS matches the destino CEP's UF (else the divergence
# guard trips); volumes equals the sum of cubagem quantities.

def _config():
    return {
        "fretio": {"max_paralelo": 1},
        "romaneio": {"cep_origem": "90000000"},
        "transportadoras": {"trd": {"habilitado": True}},
    }


def _dados():
    return {
        "destino_cep": "90010123",
        "uf_destino": "RS",
        "cnpj_destinatario": "12345678000190",
        "peso": 3.3,
        "valor": 150.0,
        "volumes": 1,
        "cubagem_m3": 0.044,
        "cubagens": [
            {"quantidade": 1, "comprimento_cm": 45, "largura_cm": 31, "altura_cm": 31}
        ],
        "descricoes_itens": [],
    }


def _make_factory(provider):
    class FakeFactory:
        def __init__(self, config):
            self.config = config

        def is_available(self, nome):
            return nome == "trd"

        def get_provider_config(self, nome):
            if nome == "trd":
                return {
                    "habilitado": True,
                    "email": "x@x.com",
                    "senha": "pw",
                    "ufs_atendidas": ["RS"],
                }
            return {"habilitado": False}

        def validate_minimum_config(self, nome):
            from fretio.providers.factory import validate_provider_minimum_config
            return validate_provider_minimum_config(nome, self.get_provider_config(nome))

        def create(self, nome, **kwargs):
            return provider

    return FakeFactory


class _FakeSessao:
    """Records circuit-breaker calls; returns the single fake provider."""

    providers = None

    def __init__(self, provider):
        self._provider = provider
        self.sucessos: list[str] = []
        self.falhas: list[str] = []

    async def obter_provider(self, nome):
        # None -> orchestrator skips the headless check and goes straight to
        # assegurar_provider, so no fechar_provider machinery is needed.
        return None

    async def assegurar_provider(self, nome, factory):
        return self._provider

    def record_quote_success(self, nome):
        self.sucessos.append(nome)

    def record_quote_failure(self, nome):
        self.falhas.append(nome)


def _drive(monkeypatch, provider, *, sessao=None):
    reports: list[tuple] = []
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))
    monkeypatch.setattr(deps, "ProviderFactory", _make_factory(provider))
    monkeypatch.setattr(
        cotacao_orchestrator,
        "report_provider_error",
        lambda provider, stage, message, **kw: reports.append((provider, stage, message)),
    )
    eventos: list[dict] = []
    resultados = asyncio.run(
        ct._executar_cotacoes_com_dados(
            config=_config(),
            dados=_dados(),
            cep_origem="90000000",
            sessao=sessao,
            progresso_callback=eventos.append,
        )
    )
    trd = [r for r in resultados if r.transportadora == "TRD"]
    return resultados, trd, reports, eventos


# --- Fake providers ----------------------------------------------------------
# Providers WITH a class-level ``cotar(self, request)`` take the QuoteResponse
# path; providers with only ``coteir`` take the legacy path.

class _QuoteOkProvider:
    nome = "TRD"
    last_error = None
    _passo_atual = "inicio"

    async def cotar(self, request):
        return QuoteResponse.ok(provider="TRD", valor_frete=321.0, prazo_dias=7)

    async def cleanup(self):
        pass


class _QuoteErrorProvider:
    nome = "TRD"
    last_error = None
    _passo_atual = "ler_resultado"

    async def cotar(self, request):
        # error_code that does NOT start with 'erro' keeps status 'erro' (a code
        # like 'erro_*' would promote the status and skip the report branch —
        # that promotion is pinned separately in test_char_orchestrator_builders).
        return QuoteResponse.error(provider="TRD", detalhes="falha portal", error_code="falha_portal")

    async def cleanup(self):
        pass


class _LegacyOkProvider:
    nome = "TRD"
    last_error = None
    _passo_atual = "inicio"

    async def coteir(self, **kwargs):
        from fretio.models import Cotacao
        return Cotacao(transportadora="TRD", prazo_dias=4, valor_frete=333.0)

    async def cleanup(self):
        pass


class _NoneProvider:
    nome = "TRD"
    _passo_atual = "ler_resultado"

    def __init__(self, last_error):
        self.last_error = last_error

    async def coteir(self, **kwargs):
        return None

    async def cleanup(self):
        pass


class _RaiseProvider:
    nome = "TRD"
    last_error = None
    _passo_atual = "submeter"

    def __init__(self, exc):
        self._exc = exc

    async def coteir(self, **kwargs):
        raise self._exc

    async def cleanup(self):
        pass


# --- QuoteResponse OK --------------------------------------------------------

def test_dispatch_quote_response_ok_maps_value_and_records_success(monkeypatch):
    sessao = _FakeSessao(_QuoteOkProvider())
    _res, trd, reports, _eventos = _drive(monkeypatch, sessao._provider, sessao=sessao)
    assert len(trd) == 1
    assert trd[0].status == "ok"
    assert trd[0].valor_frete == 321.0
    assert trd[0].prazo_dias == 7
    assert trd[0].duration_ms is not None  # orchestrator backfills duration_ms
    assert sessao.sucessos == ["TRD"]
    assert sessao.falhas == []
    assert reports == []  # success is never reported


def test_dispatch_quote_response_ok_without_sessao(monkeypatch):
    """Default production path: provider created ad-hoc via factory.create and
    cleaned up by _cleanup_adhoc (no sessao)."""
    _res, trd, reports, _eventos = _drive(monkeypatch, _QuoteOkProvider())
    assert len(trd) == 1
    assert trd[0].status == "ok"
    assert trd[0].valor_frete == 321.0
    assert reports == []


# --- QuoteResponse ERROR -----------------------------------------------------

def test_dispatch_quote_response_error_reports_and_records_failure(monkeypatch):
    sessao = _FakeSessao(_QuoteErrorProvider())
    _res, trd, reports, _eventos = _drive(monkeypatch, sessao._provider, sessao=sessao)
    assert len(trd) == 1
    assert trd[0].status == "erro"
    assert "TRD" in sessao.falhas
    assert sessao.sucessos == []
    # The QuoteResponse-error branch reports exactly once (it does not retry).
    assert len(reports) == 1
    assert reports[0][0] == "TRD"


# --- legacy result object ----------------------------------------------------

def test_dispatch_legacy_object_maps_ok_and_records_success(monkeypatch):
    sessao = _FakeSessao(_LegacyOkProvider())
    _res, trd, reports, _eventos = _drive(monkeypatch, sessao._provider, sessao=sessao)
    assert len(trd) == 1
    assert trd[0].status == "ok"
    assert trd[0].valor_frete == 333.0
    assert trd[0].prazo_dias == 4
    assert sessao.sucessos == ["TRD"]
    assert reports == []


# --- None: transient vs terminal ---------------------------------------------

def test_dispatch_none_transient_retries_without_report_or_failure_record(monkeypatch):
    sessao = _FakeSessao(_NoneProvider(last_error="net::ERR_TIMED_OUT ao abrir portal"))
    _res, trd, reports, _eventos = _drive(monkeypatch, sessao._provider, sessao=sessao)
    assert len(trd) == 1
    assert trd[0].status == "erro"
    # Transient None failures are NOT reported and do NOT record a quote failure
    # (the branch returns before record_quote_failure / report_provider_error).
    assert reports == []
    assert sessao.falhas == []


def test_dispatch_none_terminal_reports_and_records_failure(monkeypatch):
    sessao = _FakeSessao(_NoneProvider(last_error="Erro inesperado ao ler resultado do portal"))
    _res, trd, reports, _eventos = _drive(monkeypatch, sessao._provider, sessao=sessao)
    assert len(trd) == 1
    assert trd[0].status == "erro"
    assert len(reports) >= 1
    assert reports[0][0] == "TRD"
    assert "TRD" in sessao.falhas


# --- business error: via None and via exception ------------------------------

def test_dispatch_business_error_via_none_marks_nao_atendido(monkeypatch):
    sessao = _FakeSessao(_NoneProvider(last_error="Destino fora da cobertura"))
    _res, trd, reports, _eventos = _drive(monkeypatch, sessao._provider, sessao=sessao)
    assert len(trd) == 1
    assert trd[0].status == "nao_atendido"
    assert reports == []
    assert sessao.falhas == []


def test_dispatch_business_error_via_exception_marks_nao_atendido(monkeypatch):
    sessao = _FakeSessao(_RaiseProvider(RuntimeError("Rota não atendida para este destino")))
    _res, trd, reports, _eventos = _drive(monkeypatch, sessao._provider, sessao=sessao)
    assert len(trd) == 1
    assert trd[0].status == "nao_atendido"
    assert reports == []
    # Business exceptions return before sessao.record_quote_failure.
    assert sessao.falhas == []


# --- raised non-transient exception ------------------------------------------

def test_dispatch_raised_non_transient_exception_reports_and_errors(monkeypatch):
    sessao = _FakeSessao(_RaiseProvider(RuntimeError("Erro inesperado ao processar cotação")))
    _res, trd, reports, _eventos = _drive(monkeypatch, sessao._provider, sessao=sessao)
    assert len(trd) == 1
    assert trd[0].status == "erro"
    assert len(reports) >= 1
    assert reports[0][0] == "TRD"
    assert "TRD" in sessao.falhas


# --- progress callback wiring ------------------------------------------------

def test_dispatch_progress_callback_receives_result_event(monkeypatch):
    _res, trd, _reports, eventos = _drive(monkeypatch, _QuoteOkProvider())
    assert len(trd) == 1
    assert eventos, "progress callback should have been invoked"
    assert any(
        getattr(ev.get("resultado"), "transportadora", "") == "TRD"
        for ev in eventos
        if isinstance(ev, dict)
    )
