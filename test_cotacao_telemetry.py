import sys
from pathlib import Path


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from cotacao.common import ResultadoCotacao
from cotacao.telemetry import _quotation_usage_metadata, _report_quotation_usage_results


def test_quotation_usage_metadata_keeps_only_non_sensitive_shipping_fields():
    dados = {
        "uf_destino": "rs",
        "volumes": 2,
        "peso": 3.34567,
        "cnpj_destinatario": "12.345.678/0001-90",
        "destino_cep": "90010-123",
    }

    metadata = _quotation_usage_metadata(dados, modo="manual", quantidade_transportadoras=3, job_id="job-1")

    assert metadata == {
        "modo": "manual",
        "job_id": "job-1",
        "quantidade_transportadoras": 3,
        "uf_destino": "RS",
        "volumes": 2,
        "peso_total_kg": 3.346,
    }


def test_report_quotation_usage_results_uses_sanitized_metadata(monkeypatch):
    finished_calls = []
    carrier_calls = []
    monkeypatch.setattr("cotacao.deps.report_quotation_finished", lambda *a, **kw: finished_calls.append((a, kw)))
    monkeypatch.setattr("cotacao.deps.report_carrier_quotation_result", lambda *a, **kw: carrier_calls.append((a, kw)))

    _report_quotation_usage_results(
        config={"transportadoras": {"braspress": {"habilitado": True}}},
        dados={"uf_destino": "RS", "volumes": 1, "peso": 10, "cnpj_destinatario": "12345678000190"},
        resultados=[ResultadoCotacao(transportadora="BRASPRESS", status="ok", valor_frete=12.34, duration_ms=5)],
        modo="manual",
        duration_ms=10,
        job_id="job-1",
    )

    assert finished_calls
    metadata = finished_calls[0][1]["metadata"]
    assert "cnpj_destinatario" not in metadata
    assert metadata["uf_destino"] == "RS"
    assert any(call[0][:2] == ("braspress", "ok") for call in carrier_calls)
