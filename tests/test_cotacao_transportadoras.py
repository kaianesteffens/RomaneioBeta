import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

import cotacao_transportadoras as ct
from cotacao import deps
from cotacao import orchestrator as cotacao_orchestrator
from cotacao import session_manager as cotacao_session_manager


def test_cep_helpers_normalize_map_uf_and_filter_supported_states():
    assert ct._cep("90.010-123") == "90010123"
    assert ct._cep_para_uf("90010-123") == "RS"
    assert ct._uf_atendida(["rs", "sc"], "RS") is True
    assert ct._uf_atendida("SP, PR", "RS") is False
    assert ct._uf_atendida([], None) is True


def test_resolver_cep_origem_uses_expected_precedence():
    config = {
        "romaneio": {"cep_origem": "11.222-333"},
        "transportadoras": {
            "braspress": {"cep_origem": "22.333-444"},
        },
    }

    assert ct._resolver_cep_origem(config, "44.555-666") == "44555666"
    assert ct._resolver_cep_origem(config, "") == "11222333"
    assert ct._resolver_cep_origem({"transportadoras": {"trd": {"cep_origem": "55.666-777"}}}, "") == "55666777"
    assert ct._resolver_cep_origem({}, "") == ct.CEP_ORIGEM_PADRAO


def test_obter_cep_origem_default_applies_remote_override(monkeypatch):
    monkeypatch.setattr(
        ct,
        "_carregar_config",
        lambda config_path=None: {"romaneio": {"cep_origem": "11111111"}},
    )
    monkeypatch.setattr(
        ct,
        "apply_safe_runtime_overrides",
        lambda config: {"romaneio": {"cep_origem": "99.740-000"}},
    )

    assert ct.obter_cep_origem_default() == "99740000"


def test_remote_cep_origem_override_is_used_before_origin_validation(monkeypatch):
    monkeypatch.setattr(ct, "_diag_log_enabled", lambda: False)
    monkeypatch.setattr(
        cotacao_orchestrator,
        "apply_safe_runtime_overrides",
        lambda config: {
            "romaneio": {"cep_origem": "99740000"},
            "fretio": {},
            "transportadoras": {},
        },
    )

    class FakeFactory:
        def __init__(self, config):
            self.config = config

        def get_provider_config(self, nome):
            return {"habilitado": False}

        def is_available(self, nome):
            return False

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)

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
            config={"romaneio": {"cep_origem": "000"}, "fretio": {}, "transportadoras": {}},
            dados=dados,
            cep_origem="",
        )
    )

    assert resultados == []


def test_text_normalization_and_dimension_parsing_are_stable():
    texto = "<p>Linha 1</p><br>Linha&nbsp;&nbsp;2\r\n<div>Linha 3</div>"

    assert ct._normalizar_romaneio_colado(texto) == "Linha 1\nLinha 2\nLinha 3"
    assert ct._parse_dim_cm("0,31") == 31
    assert ct._parse_dim_cm("31") == 31
    assert ct._parse_dim_cm("invalido") == 0


def test_selecionar_cep_destino_prefers_matching_uf_after_reference():
    texto = (
        "CEP anterior 01001-000\n"
        "CNPJ/CPF: 12.345.678/0001-90\n"
        "Cidade: Porto Alegre / RS\n"
        "Outro CEP 80010-000\n"
        "CEP: 90010-123\n"
    )
    pos_ref = texto.index("CNPJ/CPF")

    assert ct._selecionar_cep_destino(texto, pos_referencia=pos_ref, uf_hint="RS") == "90010123"


def test_dados_envio_romaneio_colado_extracts_core_shipping_fields():
    romaneio = """
    DESTINATARIO
    CNPJ/CPF: 12.345.678/0001-90
    Endereco: Rua Exemplo, 123
    Cidade: Porto Alegre / RS
    CEP: 90010-123
    - VOL: 2
    - CUBAGEM: 0,044 m3
    - PESO: 3,300 kg
    - TOTAL: R$ 150,99
    2 x Caixas fechadas - 1,650 kg - 0,044 m3 - 31x31x45
    Produto Teste: 2 und
    """

    dados = ct._dados_envio_romaneio_colado(romaneio)

    assert dados["destino_cep"] == "90010123"
    assert dados["uf_destino"] == "RS"
    assert dados["cnpj_destinatario"] == "12345678000190"
    assert dados["peso"] == 3.3
    assert dados["valor"] == 150.99
    assert dados["volumes"] == 2
    assert dados["cubagem_m3"] == 0.044
    assert dados["comprimento_cm"] == 45
    assert dados["largura_cm"] == 31
    assert dados["altura_cm"] == 31
    assert dados["cubagens"] == [
        {
            "quantidade": 2,
            "comprimento_cm": 45,
            "largura_cm": 31,
            "altura_cm": 31,
            "peso_por_volume_kg": 1.65,
        }
    ]
    assert dados["descricoes_itens"] == ["Produto Teste"]


