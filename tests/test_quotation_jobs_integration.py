import asyncio
import sys
import threading
import time
from pathlib import Path
from urllib.error import URLError


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import cotacao_transportadoras as ct
from cotacao import jobs_client as _jobs_client_mod
from cotacao import deps


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
        "progress_status": "finalizada",
        "stage": "resultado",
        "message": "R$ 123.45 | 3 dia(s)",
        "prazo_dias": 3,
    }
    assert trd["status"] == "disabled"
    assert result["summary"]["success_count"] == 1
    assert result["total_providers"] == result["summary"]["total_providers"]
    assert result["success_count"] == 1
    assert result["error_count"] == result["summary"]["error_count"]
    assert result["disabled_count"] == result["summary"]["disabled_count"]


def test_quotation_job_result_includes_friendly_provider_progress_for_failures():
    config = {
        "transportadoras": {
            "trd": {"habilitado": True},
            "agex": {"habilitado": True},
        }
    }

    result = ct._quotation_job_result_payload(
        config,
        [
            ct.ResultadoCotacao(
                transportadora="TRD",
                status="sem_cotacao",
                detalhes="Sem resultado",
                duration_ms=8000,
                stage="resultado",
            ),
            ct.ResultadoCotacao(
                transportadora="AGEX",
                status="nao_atendido",
                detalhes="Destino fora de cobertura",
                stage="validacao",
            ),
        ],
    )

    trd = next(item for item in result["transportadoras"] if item["provider"] == "trd")
    agex = next(item for item in result["transportadoras"] if item["provider"] == "agex")

    assert trd["status"] == "error"
    assert trd["progress_status"] == "erro"
    assert trd["stage"] == "resultado"
    assert trd["message"] == "Sem cotação retornada"
    assert trd["duration_ms"] == 8000

    assert agex["status"] == "error"
    assert agex["progress_status"] == "nao_atendido"
    assert agex["stage"] == "validacao"
    assert agex["message"] == "UF não atendida"


def test_quotation_job_final_status_requires_success():
    assert ct._quotation_job_final_status({"success_count": 1}, general_error=False) == "finished"
    assert ct._quotation_job_final_status({"success_count": 0}, general_error=False) == "error"
    assert ct._quotation_job_final_status({"summary": {"success_count": 1}}, general_error=True) == "finished"


def test_cotacao_continues_when_create_quotation_job_fails(monkeypatch):
    config = {
        "fretio": {},
        "transportadoras": {"braspress": {"habilitado": True}},
    }
    monkeypatch.setattr(ct, "_carregar_config", lambda config_path=None: config)
    monkeypatch.setattr(deps, "create_quotation_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    updates = []
    monkeypatch.setattr(deps, "update_quotation_job_result", lambda *args, **kwargs: updates.append(args) or {"queued": True})
    monkeypatch.setattr(deps, "report_quotation_started", lambda *args, **kwargs: {"sent": False})
    monkeypatch.setattr(deps, "report_quotation_finished", lambda *args, **kwargs: {"sent": False})
    monkeypatch.setattr(deps, "report_carrier_quotation_result", lambda *args, **kwargs: {"sent": False})
    shadow_calls = []
    monkeypatch.setattr(deps, "normalize_quotation_remote_shadow",
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


def test_cotacao_updates_job_running_and_finished(monkeypatch):
    config = {
        "fretio": {},
        "transportadoras": {"braspress": {"habilitado": True}},
    }
    monkeypatch.setattr(ct, "_carregar_config", lambda config_path=None: config)
    monkeypatch.setattr(deps, "create_quotation_job", lambda *args, **kwargs: {"job_id": 123})
    updates = []
    monkeypatch.setattr(deps, "update_quotation_job_result",
        lambda *args, **kwargs: updates.append((args, kwargs)) or {"queued": True},
    )
    monkeypatch.setattr(deps, "report_quotation_started", lambda *args, **kwargs: {"sent": False})
    monkeypatch.setattr(deps, "report_quotation_finished", lambda *args, **kwargs: {"sent": False})
    monkeypatch.setattr(deps, "report_carrier_quotation_result", lambda *args, **kwargs: {"sent": False})
    monkeypatch.setattr(deps, "normalize_quotation_remote_shadow", lambda *args, **kwargs: {"queued": True})

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

    assert resultados[0].status == "ok"
    assert [call[0][1] for call in updates] == ["running", "finished"]
    final_payload = updates[-1][1]["result"]
    assert final_payload["success_count"] == 1
    assert final_payload["transportadoras"][0]["provider"] == "braspress"


def test_create_quotation_job_best_effort_does_not_block_when_slow(monkeypatch):
    """Uma criação de job lenta (rede travada) não deve atrasar o início da cotação.

    A espera é limitada por ``_JOB_CREATE_WAIT_S``; ao estourar retornamos sem
    job_id e a cotação segue, enquanto a chamada HTTP termina em background.
    """
    jobs = _jobs_client_mod
    started = threading.Event()
    release = threading.Event()

    def slow_create(*args, **kwargs):
        started.set()
        # Simula uma chamada HTTP travada (DNS/servidor lento).
        release.wait(timeout=5)
        return {"job_id": 999}

    monkeypatch.setattr(deps, "create_quotation_job", slow_create)
    monkeypatch.setattr(jobs, "_JOB_CREATE_WAIT_S", 0.2)

    inicio = time.monotonic()
    job_id = jobs._create_quotation_job_best_effort("manual", {"modo": "romaneio_colado"})
    elapsed = time.monotonic() - inicio

    assert job_id is None
    assert started.is_set()
    # Não pode aguardar a chamada completa: deve respeitar o limite curto.
    assert elapsed < 2.0
    release.set()


def test_create_quotation_job_best_effort_returns_job_id_when_fast(monkeypatch):
    jobs = _jobs_client_mod
    monkeypatch.setattr(deps, "create_quotation_job", lambda *a, **k: {"job_id": 321})
    monkeypatch.setattr(jobs, "_JOB_CREATE_WAIT_S", 2.0)

    job_id = jobs._create_quotation_job_best_effort("manual", {"modo": "romaneio_colado"})

    assert job_id == 321
