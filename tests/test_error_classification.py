import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import pytest
from fretio.providers.base import ProviderBase


# ---------------------------------------------------------------------------
# TestSanitize
# ---------------------------------------------------------------------------

class TestSanitize:
    def test_none_returns_none(self):
        assert ProviderBase._sanitize_quote_details(None) is None

    def test_empty_string_returns_none(self):
        assert ProviderBase._sanitize_quote_details("") is None

    def test_whitespace_only_returns_none(self):
        assert ProviderBase._sanitize_quote_details("   ") is None

    def test_html_tags_removed(self):
        result = ProviderBase._sanitize_quote_details("<b>erro</b>")
        assert result == "erro"

    def test_html_tags_replaced_with_space_then_stripped(self):
        result = ProviderBase._sanitize_quote_details("<p>mensagem de <b>erro</b></p>")
        assert result is not None
        assert "<" not in result
        assert ">" not in result
        assert "erro" in result

    def test_cnpj_14_digits_replaced_with_stars(self):
        result = ProviderBase._sanitize_quote_details("CNPJ 12345678000190 inválido")
        assert result is not None
        assert "12345678000190" not in result
        assert "***" in result

    def test_cpf_11_digits_replaced_with_stars(self):
        result = ProviderBase._sanitize_quote_details("CPF 12345678901 inválido")
        assert result is not None
        assert "12345678901" not in result
        assert "***" in result

    def test_normal_string_preserved(self):
        result = ProviderBase._sanitize_quote_details("rota não atendida")
        assert result == "rota não atendida"

    def test_extra_whitespace_normalized(self):
        result = ProviderBase._sanitize_quote_details("  texto   com   espaços  ")
        assert result == "texto com espaços"

    def test_diagnostic_suffix_removed(self):
        result = ProviderBase._sanitize_quote_details(
            "erro genérico (diagnóstico salvo em: /tmp/diag.json)"
        )
        assert result is not None
        assert "diagnóstico" not in result
        assert "erro genérico" in result

    def test_multiple_cnpj_all_replaced(self):
        result = ProviderBase._sanitize_quote_details(
            "pagador 12345678000190 e destinatário 98765432000111"
        )
        assert result is not None
        assert "12345678000190" not in result
        assert "98765432000111" not in result

    def test_html_then_cnpj_both_sanitized(self):
        result = ProviderBase._sanitize_quote_details(
            "<b>CNPJ</b> 12345678000190 não encontrado"
        )
        assert result is not None
        assert "<b>" not in result
        assert "12345678000190" not in result
        assert "***" in result


# ---------------------------------------------------------------------------
# TestClassifyNoQuote
# ---------------------------------------------------------------------------

class TestClassifyNoQuote:
    @pytest.mark.parametrize("detail", [
        "rota não atendida",
        "rota nao atendida",
        "fora de cobertura",
        "destino fora da cobertura",
        "não atendido",
        "nao atendido",
        "não atendemos",
        "nao atendemos",
        "cep destino não atendido",
        "cepdestino não atendido",
        "cidade de destino não coberta",
        "sem precificação automática no ssw",
        "sem precificacao automatica no ssw",
    ])
    def test_nao_atendido_patterns(self, detail):
        status, code = ProviderBase._classify_no_quote_status(detail)
        assert status == "nao_atendido"
        assert code == "nao_atendido"

    @pytest.mark.parametrize("detail", [
        "sem cotação",
        "sem cotacao",
        "sem resultado",
        "sem valor de frete retornado",
        "portal respondeu sem cotação",
        "portal respondeu sem cotacao",
    ])
    def test_sem_cotacao_patterns(self, detail):
        status, code = ProviderBase._classify_no_quote_status(detail)
        assert status == "sem_cotacao"
        assert code == "sem_cotacao"

    @pytest.mark.parametrize("detail", [
        "falha desconhecida",
        "erro interno do servidor",
        "timeout aguardando resultado",
        "credenciais inválidas",
    ])
    def test_non_no_quote_patterns_return_erro(self, detail):
        status, code = ProviderBase._classify_no_quote_status(detail)
        assert status == "erro"
        assert code is None

    def test_case_insensitive_matching(self):
        status, code = ProviderBase._classify_no_quote_status("ROTA NÃO ATENDIDA")
        assert status == "nao_atendido"
        assert code == "nao_atendido"

    def test_sem_cotacao_case_insensitive(self):
        status, code = ProviderBase._classify_no_quote_status("SEM COTAÇÃO disponível")
        assert status == "sem_cotacao"
        assert code == "sem_cotacao"

    # --- Regression: nao_atendido must NOT become falha_tecnica ---
    def test_regression_rota_nao_atendida_is_not_falha_tecnica(self):
        status, _ = ProviderBase._classify_no_quote_status("rota não atendida")
        assert status in ("nao_atendido", "sem_cotacao", "erro")

    def test_regression_fora_da_cobertura_is_nao_atendido(self):
        status, code = ProviderBase._classify_no_quote_status("destino fora da cobertura")
        assert status == "nao_atendido"
        assert code == "nao_atendido"