def test_cubagens_validas_discards_invalid_rows():
    cubagens = ct._cubagens_validas(
        [
            {"quantidade": 2, "comprimento_cm": 45, "largura_cm": 31, "altura_cm": 31, "peso_por_volume_kg": "1.65"},
            {"quantidade": 0, "comprimento_cm": 10, "largura_cm": 10, "altura_cm": 10},
            {"quantidade": 1, "comprimento_cm": 30, "largura_cm": 20, "altura_cm": 10, "peso_por_volume_kg": "-5"},
            "invalido",
        ]
    )

    assert cubagens == [
        {
            "quantidade": 2,
            "comprimento_cm": 45,
            "largura_cm": 31,
            "altura_cm": 31,
            "peso_por_volume_kg": 1.65,
        },
        {
            "quantidade": 1,
            "comprimento_cm": 30,
            "largura_cm": 20,
            "altura_cm": 10,
            "peso_por_volume_kg": None,
        },
    ]


def test_rodonaves_setup_error_does_not_escape_except_scope(monkeypatch):
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))

    class FakeFactory:
        def __init__(self, config):
            self.config = config

        def is_available(self, nome):
            return nome == "rodonaves"

        def get_provider_config(self, nome):
            if nome != "rodonaves":
                return {"habilitado": False}
            return {
                "habilitado": True,
                "dominio": "RTE",
                "usuario": "user",
                "senha": "secret",
                "cnpj_pagador": "12.345.678/0001-90",
                "headless": True,
                "ufs_atendidas": ["RS"],
            }

        def create(self, nome, **kwargs):
            raise RuntimeError("falha fake rodonaves")

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)

    config = {
        "fretio": {},
        "romaneio": {"cep_origem": "99740000"},
        "transportadoras": {"rodonaves": {"habilitado": True}},
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

    assert len(resultados) == 1
    assert resultados[0].transportadora == "RODONAVES"
    assert resultados[0].status == "erro"
    assert "falha fake rodonaves" in resultados[0].detalhes


def test_session_lazy_prelogin_runs_for_new_trd_provider(monkeypatch):
    monkeypatch.setattr(ct, "_carregar_config", lambda config_path=None: {})

    class FakeProvider:
        def __init__(self):
            self.pre_login_calls = 0
            self._logged_in = False

        async def pre_login(self):
            self.pre_login_calls += 1
            self._logged_in = True
            return True

    async def scenario():
        session = ct.TransportadoraSession()
        provider = FakeProvider()
        same_provider = await session.assegurar_provider("trd", lambda: provider)
        await session.assegurar_provider("trd", lambda: FakeProvider())
        return provider, same_provider

    provider, same_provider = asyncio.run(scenario())

    assert same_provider is provider
    assert provider.pre_login_calls == 1


# ── Testes de tratamento de erros reportados ──────────────────────────────────


def test_eucatur_mais_de_11_volumes_com_cubagem_nao_bloqueia(monkeypatch):
    """Eucatur > 11 volumes deve seguir para o provider quando há cubagem total."""
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))
    reportados = []
    monkeypatch.setattr(deps, "report_error_message", lambda *a, **kw: reportados.append(a))
    monkeypatch.setattr(deps, "report_error", lambda *a, **kw: reportados.append(a))
    chamadas = []

    class FakeEucaturProvider:
        nome = "EUCATUR"
        last_error = None
        _passo_atual = "inicio"

        async def coteir(self, **kwargs):
            chamadas.append(kwargs)
            from fretio.models import Cotacao
            return Cotacao(transportadora="EUCATUR", prazo_dias=2, valor_frete=321.0)

        async def cleanup(self):
            pass

    class FakeFactory:
        def __init__(self, config):
            pass

        def is_available(self, nome):
            return nome == "eucatur"

        def get_provider_config(self, nome):
            if nome == "eucatur":
                return {
                    "habilitado": True,
                    "dominio": "DOM",
                    "usuario": "usr",
                    "senha": "pw",
                    "cnpj_pagador": "00000000000191",
                    "ufs_atendidas": ["AM"],
                }
            return {"habilitado": False}

        def create(self, nome, **kwargs):
            return FakeEucaturProvider()

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)

    config = {
        "fretio": {},
        "romaneio": {"cep_origem": "69000000"},
        "transportadoras": {"eucatur": {"habilitado": True}},
    }
    dados = {
        "destino_cep": "69000100",
        "uf_destino": "AM",
        "cnpj_destinatario": "12345678000190",
        "peso": 10.0,
        "valor": 500.0,
        "volumes": 12,
        "cubagem_m3": 0.288,
        "cubagens": [{"quantidade": 12, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 20}],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(
        ct._executar_cotacoes_com_dados(config=config, dados=dados, cep_origem="69000000")
    )

    eucatur_results = [r for r in resultados if r.transportadora == "EUCATUR"]
    assert len(eucatur_results) == 1
    assert eucatur_results[0].status == "ok"
    assert eucatur_results[0].valor_frete == 321.0
    assert chamadas[0]["volumes"] == 12
    assert chamadas[0]["cubagem_m3"] == 0.288
    assert len(reportados) == 0


def test_coopex_mais_de_11_volumes_com_cubagem_nao_bloqueia(monkeypatch):
    """COOPEX > 11 volumes deve seguir para o provider quando há cubagem total."""
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))
    reportados = []
    monkeypatch.setattr(deps, "report_error_message", lambda *a, **kw: reportados.append(a))
    monkeypatch.setattr(deps, "report_error", lambda *a, **kw: reportados.append(a))
    chamadas = []

    class FakeCoopexProvider:
        nome = "COOPEX"
        last_error = None
        _passo_atual = "inicio"

        async def coteir(self, **kwargs):
            chamadas.append(kwargs)
            from fretio.models import Cotacao
            return Cotacao(transportadora="COOPEX", prazo_dias=2, valor_frete=432.1)

        async def cleanup(self):
            pass

    class FakeFactory:
        def __init__(self, config):
            pass

        def is_available(self, nome):
            return nome == "coopex"

        def get_provider_config(self, nome):
            if nome == "coopex":
                return {
                    "habilitado": True,
                    "dominio": "DOM",
                    "usuario": "usr",
                    "senha": "pw",
                    "cnpj_pagador": "00000000000191",
                    "ufs_atendidas": ["SP"],
                }
            return {"habilitado": False}

        def create(self, nome, **kwargs):
            return FakeCoopexProvider()

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)

    config = {
        "fretio": {},
        "romaneio": {"cep_origem": "01310100"},
        "transportadoras": {"coopex": {"habilitado": True}},
    }
    dados = {
        "destino_cep": "01310200",
        "uf_destino": "SP",
        "cnpj_destinatario": "12345678000190",
        "peso": 10.0,
        "valor": 500.0,
        "volumes": 12,
        "cubagem_m3": 0.288,
        "cubagens": [{"quantidade": 12, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 20}],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(
        ct._executar_cotacoes_com_dados(config=config, dados=dados, cep_origem="01310100")
    )

    coopex_results = [r for r in resultados if r.transportadora == "COOPEX"]
    assert len(coopex_results) == 1
    assert coopex_results[0].status == "ok"
    assert coopex_results[0].valor_frete == 432.1
    assert chamadas[0]["volumes"] == 12
    assert chamadas[0]["cubagem_m3"] == 0.288
    assert len(reportados) == 0


def test_translovato_cotacao_e_enfileirada_com_kwargs_esperados(monkeypatch):
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))
    chamadas = []

    class FakeTranslovatoProvider:
        nome = "TRANSLOVATO"
        last_error = None
        _passo_atual = "inicio"

        async def coteir(self, **kwargs):
            chamadas.append(kwargs)
            from fretio.models import Cotacao
            return Cotacao(transportadora="TRANSLOVATO", prazo_dias=6, valor_frete=654.32)

        async def cleanup(self):
            pass

    class FakeFactory:
        def __init__(self, config):
            self.config = config

        def is_available(self, nome):
            return nome == "translovato"

        def get_provider_config(self, nome):
            if nome == "translovato":
                return {
                    "habilitado": True,
                    "cnpj": "12.345.678/0001-90",
                    "usuario": "usr",
                    "senha": "pw",
                    "cnpj_remetente": "98.765.432/0001-10",
                    "headless": True,
                    "ufs_atendidas": ["RS"],
                }
            return {"habilitado": False}

        def validate_minimum_config(self, nome):
            from fretio.providers.factory import validate_provider_minimum_config
            return validate_provider_minimum_config(nome, self.get_provider_config(nome))

        def create(self, nome, **kwargs):
            assert nome == "translovato"
            return FakeTranslovatoProvider()

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)

    config = {
        "fretio": {},
        "romaneio": {"cep_origem": "90000000"},
        "transportadoras": {"translovato": {"habilitado": True}},
    }
    dados = {
        "destino_cep": "90010123",
        "uf_destino": "RS",
        "cnpj_destinatario": "12345678000190",
        "peso": 3.3,
        "valor": 150.0,
        "volumes": 1,
        "cubagem_m3": 0.044,
        "cubagens": [{"quantidade": 1, "comprimento_cm": 45, "largura_cm": 31, "altura_cm": 31}],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(
        ct._executar_cotacoes_com_dados(config=config, dados=dados, cep_origem="90000000")
    )

    translovato_results = [r for r in resultados if r.transportadora == "TRANSLOVATO"]
    assert len(translovato_results) == 1
    assert translovato_results[0].status == "ok"
    assert chamadas[0]["origem"] == "90000000"
    assert chamadas[0]["destino"] == "90010123"
    assert chamadas[0]["cep_origem"] == "90000000"
    assert chamadas[0]["cep_destino"] == "90010123"
    assert chamadas[0]["uf_destino"] == "RS"
    assert chamadas[0]["cnpj_destinatario"] == "12345678000190"
    assert chamadas[0]["cnpj_remetente"] == "98765432000110"
    assert chamadas[0]["cubagens"] == [
        {
            "quantidade": 1,
            "comprimento_cm": 45,
            "largura_cm": 31,
            "altura_cm": 31,
            "peso_por_volume_kg": None,
        }
    ]


def test_translovato_config_incompleta_retorna_resultado_controlado(monkeypatch):
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))

    class FakeFactory:
        def __init__(self, config):
            self.config = config

        def is_available(self, nome):
            return nome == "translovato"

        def get_provider_config(self, nome):
            if nome == "translovato":
                return {"habilitado": True, "cnpj": "", "usuario": "", "senha": "", "ufs_atendidas": ["RS"]}
            return {"habilitado": False}

        def validate_minimum_config(self, nome):
            from fretio.providers.factory import validate_provider_minimum_config
            return validate_provider_minimum_config(nome, self.get_provider_config(nome))

        def create(self, nome, **kwargs):
            raise AssertionError("provider incompleto não deveria ser criado")

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)

    config = {
        "fretio": {},
        "romaneio": {"cep_origem": "90000000"},
        "transportadoras": {"translovato": {"habilitado": True}},
    }
    dados = {
        "destino_cep": "90010123",
        "uf_destino": "RS",
        "cnpj_destinatario": "12345678000190",
        "peso": 3.3,
        "valor": 150.0,
        "volumes": 1,
        "cubagem_m3": 0.044,
        "cubagens": [{"quantidade": 1, "comprimento_cm": 45, "largura_cm": 31, "altura_cm": 31}],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(
        ct._executar_cotacoes_com_dados(config=config, dados=dados, cep_origem="90000000")
    )

    translovato_results = [r for r in resultados if r.transportadora == "TRANSLOVATO"]
    assert len(translovato_results) == 1
    assert translovato_results[0].status == "Configuração incompleta"
    assert "Configuração incompleta" in translovato_results[0].detalhes


def test_timeout_provider_nao_chama_report_error(monkeypatch):
    """TimeoutError de provider não deve acionar report_error — é falha controlada."""
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))
    report_error_calls = []
    monkeypatch.setattr(deps, "report_error", lambda *a, **kw: report_error_calls.append(a))

    class FakeTimeoutProvider:
        nome = "TRD"
        last_error = None
        _passo_atual = "aguardando_resultado"

        async def coteir(self, **kwargs):
            raise TimeoutError("Timeout de 45s na cotação TRD no passo: aguardando_resultado")

        async def cleanup(self):
            pass

    class FakeFactory:
        def __init__(self, config):
            pass

        def is_available(self, nome):
            return False

        def get_provider_config(self, nome):
            if nome == "trd":
                return {
                    "habilitado": True,
                    "email": "test@test.com",
                    "senha": "pw",
                    "ufs_atendidas": ["RS"],
                }
            return {"habilitado": False}

        def create(self, nome, **kwargs):
            if nome == "trd":
                return FakeTimeoutProvider()
            return None

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)

    config = {
        "fretio": {},
        "romaneio": {"cep_origem": "90000000"},
        "transportadoras": {"trd": {"habilitado": True}},
    }
    dados = {
        "destino_cep": "90010123",
        "uf_destino": "RS",
        "cnpj_destinatario": "12345678000190",
        "peso": 3.3,
        "valor": 150.0,
        "volumes": 1,
        "cubagem_m3": 0.044,
        "cubagens": [{"quantidade": 1, "comprimento_cm": 45, "largura_cm": 31, "altura_cm": 31}],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(
        ct._executar_cotacoes_com_dados(config=config, dados=dados, cep_origem="90000000")
    )

    # TimeoutError não deve chamar report_error
    assert len(report_error_calls) == 0
    trd_results = [r for r in resultados if "TRD" in r.transportadora]
    assert len(trd_results) >= 1
    assert trd_results[-1].status == "erro"


def test_chrome_ausente_gera_no_maximo_um_report(monkeypatch):
    """Chrome ausente deve gerar no máximo 1 report estruturado com event=chrome_missing."""
    reports = []
    monkeypatch.setattr(cotacao_session_manager, "report_provider_error", lambda provider, stage, message, **kw: reports.append((provider, stage, kw)))

    import fretio.providers.base as base_mod
    monkeypatch.setattr(
        base_mod,
        "find_chrome",
        lambda: (_ for _ in ()).throw(FileNotFoundError("Google Chrome nao encontrado. Instale o Chrome para usar o Fretio.")),
    )
    monkeypatch.setattr(cotacao_session_manager, "_kill_orphan_Fretio_chromes", lambda: None)
    monkeypatch.setattr(ct, "_carregar_config", lambda config_path=None: {})

    messages_callback = []
    session = ct.TransportadoraSession()
    asyncio.run(session.inicializar(callback=lambda msg: messages_callback.append(msg)))

    assert any(r[0] == "chrome" and r[1] == "pre_login" and r[2].get("context", {}).get("event") == "chrome_missing" for r in reports)
    assert len(reports) <= 1
    assert any("Chrome" in m or "chrome" in m.lower() for m in messages_callback)


def test_chrome_ausente_na_cotacao_reporta_uma_vez(monkeypatch):
    reports = []
    create_calls = []
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))
    monkeypatch.setattr(cotacao_orchestrator, "report_provider_error", lambda provider, stage, message, **kw: reports.append((provider, stage, kw)))

    class FakeFactory:
        def __init__(self, config):
            pass

        def is_available(self, nome):
            return False

        def get_provider_config(self, nome):
            if nome == "braspress":
                return {"habilitado": True, "cnpj": "123", "senha": "pw", "ufs_atendidas": ["RS"]}
            if nome == "trd":
                return {"habilitado": True, "email": "x@y.com", "senha": "pw", "ufs_atendidas": ["RS"]}
            return {"habilitado": False}

        def create(self, nome, **kwargs):
            create_calls.append(nome)
            raise FileNotFoundError("Google Chrome nao encontrado. Instale o Chrome para usar o Fretio.")

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)

    dados = {
        "destino_cep": "90010123",
        "uf_destino": "RS",
        "cnpj_destinatario": "12345678000190",
        "peso": 3.3,
        "valor": 150.0,
        "volumes": 1,
        "cubagem_m3": 0.044,
        "cubagens": [{"quantidade": 1, "comprimento_cm": 45, "largura_cm": 31, "altura_cm": 31}],
        "descricoes_itens": [],
    }

    asyncio.run(
        ct._executar_cotacoes_com_dados(
            config={"romaneio": {"cep_origem": "90000000"}, "fretio": {}, "transportadoras": {}},
            dados=dados,
            cep_origem="90000000",
        )
    )

    chrome_reports = [r for r in reports if r[0] == "chrome"]
    assert len(chrome_reports) == 1
    assert create_calls == ["braspress"]
    assert chrome_reports[0][1] == "abrir_pagina"
    assert chrome_reports[0][2].get("context", {}).get("event") == "chrome_missing"


