import asyncio
import sys
from pathlib import Path
from urllib.error import URLError


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import cotacao_transportadoras as ct


def _valid_romaneio() -> str:
    return """
    DESTINATARIO
    CNPJ/CPF: 12.345.678/0001-90
    Cidade: Porto Alegre / RS
    CEP: 90010-123
    - VOL: 2
    - CUBAGEM: 0,044 m3
    - PESO: 3,300 kg
    - TOTAL: R$ 150,99
    2 x Caixas fechadas - 1,650 kg - 0,044 m3 - 31x31x45
    Produto Teste: 2 und
    """


def test_quotation_job_result_converts_frete_to_value_cents():
    config = {
        "transportadoras": {
            "braspress": {"habilitado": True},
            "trd": {"habilitado": False},
        }
    }
    result = ct._quotation_job_result_payload(
        config,
        [
            ct.ResultadoCotacao(
                transportadora="BRASPRESS",
                status="ok",
                valor_frete=123.45,
                prazo_dias=3,
                duration_ms=1200,
            )
        ],
    )

    braspress = next(item for item in result["transportadoras"] if item["provider"] == "braspress")
    trd = next(item for item in result["transportadoras"] if item["provider"] == "trd")
    assert braspress == {
        "provider": "braspress",
        "status": "ok",
        "value_cents": 12345,
        "duration_ms": 1200,
        "prazo_dias": 3,
    }
    assert trd["status"] == "disabled"
    assert result["summary"]["success_count"] == 1


def test_cotacao_continues_when_create_quotation_job_fails(monkeypatch):
    config = {
        "fretio": {},
        "transportadoras": {"braspress": {"habilitado": True}},
    }
    monkeypatch.setattr(ct, "_carregar_config", lambda config_path=None: config)
    monkeypatch.setattr(
        ct,
        "create_quotation_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    updates = []
    monkeypatch.setattr(ct, "update_quotation_job_result", lambda *args, **kwargs: updates.append(args) or {"queued": True})
    monkeypatch.setattr(ct, "report_quotation_started", lambda *args, **kwargs: {"sent": False})
    monkeypatch.setattr(ct, "report_quotation_finished", lambda *args, **kwargs: {"sent": False})
    monkeypatch.setattr(ct, "report_carrier_quotation_result", lambda *args, **kwargs: {"sent": False})
    shadow_calls = []
    monkeypatch.setattr(
        ct,
        "normalize_quotation_remote_shadow",
        lambda *args, **kwargs: shadow_calls.append((args, kwargs)) or {"queued": True},
    )

    async def fake_execute(**kwargs):
        return [
            ct.ResultadoCotacao(
                transportadora="BRASPRESS",
                status="ok",
                valor_frete=55.25,
                prazo_dias=2,
                duration_ms=900,
            )
        ]

    monkeypatch.setattr(ct, "_executar_cotacoes_com_dados", fake_execute)

    resultados = asyncio.run(
        ct.cotar_transportadoras_romaneio_colado(
            romaneio_colado=_valid_romaneio(),
        )
    )

    assert resultados[0].transportadora == "BRASPRESS"
    assert resultados[0].status == "ok"
    assert resultados[0].valor_frete == 55.25
    assert updates == []
    assert shadow_calls
    assert shadow_calls[0][0][0] == "manual"
    assert shadow_calls[0][1]["wait"] is False
