import sys
import time
from pathlib import Path
from threading import Event


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))

import quotation_normalization_shadow as shadow


def _sample_dados():
    return {
        "destino_cep": "99740-000",
        "uf_destino": "rs",
        "cnpj_destinatario": "12.345.678/0001-90",
        "peso": "12,500",
        "valor": "1.234,56",
        "volumes": "2",
        "cubagem_m3": "0,123",
        "cubagens": [
            {
                "quantidade": "2",
                "comprimento_cm": "45",
                "largura_cm": "31",
                "altura_cm": "31",
                "peso_por_volume_kg": 6.25,
            }
        ],
        "descricoes_itens": ["Produto interno"],
        "texto_romaneio": "texto bruto que nao deve sair",
        "senha": "segredo",
        "token": "token-secreto",
    }


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_build_shadow_payload_removes_sensitive_fields():
    payload = shadow.build_shadow_payload(_sample_dados(), cep_origem="99.740-000", modo="pdf")

    assert payload == {
        "modo": "pdf",
        "cep_origem": "99740000",
        "destino_cep": "99740-000",
        "uf_destino": "rs",
        "volumes": "2",
        "peso": "12,500",
        "valor": "1.234,56",
        "cubagem_m3": "0,123",
        "cubagens": _sample_dados()["cubagens"],
    }
    assert "cnpj_destinatario" not in payload
    assert "descricoes_itens" not in payload
    assert "texto_romaneio" not in payload
    assert "senha" not in payload
    assert "token" not in payload


def test_build_local_normalized_data_converts_local_aliases():
    local = shadow.build_local_normalized_data(_sample_dados(), cep_origem="99.740-000")

    assert local["cep_origem"] == "99740000"
    assert local["cep_destino"] == "99740000"
    assert local["uf_destino"] == "RS"
    assert local["volumes"] == 2
    assert local["peso_total_kg"] == 12.5
    assert local["valor_nf"] == 1234.56
    assert local["cubagem_m3"] == 0.123
    assert local["medidas"] == [
        {
            "quantidade": 2,
            "comprimento_cm": 45,
            "largura_cm": 31,
            "altura_cm": 31,
        }
    ]


def test_compare_normalized_data_matches_equivalent_values():
    local = shadow.build_local_normalized_data(_sample_dados(), cep_origem="99.740-000")
    remote = {
        "cep_origem": "99740-000",
        "cep_destino": "99.740-000",
        "uf_destino": "rs",
        "volumes": "2",
        "peso_total_kg": 12.5004,
        "valor_nf": "1.234,560",
        "cubagem_m3": "0,1234",
        "medidas": [
            {
                "quantidade": "2",
                "comprimento_cm": "45",
                "largura_cm": "31",
                "altura_cm": "31",
            }
        ],
    }

    assert shadow.compare_normalized_data(local, remote) == {"match": True, "different_fields": []}


def test_compare_normalized_data_reports_different_weight_and_volumes():
    local = shadow.build_local_normalized_data(_sample_dados(), cep_origem="99.740-000")
    remote = dict(local)
    remote["peso_total_kg"] = 20
    remote["volumes"] = 3

    comparison = shadow.compare_normalized_data(local, remote)

    assert comparison["match"] is False
    assert comparison["different_fields"] == ["volumes", "peso_total_kg"]


def test_run_shadow_normalization_does_not_call_remote_when_disabled(monkeypatch):
    monkeypatch.delenv("FRETIO_QUOTATION_NORMALIZATION_SHADOW", raising=False)
    called = False

    def fake_normalize(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    shadow.run_shadow_normalization(
        "manual",
        {"fretio": {"quotation_normalization_shadow_enabled": False}},
        _sample_dados(),
        cep_origem="99740000",
        modo="romaneio_colado",
        normalize_func=fake_normalize,
    )

    time.sleep(0.05)
    assert called is False


def test_run_shadow_normalization_calls_remote_when_enabled(monkeypatch):
    monkeypatch.delenv("FRETIO_QUOTATION_NORMALIZATION_SHADOW", raising=False)
    called = Event()
    captured = {}

    def fake_normalize(source_type, payload=None, wait=False):
        captured["source_type"] = source_type
        captured["payload"] = payload
        captured["wait"] = wait
        called.set()
        return {
            "sent": True,
            "normalized": True,
            "data": {"quotation_data": shadow.build_local_normalized_data(_sample_dados(), "99740000")},
        }

    shadow.run_shadow_normalization(
        "manual",
        {"fretio": {"quotation_normalization_shadow_enabled": True}},
        _sample_dados(),
        cep_origem="99740000",
        modo="romaneio_colado",
        normalize_func=fake_normalize,
    )

    assert called.wait(2)
    assert captured["source_type"] == "manual"
    assert captured["wait"] is True
    assert captured["payload"]["modo"] == "romaneio_colado"
    assert "texto_romaneio" not in captured["payload"]
    assert "cnpj_destinatario" not in captured["payload"]


def test_run_shadow_normalization_logs_divergence(monkeypatch):
    monkeypatch.delenv("FRETIO_QUOTATION_NORMALIZATION_SHADOW", raising=False)
    logs = []
    local = shadow.build_local_normalized_data(_sample_dados(), "99740000")
    remote = dict(local)
    remote["peso_total_kg"] = 99

    def fake_normalize(source_type, payload=None, wait=False):
        return {
            "sent": True,
            "normalized": True,
            "data": {"quotation_data": remote},
        }

    shadow.run_shadow_normalization(
        "romaneio",
        {"fretio": {"quotation_normalization_shadow_enabled": True}},
        _sample_dados(),
        cep_origem="99740000",
        modo="pdf",
        log_func=logs.append,
        normalize_func=fake_normalize,
    )

    assert _wait_for(lambda: any("Normalização remota sombra divergiu" in item for item in logs))
    assert any("campos=peso_total_kg" in item for item in logs)
