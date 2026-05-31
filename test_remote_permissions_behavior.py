import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import cotacao_transportadoras as ct
import remote_permissions as rp


def test_allow_cotacao_false_blocks_cotacao(monkeypatch):
    monkeypatch.setattr(rp, "is_feature_allowed", lambda feature: feature != "cotacao")

    assert rp.ensure_feature_allowed("cotacao") is False
    assert rp.feature_message("cotacao") == "Este módulo foi desabilitado pela configuração da licença."


def test_allow_rastreio_false_blocks_rastreio(monkeypatch):
    monkeypatch.setattr(rp, "is_feature_allowed", lambda feature: feature != "rastreio")

    assert rp.ensure_feature_allowed("rastreio") is False
    assert rp.feature_message("rastreio") == "Este módulo foi desabilitado pela configuração da licença."


def test_without_remote_cache_allows_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("FRETIO_LICENSE_CONFIG_API_URL", raising=False)
    monkeypatch.delenv("FRETEBOT_LICENSE_CONFIG_API_URL", raising=False)

    assert rp.feature_allowed_or_default("cotacao") is True
    assert rp.feature_allowed_or_default("rastreio") is True
    assert rp.carrier_enabled_or_message("Braspress") == (True, "")


def test_remote_config_failure_does_not_break_permissions(monkeypatch):
    monkeypatch.setattr(rp, "is_feature_allowed", lambda feature: (_ for _ in ()).throw(OSError("cache falhou")))
    monkeypatch.setattr(rp, "is_carrier_enabled", lambda carrier: (_ for _ in ()).throw(OSError("cache falhou")))

    assert rp.ensure_feature_allowed("cotacao") is True
    assert rp.carrier_enabled_or_message("TRD") == (True, "")


def test_disabled_carrier_is_skipped_before_provider_creation(monkeypatch):
    create_calls = []

    class FakeProvider:
        async def coteir(self, **kwargs):
            raise AssertionError("provider desabilitado nao deveria cotar")

    class FakeFactory:
        def __init__(self, config):
            self.config = config

        def is_available(self, nome):
            return nome == "braspress"

        def get_provider_config(self, nome):
            transportadoras = self.config.get("transportadoras", {})
            return dict(transportadoras.get(nome, {"habilitado": False}))

        def create(self, nome, **kwargs):
            create_calls.append((nome, kwargs))
            return FakeProvider()

    monkeypatch.setattr(ct, "ProviderFactory", FakeFactory)
    monkeypatch.setattr(
        ct,
        "carrier_enabled_or_message",
        lambda carrier: (
            False,
            ct.CARRIER_DISABLED_MESSAGE,
        )
        if str(carrier).lower() == "braspress"
        else (True, ""),
    )
    monkeypatch.setattr(ct, "_diag_log_enabled", lambda: False)

    config = {
        "fretio": {},
        "romaneio": {"cep_origem": "99740000"},
        "transportadoras": {
            "braspress": {
                "habilitado": True,
                "cnpj": "12345678000190",
                "senha": "secret",
                "ufs_atendidas": ["RS"],
            }
        },
    }
    dados = {
        "destino_cep": "90010123",
        "uf_destino": "RS",
        "cnpj_destinatario": "12345678000190",
        "peso": 3.3,
        "valor": 150.99,
        "volumes": 1,
        "cubagem_m3": 0.044,
        "cubagens": [
            {
                "quantidade": 1,
                "comprimento_cm": 45,
                "largura_cm": 31,
                "altura_cm": 31,
                "peso_por_volume_kg": 3.3,
            }
        ],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(
        ct._executar_cotacoes_com_dados(
            config=config,
            dados=dados,
            cep_origem="99740000",
        )
    )

    assert create_calls == []
    assert len(resultados) == 1
    assert resultados[0].transportadora == "BRASPRESS"
    assert resultados[0].status == "desabilitada"
    assert resultados[0].detalhes == ct.CARRIER_DISABLED_MESSAGE