def test_trd_retornou_none_sem_diag_path_nao_reporta_duas_vezes(monkeypatch):
    """Normalização da mensagem TRD evita flood por path de diagnóstico variável."""
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))
    mensagens_reportadas = []

    def fake_report(msg, context=""):
        mensagens_reportadas.append(msg)

    monkeypatch.setattr(deps, "report_error_message", fake_report)
    monkeypatch.setattr(deps, "report_error", lambda *a, **kw: None)

    class FakeTRDProvider:
        nome = "TRD"
        _passo_atual = "valor_resultado"

        @property
        def last_error(self):
            import time
            return f"Valor não encontrado no resultado TRD (diagnóstico salvo em: /tmp/trd_{time.time():.0f})"

        async def coteir(self, **kwargs):
            return None

        async def cleanup(self):
            pass

    class FakeFactory:
        def __init__(self, config):
            pass

        def is_available(self, nome):
            return False

        def get_provider_config(self, nome):
            if nome == "trd":
                return {
                    "habilitado": True,
                    "email": "x@x.com",
                    "senha": "pw",
                    "ufs_atendidas": ["RS"],
                }
            return {"habilitado": False}

        def create(self, nome, **kwargs):
            if nome == "trd":
                return FakeTRDProvider()
            return None

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)

    config = {
        "fretio": {},
        "romaneio": {"cep_origem": "90000000"},
        "transportadoras": {"trd": {"habilitado": True}},
    }
    dados = {
        "destino_cep": "90010123",
        "uf_destino": "RS",
        "cnpj_destinatario": "12345678000190",
        "peso": 3.3,
        "valor": 150.0,
        "volumes": 1,
        "cubagem_m3": 0.044,
        "cubagens": [{"quantidade": 1, "comprimento_cm": 45, "largura_cm": 31, "altura_cm": 31}],
        "descricoes_itens": [],
    }

    asyncio.run(
        ct._executar_cotacoes_com_dados(config=config, dados=dados, cep_origem="90000000")
    )

    # A mensagem reportada não deve conter o path variável
    for msg in mensagens_reportadas:
        assert "diagnóstico salvo em" not in msg