# ---------------------------------------------------------------------------
# TestInferErrorCode
# ---------------------------------------------------------------------------

class TestInferErrorCode:
    # --- TimeoutError exception ---
    def test_timeout_error_exception_returns_timeout(self):
        result = ProviderBase._infer_error_code(None, TimeoutError("timed out"))
        assert result == "timeout"

    def test_timeout_error_beats_login_pattern_in_detail(self):
        """TimeoutError instance takes priority over detail text."""
        result = ProviderBase._infer_error_code("credenciais inválidas", TimeoutError("timed out"))
        assert result == "timeout"


    def test_playwright_timeout_error_returns_timeout(self):
        """PlaywrightTimeoutError tem __name__ == 'TimeoutError', portanto deve
        ser classificado como 'timeout' mesmo não herdando de TimeoutError do Python."""
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        except ImportError:
            pytest.skip("playwright not installed")
        result = ProviderBase._infer_error_code(None, PlaywrightTimeoutError("timed out"))
        assert result == "timeout"

    def test_any_exception_named_timeout_error_returns_timeout(self):
        """Qualquer exceção com __name__ == 'TimeoutError' deve retornar 'timeout',
        independente da hierarquia de herança."""
        class TimeoutError(Exception):  # noqa: N818  — nome intencional para simular playwright
            pass
        result = ProviderBase._infer_error_code(None, TimeoutError("timed out"))
        assert result == "timeout"

    # --- Timeout patterns in detail ---
    @pytest.mark.parametrize("detail", [
        "timeout aguardando resultado",
        "timed out após 30s",
        "aguardando resultado do portal",
        "operação timeout",
    ])
    def test_timeout_patterns_in_detail(self, detail):
        assert ProviderBase._infer_error_code(detail) == "timeout"

    # --- Login patterns ---
    @pytest.mark.parametrize("detail", [
        "login falhou",
        "erro no login do portal",
        "credenciais inválidas",
        "acesso negado ao portal",
    ])
    def test_login_patterns(self, detail):
        assert ProviderBase._infer_error_code(detail) == "login_falhou"

    # --- Dados inválidos patterns ---
    @pytest.mark.parametrize("detail", [
        "cubagem inválida",
        "cubagem invalida",
        "cubagem do romaneio não encontrada",
        "cubagem do romaneio nao encontrada",
        "cubagens ausentes",
        "volumes inválidos",
        "volumes invalidos",
        "cnpj do destinatário inválido",
        "cnpj do destinatario invalido",
        "cep de destino inválido",
        "cep de destino invalido",
        "não informado",
        "nao informado",
    ])
    def test_dados_invalidos_patterns(self, detail):
        assert ProviderBase._infer_error_code(detail) == "dados_invalidos"

    # --- valor_nao_encontrado ---
    def test_valor_nao_encontrado_with_acento(self):
        assert ProviderBase._infer_error_code("valor não encontrado") == "valor_nao_encontrado"

    def test_valor_nao_encontrado_without_acento(self):
        assert ProviderBase._infer_error_code("valor nao encontrado") == "valor_nao_encontrado"

    def test_valor_nao_encontrado_with_context(self):
        assert ProviderBase._infer_error_code("o valor não encontrado no portal") == "valor_nao_encontrado"

    # --- Falha técnica fallback ---
    @pytest.mark.parametrize("detail, error", [
        ("texto completamente desconhecido", None),
        (None, None),
        ("", None),
        ("algum erro não mapeado aqui", None),
    ])
    def test_unknown_returns_falha_tecnica(self, detail, error):
        assert ProviderBase._infer_error_code(detail, error) == "falha_tecnica"

    # --- Regression: cross-classification guard ---
    def test_regression_rota_nao_atendida_is_not_timeout(self):
        assert ProviderBase._infer_error_code("rota não atendida") != "timeout"

    def test_regression_timeout_is_not_login_falhou(self):
        result = ProviderBase._infer_error_code("timeout aguardando resultado")
        assert result == "timeout"
        assert result != "login_falhou"

    def test_regression_acesso_negado_is_login_falhou_not_falha_tecnica(self):
        result = ProviderBase._infer_error_code("acesso negado")
        assert result == "login_falhou"
        assert result != "falha_tecnica"

    def test_regression_none_detail_none_error_is_falha_tecnica(self):
        assert ProviderBase._infer_error_code(None, None) == "falha_tecnica"

    def test_regression_timeout_exception_not_dados_invalidos(self):
        result = ProviderBase._infer_error_code("cep de destino inválido", TimeoutError("t"))
        # TimeoutError exception takes priority
        assert result == "timeout"
        assert result != "dados_invalidos"
