import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from cotacao.common import (
    ProviderCotacaoStatus,
    ResultadoCotacao,
    normalize_provider_progress_message,
    normalize_provider_progress_status,
    provider_progress_from_resultado,
)


def test_normalize_provider_progress_status_aliases():
    assert normalize_provider_progress_status("pending") == "aguardando"
    assert normalize_provider_progress_status("fazendo_login") == "login"
    assert normalize_provider_progress_status("quoting") == "cotando"
    assert normalize_provider_progress_status("ok") == "finalizada"
    assert normalize_provider_progress_status("disabled") == "desabilitada"
    assert normalize_provider_progress_status("not_served") == "nao_atendido"
    assert normalize_provider_progress_status("erro_timeout") == "erro"


def test_normalize_provider_progress_message_uses_friendly_messages():
    assert normalize_provider_progress_message("desabilitada", "licenca bloqueou") == (
        "Transportadora desabilitada pela licença"
    )
    assert normalize_provider_progress_message("nao_atendido", "rota bloqueada") == "UF não atendida"
    assert normalize_provider_progress_message("erro", "Timeout aguardando resultado") == (
        "Tempo limite aguardando resultado"
    )
    assert normalize_provider_progress_message("erro", "Sem resultado") == "Sem cotação retornada"
    assert normalize_provider_progress_message("erro", "Configuração ausente") == "Configuração incompleta"


def test_provider_progress_from_resultado_normalizes_status_and_payload():
    resultado = ResultadoCotacao(
        transportadora="TRD",
        status="ok",
        valor_frete=123.45,
        prazo_dias=4,
        duration_ms=1500,
    )

    progress = provider_progress_from_resultado(resultado)
    payload = progress.to_payload()

    assert isinstance(progress, ProviderCotacaoStatus)
    assert payload["provider"] == "TRD"
    assert payload["status"] == "finalizada"
    assert payload["stage"] == "resultado"
    assert payload["mensagem"] == "R$ 123.45 | 4 dia(s)"
    assert payload["duration_ms"] == 1500
    assert payload["resultado"] is resultado