def test_erros_fake_nao_aparecem_em_fluxo_producao():
    """'This always fails' e 'Database connection failed' nunca devem ir para a API em produção."""
    from error_handler import ErrorHandler
    # Confirma que report_to_server está desabilitado no arquivo de testes
    # (test_error_handler.py configura isso ao ser importado)
    # Aqui apenas verificamos que o fluxo cotacao não gera esses strings
    import cotacao_transportadoras as ct2
    source = Path(__file__).parent.parent / "app" / "cotacao_transportadoras.py"
    content = source.read_text(encoding="utf-8")
    assert "This always fails" not in content
    assert "Database connection failed" not in content
    assert "Sempre falha" not in content
    assert "Conectar ao banco de dados" not in content


def test_rodonaves_transient_errors_viram_falha_controlada(monkeypatch):
    """Erros transitórios da Rodonaves (timeout, page closed, frame detached, ERR_ABORTED)
    capturados em last_error NÃO devem ser reportados à API e devem gerar retry."""
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))
    reportados = []
    monkeypatch.setattr(deps, "report_error_message", lambda *a, **kw: reportados.append(a))
    monkeypatch.setattr(deps, "report_error", lambda *a, **kw: reportados.append(a))

    mensagens_transient = [
        "Page.goto Timeout 15000ms para https://cliente.rte.com.br/?showLogin=true",
        "net::ERR_ABORTED; maybe frame was detached",
        "Target page, context or browser has been closed",
        "Formulário de cotação não carregou",
        "valor de frete nao encontrado no resultado",
    ]

    for msg_erro in mensagens_transient:
        _captured_error = msg_erro

        class FakeRodonavesProvider:
            nome = "RODONAVES"
            _passo_atual = "login"

            @property
            def last_error(self):
                return _captured_error

            async def coteir(self, **kwargs):
                return None

            async def cleanup(self):
                pass

        class FakeFactory:
            def __init__(self, config):
                pass

            def is_available(self, nome):
                return nome == "rodonaves"

            def get_provider_config(self, nome):
                if nome == "rodonaves":
                    return {
                        "habilitado": True,
                        "dominio": "RTE",
                        "usuario": "12345678000190",
                        "senha": "pw",
                        "cnpj_pagador": "12345678000190",
                        "ufs_atendidas": ["SP"],
                    }
                return {"habilitado": False}

            def create(self, nome, **kwargs):
                if nome == "rodonaves":
                    return FakeRodonavesProvider()
                return None

        monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)
        reportados.clear()

        config = {
            "fretio": {},
            "romaneio": {"cep_origem": "01310100"},
            "transportadoras": {"rodonaves": {"habilitado": True}},
        }
        dados = {
            "destino_cep": "01310200",
            "uf_destino": "SP",
            "cnpj_destinatario": "12345678000190",
            "peso": 5.0,
            "valor": 300.0,
            "volumes": 1,
            "cubagem_m3": 0.03,
            "cubagens": [{"quantidade": 1, "comprimento_cm": 30, "largura_cm": 20, "altura_cm": 20}],
            "descricoes_itens": [],
        }

        resultados = asyncio.run(
            ct._executar_cotacoes_com_dados(config=config, dados=dados, cep_origem="01310100")
        )

        assert len(reportados) == 0, (
            f"Erro transitório '{msg_erro[:60]}...' não deveria gerar report, mas gerou: {reportados}"
        )
        rod_results = [r for r in resultados if r.transportadora == "RODONAVES"]
        assert len(rod_results) >= 1
        assert rod_results[-1].status == "erro"


