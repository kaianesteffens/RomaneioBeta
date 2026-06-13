"""Per-pattern tests for the cotação error classifiers.

The three classifier families lived scattered across orchestrator.py
(``_is_business_error`` / ``_TRANSIENT_PATTERNS``) and error_context.py
(``_PRELOGIN_CONTROLLED_PATTERNS``). These tests pin that EVERY pattern in the
transient and pre-login tuples actually classifies, that business phrases are
caught, and that the known cross-classification quirks hold. They are imported
from the public/legacy homes so they keep passing after the patterns are moved
into a single ``cotacao.error_classifiers`` module (re-exported from both).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from cotacao.orchestrator import (
    _is_business_error,
    _is_expected_transient_failure,
    _is_expected_transient_failure_str,
    _TRANSIENT_PATTERNS,
)
from cotacao.error_context import (
    _PRELOGIN_CONTROLLED_PATTERNS,
    is_expected_prelogin_failure,
)


_BUSINESS_PHRASES = (
    "Destino fora da cobertura",
    "CEP destino não atendido",
    "Não atendemos esse CEP",
    "Rota não atendida",
    "transportadora não atende",
    "Sem precificação automática no SSW",
    "Cidade de destino não cadastrada",
    "Rota: SP-AM indisponível",
)


def test_every_transient_pattern_classifies_as_transient():
    for pattern in _TRANSIENT_PATTERNS:
        detail = f"contexto {pattern} fim"
        assert _is_expected_transient_failure_str(detail), pattern
        assert _is_expected_transient_failure(RuntimeError(detail)), pattern


def test_every_prelogin_pattern_classifies_as_prelogin():
    for pattern in _PRELOGIN_CONTROLLED_PATTERNS:
        detail = f"pré-login: {pattern} (provider X)"
        assert is_expected_prelogin_failure(detail), pattern


def test_business_phrases_classify_as_business():
    for phrase in _BUSINESS_PHRASES:
        assert _is_business_error(phrase), phrase


def test_cross_classification_quirks():
    # 'valor de frete nao encontrado' is TRANSIENT, not business.
    assert _is_expected_transient_failure_str("valor de frete nao encontrado") is True
    assert _is_business_error("valor de frete nao encontrado") is False
    # A coverage/business error is NOT a transient failure.
    assert _is_business_error("Destino fora da cobertura") is True
    assert _is_expected_transient_failure(RuntimeError("Destino fora da cobertura")) is False


def test_timeout_exception_is_always_transient():
    assert _is_expected_transient_failure(TimeoutError("x")) is True
    # ...and the *_str variant catches bare timeout wording too.
    assert _is_expected_transient_failure_str("Connection timed out") is True


def test_empty_and_none_are_unclassified():
    for empty in ("", None):
        assert _is_business_error(empty) is False
        assert _is_expected_transient_failure_str(empty or "") is False
        assert is_expected_prelogin_failure(empty) is False
