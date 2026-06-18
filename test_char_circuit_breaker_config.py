"""Characterization tests for cotacao.circuit_breaker and cotacao.config.

These pin the CURRENT behavior of:
  - ProviderCircuitBreaker state machine (closed -> open -> half-open -> reset)
  - cotacao.config._dados_envio (dict shape + Phase 1 cnpj_destinatario fix)
  - cotacao.config._resolver_cep_origem / obter_cep_origem_default

DO NOT "fix" anything here: assertions encode what the code actually does today,
including quirks. Quirks are documented in comments where relevant.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from cotacao import circuit_breaker, config


# ---------------------------------------------------------------------------
# Deterministic clock helper for the circuit breaker.
# circuit_breaker.is_open / record_failure use time.monotonic() from the
# module's `time` import. We monkeypatch a controllable fake clock.
# ---------------------------------------------------------------------------
class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _patch_clock(monkeypatch) -> _FakeClock:
    clock = _FakeClock()
    monkeypatch.setattr(circuit_breaker.time, "monotonic", clock.monotonic)
    return clock


# ===========================================================================
# TARGET A: ProviderCircuitBreaker state machine
# ===========================================================================

def test_char_breaker_starts_closed(monkeypatch):
    _patch_clock(monkeypatch)
    cb = circuit_breaker.ProviderCircuitBreaker()
    # Unknown provider is closed (lazily creates state, not open)
    assert cb.is_open("braspress") is False


def test_char_breaker_opens_after_threshold_failures(monkeypatch):
    _patch_clock(monkeypatch)
    cb = circuit_breaker.ProviderCircuitBreaker()  # default threshold = 3
    cb.record_failure("trd")
    assert cb.is_open("trd") is False  # 1 failure: still closed
    cb.record_failure("trd")
    assert cb.is_open("trd") is False  # 2 failures: still closed
    cb.record_failure("trd")
    assert cb.is_open("trd") is True   # 3rd failure (>= threshold): OPEN


def test_char_breaker_half_open_after_cooldown(monkeypatch):
    clock = _patch_clock(monkeypatch)
    cb = circuit_breaker.ProviderCircuitBreaker(
        failure_threshold=2, recovery_timeout_s=300.0
    )
    cb.record_failure("agex")
    cb.record_failure("agex")
    assert cb.is_open("agex") is True

    # Just before cooldown: still open
    clock.advance(299.999)
    assert cb.is_open("agex") is True

    # Exactly at the cooldown boundary (elapsed >= timeout): half-open -> False.
    # QUIRK: half-open does NOT reset state; opened_at stays set, failures stay
    # at threshold. is_open keeps returning False while time keeps elapsing,
    # but a single new record_failure would re-open immediately (see next test).
    clock.advance(0.001)
    assert cb.is_open("agex") is False


def test_char_breaker_half_open_failure_reopens_immediately(monkeypatch):
    clock = _patch_clock(monkeypatch)
    cb = circuit_breaker.ProviderCircuitBreaker(
        failure_threshold=2, recovery_timeout_s=100.0
    )
    cb.record_failure("eucatur")
    cb.record_failure("eucatur")
    assert cb.is_open("eucatur") is True

    clock.advance(100.0)  # enter half-open window
    assert cb.is_open("eucatur") is False

    # QUIRK: in half-open, failures counter was NOT reset, so it's already at
    # threshold (2). One more failure (-> 3 >= 2) re-opens and refreshes opened_at
    # to the current (advanced) clock value.
    cb.record_failure("eucatur")
    assert cb.is_open("eucatur") is True


def test_char_breaker_success_resets_to_closed(monkeypatch):
    clock = _patch_clock(monkeypatch)
    cb = circuit_breaker.ProviderCircuitBreaker(failure_threshold=2)
    cb.record_failure("alfa")
    cb.record_failure("alfa")
    assert cb.is_open("alfa") is True

    cb.record_success("alfa")  # full reset: failures=0, opened_at=None
    assert cb.is_open("alfa") is False

    # After reset it takes the full threshold of NEW failures to open again.
    cb.record_failure("alfa")
    assert cb.is_open("alfa") is False
    cb.record_failure("alfa")
    assert cb.is_open("alfa") is True


def test_char_breaker_name_normalized_case_and_whitespace(monkeypatch):
    _patch_clock(monkeypatch)
    cb = circuit_breaker.ProviderCircuitBreaker(failure_threshold=2)
    # State key is str(nome).strip().lower(): these all map to the same circuit.
    cb.record_failure("  Coopex ")
    cb.record_failure("COOPEX")
    assert cb.is_open("coopex") is True


def test_char_breaker_states_are_isolated_per_provider(monkeypatch):
    _patch_clock(monkeypatch)
    cb = circuit_breaker.ProviderCircuitBreaker(failure_threshold=2)
    cb.record_failure("braspress")
    cb.record_failure("braspress")
    assert cb.is_open("braspress") is True
    # A different provider is unaffected.
    assert cb.is_open("translovato") is False


# ===========================================================================
# TARGET B: cotacao.config._dados_envio
# ===========================================================================

class _FakeItem:
    def __init__(self, produto="", descricao=""):
        self.produto = produto
        self.descricao = descricao


class _FakePedido:
    def __init__(self, local_entrega="", cnpj_cliente="", itens=None):
        self.local_entrega = local_entrega
        self.cnpj_cliente = cnpj_cliente
        self.itens = itens or []


class _FakeExtrator:
    """Minimal stand-in exposing exactly the surface _dados_envio touches."""

    def __init__(
        self,
        agrupadas,
        cep_local="89010-000",
        uf_local="SC",
        componentes=("Rua X", "89010000", "Blumenau / SC"),
    ):
        self._agrupadas = agrupadas
        self._cep_local = cep_local
        self._uf_local = uf_local
        self._componentes = componentes

    def _calcular_caixas_agrupadas(self, pedidos):
        return self._agrupadas

    def obter_cep_local_entrega(self, local_entrega):
        return self._cep_local

    def obter_uf_local_entrega(self, local_entrega):
        return self._uf_local

    def _extrair_componentes_local(self, local_entrega):
        return self._componentes


def _make_agrupadas(
    grupos_caixa=None,
    caixas_complementares=None,
    total_boxes=0,
    total_volume=0.0,
    total_weight=0.0,
    total_valor=0.0,
):
    return (
        grupos_caixa or {},
        caixas_complementares or [],
        total_boxes,
        total_volume,
        total_weight,
        total_valor,
    )


def test_char_dados_envio_empty_pedidos_returns_empty_dict():
    extrator = _FakeExtrator(_make_agrupadas())
    assert config._dados_envio(extrator, []) == {}


# ---------------------------------------------------------------------------
# BUG corrigido na Fase 5: antes, `_rua, _cep, cidade_uf = extrator
# ._extrair_componentes_local(...)` ligava `_cep` como LOCAL, sombreando a função
# _cep importada de validation. Para qualquer `pedidos` não vazio o return
# `{... "destino_cep": _cep(destino_cep) ...}` quebrava (TypeError/UnboundLocalError),
# então o fluxo de cotação por PDF nunca produzia dados. A local virou _cep_comp.
# Os testes abaixo fixam o dict correto agora produzido.
# ---------------------------------------------------------------------------

def test_char_dados_envio_returns_populated_dict_with_componentes():
    extrator = _FakeExtrator(
        _make_agrupadas(
            total_boxes=5, total_volume=1.25, total_weight=42.5, total_valor=1000.0
        )
    )
    pedido = _FakePedido(
        local_entrega="Rua X, 89010-000, Blumenau / SC",
        cnpj_cliente="12.345.678/0001-95",
        itens=[_FakeItem(produto="Parafuso", descricao="Aco inox")],
    )
    dados = config._dados_envio(extrator, [pedido])
    assert dados == {
        "destino_cep": "89010000",
        "uf_destino": "SC",
        "cidade_destino": "Blumenau",
        "cnpj_destinatario": "12345678000195",
        "peso": 42.5,
        "valor": 1000.0,
        "volumes": 5,
        "cubagem_m3": 1.25,
        "comprimento_cm": 0,
        "largura_cm": 0,
        "altura_cm": 0,
        "cubagens": [],
        "descricoes_itens": ["Parafuso", "Aco inox"],
    }


def test_char_dados_envio_works_without_componentes_method():
    class _ExtratorNoComponentes:
        def _calcular_caixas_agrupadas(self, pedidos):
            return _make_agrupadas()

        def obter_cep_local_entrega(self, local):
            return "89010-000"

        def obter_uf_local_entrega(self, local):
            return "SC"

    pedido = _FakePedido(local_entrega="X / SP", cnpj_cliente="1")
    dados = config._dados_envio(_ExtratorNoComponentes(), [pedido])
    assert dados["destino_cep"] == "89010000"
    assert dados["uf_destino"] == "SC"
    assert dados["cidade_destino"] == ""        # sem método de componentes
    assert dados["cnpj_destinatario"] == "1"


def test_char_dados_envio_empty_pedidos_is_only_dict_returning_path():
    # The ONLY input that returns a dict today is the empty-pedidos early return.
    extrator = _FakeExtrator(_make_agrupadas())
    assert config._dados_envio(extrator, []) == {}


def test_char_dados_envio_cnpj_logic_is_digits_in_isolation():
    """Phase 1 cnpj_destinatario logic is correct in isolation (the digits of
    cnpj_cliente), even though the surrounding function crashes before returning
    it. This locks the intended `_digits(getattr(pedido, "cnpj_cliente", ""))`
    behavior using the same helper the production code uses.

    NOTE: we cannot observe out["cnpj_destinatario"] because _dados_envio raises
    on the return line (see the two tests above). We pin the underlying helper
    semantics instead so the Phase 1 fix intent is still captured.
    """
    from cotacao.validation import _digits

    pedido = _FakePedido(cnpj_cliente="12.345.678/0001-95")
    assert _digits(getattr(pedido, "cnpj_cliente", "")) == "12345678000195"
    # Missing/empty -> "" (getattr default + _digits of empty).
    bare = _FakePedido(cnpj_cliente="")
    assert _digits(getattr(bare, "cnpj_cliente", "")) == ""


def test_char_dados_envio_security_cnpj_digits_only_helper():
    """SECURITY boundary: the cnpj normalization used by _dados_envio strips all
    punctuation, so no dotted/slashed full CNPJ can leak into the payload field.

    Pinned via the exact helper (_digits) the function applies to cnpj_cliente,
    since the function body itself currently raises before returning the dict.
    """
    from cotacao.validation import _digits

    formatted = "98.765.432/0001-10"
    normalized = _digits(formatted)
    assert normalized == "98765432000110"
    assert "." not in normalized
    assert "/" not in normalized
    assert "-" not in normalized
    assert formatted != normalized  # formatting was stripped, not passed through


# ===========================================================================
# TARGET B (cont.): _resolver_cep_origem / obter_cep_origem_default
# ===========================================================================

def test_char_resolver_cep_origem_precedence():
    cfg = {
        "romaneio": {"cep_origem": "11.222-333"},
        "transportadoras": {"braspress": {"cep_origem": "22.333-444"}},
    }
    # Informed CEP wins.
    assert config._resolver_cep_origem(cfg, "44.555-666") == "44555666"
    # No informed -> romaneio.cep_origem.
    assert config._resolver_cep_origem(cfg, "") == "11222333"


def test_char_resolver_cep_origem_falls_back_to_transportadora_then_default():
    # No romaneio, falls to first transportadora cep (braspress then trd order).
    cfg_trd = {"transportadoras": {"trd": {"cep_origem": "55.666-777"}}}
    assert config._resolver_cep_origem(cfg_trd, "") == "55666777"
    # Nothing configured -> module default constant.
    assert config._resolver_cep_origem({}, "") == config.CEP_ORIGEM_PADRAO
    assert config.CEP_ORIGEM_PADRAO == "99740000"


def test_char_resolver_cep_origem_non_dict_config():
    # QUIRK: a non-dict config is tolerated; treated as empty -> default.
    assert config._resolver_cep_origem(None, "") == config.CEP_ORIGEM_PADRAO


def test_char_obter_cep_origem_default_reads_romaneio(monkeypatch):
    # Stub _carregar_config so no real CONFIG.toml / filesystem is touched.
    monkeypatch.setattr(
        config, "_carregar_config",
        lambda config_path=None: {"romaneio": {"cep_origem": "12.345-678"}},
    )
    # Identity override so behavior is deterministic regardless of remote_config.
    monkeypatch.setattr(config, "apply_safe_runtime_overrides", lambda c: c)
    assert config.obter_cep_origem_default() == "12345678"


def test_char_obter_cep_origem_default_empty_config_returns_padrao(monkeypatch):
    monkeypatch.setattr(config, "_carregar_config", lambda config_path=None: {})
    monkeypatch.setattr(config, "apply_safe_runtime_overrides", lambda c: c)
    assert config.obter_cep_origem_default() == config.CEP_ORIGEM_PADRAO