def test_copex_timeout_aguardando_resultado_falha_controlada(monkeypatch):
    """Timeout no passo aguardando_resultado da Copex vira falha controlada sem report_error."""
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))
    report_calls = []
    monkeypatch.setattr(deps, "report_error", lambda *a, **kw: report_calls.append(a))
    monkeypatch.setattr(deps, "report_error_message", lambda *a, **kw: report_calls.append(a))

    class FakeCopexTimeout:
        nome = "COOPEX"
        _passo_atual = "aguardando_resultado"
        last_error = "Timeout aguardando resultado da Copex"

        async def coteir(self, **kwargs):
            return None

        async def cleanup(self):
            pass

    class FakeFactory:
        def __init__(self, config):
            pass

        def is_available(self, nome):
            return nome == "coopex"

        def get_provider_config(self, nome):
            if nome == "coopex":
                return {
                    "habilitado": True,
                        "dominio": "DOM",
                        "usuario": "usr",
                        "senha": "pw",
                        "cnpj_pagador": "00000000000191",
                        "ufs_atendidas": ["SP"],
                    }
            return {"habilitado": False}

        def create(self, nome, **kwargs):
            if nome == "coopex":
                return FakeCopexTimeout()
            return None

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)

    config = {
        "fretio": {},
        "romaneio": {"cep_origem": "01310100"},
        "transportadoras": {"coopex": {"habilitado": True}},
    }
    dados = {
        "destino_cep": "01310200",
        "uf_destino": "SP",
        "cnpj_destinatario": "12345678000190",
        "peso": 5.0,
        "valor": 300.0,
        "volumes": 1,
        "cubagem_m3": 0.03,
        "cubagens": [{"quantidade": 1, "comprimento_cm": 30, "largura_cm": 20, "altura_cm": 20}],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(
        ct._executar_cotacoes_com_dados(config=config, dados=dados, cep_origem="01310100")
    )

    assert len(report_calls) == 0, f"Timeout Copex não deveria gerar report, mas gerou: {report_calls}"
    copex_results = [r for r in resultados if r.transportadora == "COOPEX"]
    assert len(copex_results) >= 1
    assert copex_results[-1].status == "erro"


def test_orquestrador_faz_fallback_para_coteir_quando_cotar_request_falha(monkeypatch):
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))

    class FakeFallbackProvider:
        nome = "TRD"
        _passo_atual = "inicio"
        last_error = None

        async def cotar(self, request):
            raise TypeError("assinatura legada")

        async def coteir(self, **kwargs):
            from fretio.models import Cotacao
            return Cotacao(transportadora="TRD", prazo_dias=4, valor_frete=333.0)

        async def cleanup(self):
            pass

    class FakeFactory:
        def __init__(self, config):
            pass

        def is_available(self, nome):
            return False

        def get_provider_config(self, nome):
            if nome == "trd":
                return {
                    "habilitado": True,
                    "email": "x@x.com",
                    "senha": "pw",
                    "ufs_atendidas": ["RS"],
                }
            return {"habilitado": False}

        def create(self, nome, **kwargs):
            if nome == "trd":
                return FakeFallbackProvider()
            return None

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)

    config = {
        "fretio": {},
        "romaneio": {"cep_origem": "90000000"},
        "transportadoras": {"trd": {"habilitado": True}},
    }
    dados = {
        "destino_cep": "90010123",
        "uf_destino": "RS",
        "cnpj_destinatario": "12345678000190",
        "peso": 3.3,
        "valor": 150.0,
        "volumes": 1,
        "cubagem_m3": 0.044,
        "cubagens": [{"quantidade": 1, "comprimento_cm": 45, "largura_cm": 31, "altura_cm": 31}],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(
        ct._executar_cotacoes_com_dados(config=config, dados=dados, cep_origem="90000000")
    )

    trd_results = [r for r in resultados if r.transportadora == "TRD"]
    assert len(trd_results) >= 1
    assert trd_results[0].status == "ok"
    assert trd_results[0].valor_frete == 333.0


def test_eucatur_falha_com_mensagem_clara_sem_documento_pagador(monkeypatch):
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))

    class FakeFactory:
        def __init__(self, config):
            self.config = config

        def is_available(self, nome):
            return nome == "eucatur"

        def get_provider_config(self, nome):
            if nome == "eucatur":
                return {
                    "habilitado": True,
                    "dominio": "DOM",
                    "usuario": "usr",
                    "senha": "pw",
                    "cnpj_pagador": "",
                    "ufs_atendidas": ["AM"],
                }
            return {"habilitado": False}

        def validate_minimum_config(self, nome):
            from fretio.providers.factory import validate_provider_minimum_config
            return validate_provider_minimum_config(nome, self.get_provider_config(nome))

        def create(self, nome, **kwargs):
            raise AssertionError("não deve criar provider sem documento pagador")

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)
    config = {"fretio": {}, "romaneio": {"cep_origem": "69000000", "cnpj_pagador_padrao": ""}}
    dados = {
        "destino_cep": "69000100",
        "uf_destino": "AM",
        "cnpj_destinatario": "12345678000190",
        "peso": 10.0,
        "valor": 500.0,
        "volumes": 1,
        "cubagem_m3": 0.024,
        "cubagens": [{"quantidade": 1, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 20}],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(ct._executar_cotacoes_com_dados(config=config, dados=dados, cep_origem="69000000"))

    eucatur_results = [r for r in resultados if r.transportadora == "EUCATUR"]
    assert len(eucatur_results) == 1
    assert eucatur_results[0].status == "Configuração incompleta"
    assert "documento pagador obrigatório" in eucatur_results[0].detalhes
    assert "Configurações > Empresa" in eucatur_results[0].detalhes


def test_coopex_usa_documento_pagador_padrao_da_empresa(monkeypatch):
    monkeypatch.setattr(deps, "carrier_enabled_or_message", lambda carrier: (True, ""))
    chamadas = []

    class FakeCoopexProvider:
        nome = "COOPEX"
        last_error = None
        _passo_atual = "inicio"

        async def coteir(self, **kwargs):
            chamadas.append(kwargs)
            from fretio.models import Cotacao
            return Cotacao(transportadora="COOPEX", prazo_dias=2, valor_frete=111.0)

    class FakeFactory:
        def __init__(self, config):
            self.config = config

        def is_available(self, nome):
            return nome == "coopex"

        def get_provider_config(self, nome):
            if nome == "coopex":
                return {
                    "habilitado": True,
                    "dominio": "DOM",
                    "usuario": "usr",
                    "senha": "pw",
                    "cnpj_pagador": "",
                    "ufs_atendidas": ["SP"],
                }
            return {"habilitado": False}

        def validate_minimum_config(self, nome):
            from fretio.providers.factory import validate_provider_minimum_config
            return validate_provider_minimum_config(nome, self.get_provider_config(nome))

        def create(self, nome, **kwargs):
            assert kwargs["cnpj_pagador"] == "12345678000190"
            return FakeCoopexProvider()

    monkeypatch.setattr(deps, "ProviderFactory", FakeFactory)
    config = {"fretio": {}, "romaneio": {"cep_origem": "01310100", "cnpj_pagador_padrao": "12.345.678/0001-90"}}
    dados = {
        "destino_cep": "01310200",
        "uf_destino": "SP",
        "cnpj_destinatario": "98765432000110",
        "peso": 10.0,
        "valor": 500.0,
        "volumes": 1,
        "cubagem_m3": 0.024,
        "cubagens": [{"quantidade": 1, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 20}],
        "descricoes_itens": [],
    }

    resultados = asyncio.run(ct._executar_cotacoes_com_dados(config=config, dados=dados, cep_origem="01310100"))

    coopex_results = [r for r in resultados if r.transportadora == "COOPEX"]
    assert len(coopex_results) == 1
    assert coopex_results[0].status == "ok"
    assert chamadas[0]["cnpj_pagador"] == "12345678000190"
    assert chamadas[0]["cnpj_remetente"] == "12345678000190"
