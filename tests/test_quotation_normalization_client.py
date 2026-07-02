import json
import ssl
import sys
from pathlib import Path
from urllib.error import URLError


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app"))

import quotation_normalization_client as qn
from quotation_shadow_compare import build_shadow_payload, compare_quotation_normalization


class FakeResponse:
    status = 200

    def __init__(self, payload=None, status=200):
        self.payload = payload if payload is not None else {}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _prepare_identity(monkeypatch):
    qn.configure(None)
    monkeypatch.setattr(qn, "get_saved_license", lambda: "FBOT-ABCD-1234-EFGH-5678")
    monkeypatch.setattr(qn, "get_machine_id", lambda: "machine-123")


def _local_payload():
    return {
        "modo": "romaneio_colado",
        "destino_cep": "90010-123",
        "uf_destino": "rs",
        "cnpj_destinatario": "12.345.678/0001-90",
        "peso": 3.3,
        "valor": 150.99,
        "volumes": 2,
        "cubagem_m3": 0.044,
        "cubagens": [
            {
                "quantidade": 2,
                "comprimento_cm": 45,
                "largura_cm": 31,
                "altura_cm": 31,
                "peso_por_volume_kg": 1.65,
            }
        ],
        "descricoes_itens": ["Produto Teste"],
    }


def test_shadow_normalization_posts_sanitized_payload_and_compares(monkeypatch):
    _prepare_identity(monkeypatch)
    captured = {}

    def fake_urlopen(req, timeout=6, context=None):
        assert isinstance(context, ssl.SSLContext)
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse(
            {
                "source_type": "manual",
                "modo": "romaneio_colado",
                "ready_for_quotation": True,
                "missing_fields": [],
                "warnings": [],
                "quotation_data": {
                    "cep_destino": "90010123",
                    "uf_destino": "RS",
                    "volumes": 2,
                    "peso_total_kg": 3.3,
                    "valor_nf": 150.99,
                    "cubagem_m3": 0.044,
                    "medidas": [
                        {
                            "quantidade": 2,
                            "comprimento_cm": 45,
                            "largura_cm": 31,
                            "altura_cm": 31,
                        }
                    ],
                },
            }
        )

    monkeypatch.setenv("FRETIO_QUOTATION_NORMALIZATION_API_URL", "https://normalize.example.test/api/quotations/normalize")
    monkeypatch.setattr(qn, "urlopen", fake_urlopen)

    result = qn.normalize_quotation_remote_shadow("manual", payload=_local_payload(), wait=True)

    assert result["sent"] is True
    assert result["comparison"]["matched"] is True
    assert captured["url"] == "https://normalize.example.test/api/quotations/normalize"
    assert captured["payload"]["license_key"] == "FBOT-ABCD-1234-EFGH-5678"
    assert captured["payload"]["machine_id"] == "machine-123"
    assert captured["payload"]["source_type"] == "manual"
    assert set(captured["payload"]) == {"license_key", "machine_id", "source_type", "payload"}
    assert captured["payload"]["payload"] == {
        "modo": "romaneio_colado",
        "cep_destino": "90010123",
        "uf_destino": "RS",
        "volumes": 2,
        "peso_total_kg": 3.3,
        "valor_nf": 150.99,
        "cubagem_m3": 0.044,
        "medidas": [
            {
                "comprimento_cm": 45,
                "largura_cm": 31,
                "altura_cm": 31,
                "quantidade": 2,
            }
        ],
    }
    serialized = json.dumps(captured["payload"], ensure_ascii=False)
    assert "12.345.678/0001-90" not in serialized
    assert "Produto Teste" not in serialized


def test_shadow_normalization_url_can_come_from_config(monkeypatch, tmp_path):
    _prepare_identity(monkeypatch)
    monkeypatch.delenv("FRETIO_QUOTATION_NORMALIZATION_API_URL", raising=False)
    monkeypatch.delenv("FRETEBOT_QUOTATION_NORMALIZATION_API_URL", raising=False)
    config_path = tmp_path / "CONFIG.toml"
    config_path.write_text(
        "[fretio]\n"
        'quotation_normalization_api_url = "https://config.example.test/normalize"\n',
        encoding="utf-8",
    )
    qn.configure(config_path)
    captured = {}

    def fake_urlopen(req, timeout=6, context=None):
        captured["url"] = req.full_url
        return FakeResponse({"ready_for_quotation": False, "missing_fields": [], "quotation_data": {}})

    monkeypatch.setattr(qn, "urlopen", fake_urlopen)

    qn.normalize_quotation_remote_shadow("manual", payload={}, wait=True)

    assert captured["url"] == "https://config.example.test/normalize"


def test_shadow_normalization_without_identity_does_not_call_server(monkeypatch):
    qn.configure(None)
    monkeypatch.setattr(qn, "get_saved_license", lambda: "")
    monkeypatch.setattr(qn, "get_machine_id", lambda: "machine-123")
    called = False

    def fake_urlopen(*args, **kwargs):
        nonlocal called
        called = True
        return FakeResponse()

    monkeypatch.setattr(qn, "urlopen", fake_urlopen)

    result = qn.normalize_quotation_remote_shadow("manual", payload=_local_payload(), wait=True)

    assert result["skipped"] is True
    assert called is False


def test_shadow_normalization_network_failure_does_not_raise(monkeypatch):
    _prepare_identity(monkeypatch)
    monkeypatch.setenv("FRETIO_QUOTATION_NORMALIZATION_API_URL", "https://normalize.example.test")
    monkeypatch.setattr(qn, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(URLError("offline")))

    result = qn.normalize_quotation_remote_shadow("manual", payload=_local_payload(), wait=True)

    assert result["sent"] is False
    assert result["status_code"] is None


def test_shadow_normalization_wait_false_only_queues(monkeypatch):
    queued = {}

    def fake_run(target, *args, **kwargs):
        queued["target"] = target
        queued["args"] = args
        queued["kwargs"] = kwargs

    monkeypatch.setattr(qn, "_run_in_background", fake_run)

    result = qn.normalize_quotation_remote_shadow("manual", payload={"volumes": 1}, wait=False)

    assert result["queued"] is True
    assert queued["args"] == ("manual", {"volumes": 1})


def test_shadow_compare_reports_mismatch_without_using_server_data():
    comparison = compare_quotation_normalization(
        {"destino_cep": "90010-123", "volumes": 2, "peso": 3.3},
        {
            "ready_for_quotation": True,
            "missing_fields": [],
            "quotation_data": {"cep_destino": "90010123", "volumes": 1, "peso_total_kg": 3.3},
        },
    )

    assert comparison["matched"] is False
    assert {"field": "volumes", "local": 2, "remote": 1} in comparison["differences"]


def test_build_shadow_payload_maps_local_fields_only():
    payload = build_shadow_payload(_local_payload(), cep_origem="99740-000")

    assert payload["cep_origem"] == "99740000"
    assert payload["cep_destino"] == "90010123"
    assert payload["peso_total_kg"] == 3.3
    assert "cnpj_destinatario" not in payload
    assert "descricoes_itens" not in payload
