import asyncio
import inspect
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from fretio.providers.agex import AGEXProvider
from fretio.providers.alfa import AlfaProvider
from fretio.providers.braspress_playwright import BraspressPlaywrightProvider
from fretio.providers.coopex import CoopexProvider
from fretio.providers.eucatur import EucaturProvider
from fretio.providers.rodonaves import RodonavesProvider
from fretio.providers.translovato import TranslovatoProvider
from fretio.providers.trd import TRDProvider
from fretio.providers import provider_utils as pu
from fretio.models import Cotacao
from fretio.quotation_contract import QuoteRequest, QuoteResponse


def test_parse_helpers_handle_multiple_input_formats():
    assert pu._digits("12.345-67") == "1234567"
    assert pu._parse_decimal_any("1.234,56") == 1234.56
    assert pu._parse_decimal_any("1,234.56") == 1234.56
    assert pu._parse_decimal_any(987.5) == 987.5
    assert pu._parse_decimal_any("invalido") is None
    assert pu._parse_int_any("Volumes: 42 caixas") == 42
    assert pu._parse_int_any("sem numero") == 0


def test_decimal_and_currency_formatters_apply_expected_rounding():
    assert pu._fmt_decimal(12.3456) == "12,35"
    assert pu._fmt_decimal(12.3456, comma=False) == "12.35"
    assert pu._fmt_peso(1.23456) == "1,235"
    assert pu._format_decimal_br_2(1.005) == "1,01"
    assert pu._format_decimal_br_2(0.1, min_value=0.25) == "0,25"
    assert pu._format_currency(1234.565) == "R$ 1.234,57"


def test_document_formatters_only_apply_for_complete_values():
    assert pu._format_cnpj("12345678000190") == "12.345.678/0001-90"
    assert pu._format_cnpj("123") == "123"
    assert pu._format_cpf("12345678901") == "123.456.789-01"
    assert pu._format_cpf("123") == "123"


def test_translovato_helpers_parse_dimensions_value_and_deadline():
    assert TranslovatoProvider._cm_to_m_br(10) == "0,1"
    assert TranslovatoProvider._cm_to_m_br(31) == "0,31"
    assert TranslovatoProvider._extract_prazo("Prazo de entrega 6 dias") == 6
    assert TranslovatoProvider._extract_valor("Total do frete R$ 1.234,56") == 1234.56


def test_translovato_provider_keeps_observed_selectors():
    selectors = set(TranslovatoProvider.MAIN_SELECTORS)

    assert "#cnpj" in selectors
    assert "#user" in selectors
    assert 'input[name="password"]' in selectors
    assert "#sender_cpnj" in selectors
    assert "#receiver_c" in selectors
    assert "#cep_entrega" in selectors
    assert 'input[name="value[volume_nf]"]' in selectors
    assert 'input[name="value[volume_weigth]"]' in selectors
    assert 'input[name="cubing_qnt[]"]' in selectors
    assert 'input[name="cubing_height[]"]' in selectors
    assert 'input[name="cubing_length[]"]' in selectors
    assert 'input[name="cubing_depth[]"]' in selectors


def test_get_stealth_script_returns_expected_browser_patches():
    script = pu.get_stealth_script()

    assert "navigator.webdriver" in script
    assert "chrome.runtime" in script
    assert "hardwareConcurrency" in script


def test_get_stealth_script_can_skip_eval_patch():
    script = pu.get_stealth_script(preserve_eval=False)

    assert "navigator.webdriver" in script
    assert "window.eval = function()" not in script


def test_provider_classes_keep_backward_compatible_helper_aliases():
    assert AGEXProvider._digits("12.345-67") == "1234567"
    assert AGEXProvider._parse_brl("R$ 1.234,56") == 1234.56
    assert AlfaProvider._fmt_decimal(12.3456) == "12,35"
    assert BraspressPlaywrightProvider._parse_int_any("Prazo 7 dias") == 7
    assert RodonavesProvider._digits("85.955-191") == "85955191"
    assert TRDProvider._digits("00.000.000/0001-91") == "00000000000191"


def test_trd_login_context_falls_back_to_page_when_frame_is_none():
    provider = TRDProvider(email="a@b.com", senha="123")
    provider._page = object()
    provider._login_frame = None

    login_context = provider._login_frame or provider._page

    assert login_context is provider._page


def test_alfa_prefers_real_site_page_over_chrome_internal_pages():
    provider = AlfaProvider(login="user", senha="123")
    pages = [
        {"type": "page", "url": "chrome://omnibox-popup.top-chrome/", "webSocketDebuggerUrl": "ws://chrome"},
        {"type": "page", "url": "https://arearestrita.alfatransportes.com.br/cotacao/api/", "webSocketDebuggerUrl": "ws://alfa"},
    ]

    best_page = provider._select_best_debug_target(pages)

    assert best_page is not None
    assert best_page["webSocketDebuggerUrl"] == "ws://alfa"


def test_rodonaves_allows_grace_when_launcher_exits_cleanly():
    assert RodonavesProvider._launcher_exit_can_still_spawn_browser(0) is True
    assert RodonavesProvider._launcher_exit_can_still_spawn_browser(1) is False
    assert RodonavesProvider._launcher_exit_can_still_spawn_browser(None) is False


def test_trd_document_helpers_cover_formatted_cnpj_and_ng_model_selectors():
    values = TRDProvider._document_candidate_values("00000000000191")
    selectors = TRDProvider._etapa1_document_selectors("destinatario")

    assert "00.000.000/0001-91" in values
    assert "input[ng-model*='destinatario' i][ng-model*='cnpj' i]" in selectors
    assert "input[name*='document' i]" in selectors


def test_rodonaves_detects_live_browser_session_even_if_launcher_exited():
    class _Browser:
        def is_connected(self):
            return True

    provider = RodonavesProvider(dominio="RTE", usuario="u", senha="s", cnpj_pagador="00000000000191")
    provider._context = object()
    provider._browser = _Browser()

    assert provider._has_live_browser_session() is True


def test_rodonaves_captcha_window_bounds_center_screen():
    provider = RodonavesProvider(dominio="RTE", usuario="u", senha="s", cnpj_pagador="00000000000191")

    assert provider._captcha_window_bounds(1920, 1080) == (550, 180, 820, 720)


def test_rodonaves_prefers_portal_page_over_new_tab():
    provider = RodonavesProvider(dominio="RTE", usuario="u", senha="s", cnpj_pagador="00000000000191")

    assert provider._score_page_url("https://cliente.rte.com.br/Quotation") < provider._score_page_url("chrome://newtab/")


def test_rodonaves_navigation_retry_classifies_connection_resets_as_transient():
    provider = RodonavesProvider(dominio="RTE", usuario="u", senha="s", cnpj_pagador="00000000000191")

    assert provider._is_retryable_navigation_error("Page.goto: net::ERR_CONNECTION_RESET at https://cliente.rte.com.br/") is True
    assert provider._is_retryable_navigation_error("Page.goto: net::ERR_ABORTED at https://cliente.rte.com.br/") is True
    assert provider._is_retryable_navigation_error("RuntimeError: campo obrigatório ausente") is False


def test_rodonaves_window_fallback_uses_pid(monkeypatch):
    calls = []

    async def fake_by_page(page, **kwargs):
        calls.append(("page", kwargs))
        return False

    def fake_by_pid(pid, **kwargs):
        calls.append(("pid", pid, kwargs))
        return True

    provider = RodonavesProvider(dominio="RTE", usuario="u", senha="s", cnpj_pagador="00000000000191")
    provider._page = object()

    class _Proc:
        pid = 321

    provider._chrome_proc = _Proc()

    monkeypatch.setattr("fretio.providers.rodonaves.posicionar_janela_por_pagina", fake_by_page)
    monkeypatch.setattr("fretio.providers.rodonaves.posicionar_janela_por_pid", fake_by_pid)

    ok = asyncio.run(
        provider._reposicionar_janela_win32(
            left=10,
            top=20,
            width=30,
            height=40,
            bring_to_front=True,
        )
    )

    assert ok is True
    assert calls[0][0] == "page"
    assert calls[1][0] == "pid"
    assert calls[1][1] == 321


def test_coopex_coteir_updates_current_step_before_each_phase():
    provider = CoopexProvider(dominio="dom", usuario="user", senha="secret")
    observed_steps = []

    class _FakePage:
        async def close(self):
            return None

    class _FakeContext:
        async def new_page(self):
            return _FakePage()

    async def fake_init_browser():
        observed_steps.append(provider._passo_atual)
        provider._context = _FakeContext()
        provider._page = _FakePage()

    async def fake_login():
        observed_steps.append(provider._passo_atual)
        provider._logged_in = True

    async def fake_navegar_cotacao():
        observed_steps.append(provider._passo_atual)

    async def fake_preencher_cotacao(*args, **kwargs):
        observed_steps.append(provider._passo_atual)

    async def fake_submeter_e_extrair():
        observed_steps.append(provider._passo_atual)
        return None

    provider._init_browser = fake_init_browser
    provider._login = fake_login
    provider._navegar_cotacao = fake_navegar_cotacao
    provider._preencher_cotacao = fake_preencher_cotacao
    provider._submeter_e_extrair = fake_submeter_e_extrair

    resultado = asyncio.run(
        provider.coteir(
            origem="90010-123",
            destino="89010-020",
            peso=12.5,
            valor=350.0,
            volumes=1,
            cubagem_m3=0.024,
            cubagens=[
                {
                    "quantidade": 1,
                    "comprimento_cm": 40,
                    "largura_cm": 30,
                    "altura_cm": 20,
                }
            ],
        )
    )

    assert resultado is None
    assert observed_steps == [
        "init_browser",
        "login",
        "navegando_cotacao",
        "preenchendo_formulario",
        "submetendo_cotacao",
    ]


def test_rodonaves_candidate_window_pids_include_profile_processes(monkeypatch):
    provider = RodonavesProvider(dominio="RTE", usuario="u", senha="s", cnpj_pagador="00000000000191")
    provider._active_user_data_dir = r"C:\tmp\profile"

    class _Proc:
        pid = 123

    provider._chrome_proc = _Proc()

    monkeypatch.setattr(
        RodonavesProvider,
        "_listar_pids_chrome_por_user_data_dir",
        staticmethod(lambda path: [456, 123, 789] if path == r"C:\tmp\profile" else []),
    )

    assert provider._candidate_window_pids() == [123, 456, 789]


def test_agex_extracts_previsao_and_quote_number_from_text():
    texto = "Cotação 2197136\nFrete total R$ 541,58\nEntrega prevista 13/05/2026"

    assert AGEXProvider._extrair_previsao_do_texto(texto) == "13/05/2026"
    assert AGEXProvider._extrair_numero_cotacao_do_texto(texto) == "2197136"


def test_agex_detects_pending_confirmation_screen():
    assert AGEXProvider._tem_confirmacao_resultado_pendente("Confirmar e ver resultado") is True
    assert AGEXProvider._tem_confirmacao_resultado_pendente("Resumo da cotação") is False


def _quote_request_sample() -> QuoteRequest:
    return QuoteRequest(
        origem_cep="99740000",
        destino_cep="01310100",
        uf_destino="SP",
        cnpj_destinatario="12345678000190",
        peso_total_kg=12.5,
        valor_nf=750.0,
        volumes=1,
        cubagem_m3=0.024,
        cubagens=[
            {
                "quantidade": 1,
                "comprimento_cm": 40,
                "largura_cm": 30,
                "altura_cm": 20,
            }
        ],
    )


def _target_provider_factories():
    return [
        ("braspress", lambda: BraspressPlaywrightProvider(cnpj="00000000000191", senha="secret", headless=True)),
        ("trd", lambda: TRDProvider(email="test@example.com", senha="secret", headless=True)),
        ("agex", lambda: AGEXProvider(email="test@example.com", senha="secret", cnpj="00000000000191", headless=True)),
        ("eucatur", lambda: EucaturProvider(dominio="DOM", usuario="usr", senha="secret", cnpj_pagador="00000000000191", headless=True)),
        (
            "rodonaves",
            lambda: RodonavesProvider(
                dominio="RTE",
                usuario="usr",
                senha="secret",
                cnpj_pagador="00000000000191",
                headless=True,
            ),
        ),
        ("alfa", lambda: AlfaProvider(login="usr", senha="secret", headless=True)),
        ("coopex", lambda: CoopexProvider(dominio="DOM", usuario="usr", senha="secret", cnpj_pagador="00000000000191", headless=True)),
    ]


@pytest.mark.parametrize("provider_name, provider_factory", _target_provider_factories())
def test_target_providers_implement_quote_request_contract(provider_name, provider_factory):
    provider = provider_factory()
    assert "cotar" in type(provider).__dict__
    assert callable(provider.coteir)
    signature = inspect.signature(provider.cotar)
    params = list(signature.parameters.values())
    assert params and params[0].name == "request"


@pytest.mark.parametrize("provider_name, provider_factory", _target_provider_factories())
def test_target_providers_cotar_retornam_quote_response_ok(provider_name, provider_factory, monkeypatch):
    provider = provider_factory()
    request = _quote_request_sample()
    provider._passo_atual = "teste"

    async def _fake_coteir(**kwargs):
        return Cotacao(
            transportadora=provider.nome,
            prazo_dias=3,
            valor_frete=123.45,
            restricoes="Cotação fake",
        )

    monkeypatch.setattr(provider, "coteir", _fake_coteir)
    response = asyncio.run(provider.cotar(request))

    assert isinstance(response, QuoteResponse)
    assert response.status == "ok"
    assert response.provider == provider.nome
    assert response.valor_frete == 123.45
    assert response.prazo_dias == 3
    assert response.duration_ms is not None
    assert response.stage == "teste"


@pytest.mark.parametrize("provider_name, provider_factory", _target_provider_factories())
def test_target_providers_cotar_nao_atendido_quando_rota_nao_suportada(provider_name, provider_factory, monkeypatch):
    provider = provider_factory()
    request = _quote_request_sample()
    provider._passo_atual = "validacao"

    async def _fake_coteir(**kwargs):
        provider.last_error = "Rota não atendida pelo SSW"
        return None

    monkeypatch.setattr(provider, "coteir", _fake_coteir)
    response = asyncio.run(provider.cotar(request))

    assert isinstance(response, QuoteResponse)
    assert response.status == "nao_atendido"
    assert response.error_code == "nao_atendido"
    assert response.stage == "validacao"
    assert response.duration_ms is not None


@pytest.mark.parametrize("provider_name, provider_factory", _target_provider_factories())
def test_target_providers_cotar_timeout_vira_erro(provider_name, provider_factory, monkeypatch):
    provider = provider_factory()
    request = _quote_request_sample()
    provider._passo_atual = "aguardando_resultado"

    async def _fake_coteir(**kwargs):
        raise TimeoutError("Timeout aguardando resultado do portal")

    monkeypatch.setattr(provider, "coteir", _fake_coteir)
    response = asyncio.run(provider.cotar(request))

    assert isinstance(response, QuoteResponse)
    assert response.status == "erro"
    assert response.error_code == "timeout"
    assert response.stage == "aguardando_resultado"
    assert response.duration_ms is not None


def test_eucatur_aceita_mais_de_11_volumes_quando_tem_cubagem_total(monkeypatch):
    provider = EucaturProvider(
        dominio="DOM",
        usuario="usr",
        senha="secret",
        cnpj_pagador="00000000000191",
        headless=True,
    )
    captured = {}

    async def _noop():
        return None

    async def _fake_preencher(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    async def _fake_submeter():
        return Cotacao(transportadora=provider.nome, prazo_dias=4, valor_frete=456.78)

    monkeypatch.setattr(provider, "_init_browser", _noop)
    monkeypatch.setattr(provider, "_login", _noop)
    monkeypatch.setattr(provider, "_navegar_cotacao", _noop)
    monkeypatch.setattr(provider, "_preencher_cotacao", _fake_preencher)
    monkeypatch.setattr(provider, "_submeter_e_extrair", _fake_submeter)

    result = asyncio.run(
        provider.coteir(
            origem="69000000",
            destino="69000100",
            peso=10.0,
            valor=500.0,
            volumes=12,
            cubagem_m3=0.288,
            cubagens=[{"quantidade": 12, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 20}],
        )
    )

    assert result is not None
    assert result.valor_frete == 456.78
    assert captured["args"][4] == 12
    assert captured["args"][5] == 0.288


def test_eucatur_sem_cubagem_retorna_dado_incompleto_antes_do_browser(monkeypatch):
    provider = EucaturProvider(
        dominio="DOM",
        usuario="usr",
        senha="secret",
        cnpj_pagador="00000000000191",
        headless=True,
    )

    async def _fail_init():
        raise AssertionError("browser não deveria abrir sem cubagem")

    monkeypatch.setattr(provider, "_init_browser", _fail_init)

    result = asyncio.run(
        provider.coteir(
            origem="69000000",
            destino="69000100",
            peso=10.0,
            valor=500.0,
            volumes=12,
            cubagem_m3=0,
            cubagens=[{"quantidade": 12, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 20}],
        )
    )

    assert result is None
    assert "Cubagem do romaneio não encontrada" in (provider.last_error or "")


def test_coopex_preserva_cubagem_total_do_romaneio(monkeypatch):
    provider = CoopexProvider(
        dominio="DOM",
        usuario="usr",
        senha="secret",
        cnpj_pagador="00000000000191",
        headless=True,
    )
    captured = {}

    async def _noop():
        return None

    async def _fake_preencher(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    async def _fake_submeter():
        return Cotacao(transportadora=provider.nome, prazo_dias=5, valor_frete=654.32)

    monkeypatch.setattr(provider, "_init_browser", _noop)
    monkeypatch.setattr(provider, "_login", _noop)
    monkeypatch.setattr(provider, "_navegar_cotacao", _noop)
    monkeypatch.setattr(provider, "_preencher_cotacao", _fake_preencher)
    monkeypatch.setattr(provider, "_submeter_e_extrair", _fake_submeter)

    result = asyncio.run(
        provider.coteir(
            origem="01310100",
            destino="01310200",
            peso=10.0,
            valor=500.0,
            volumes=12,
            cubagem_m3=0.777,
            cubagens=[{"quantidade": 12, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 20}],
        )
    )

    assert result is not None
    assert result.valor_frete == 654.32
    assert captured["args"][4] == 12
    assert captured["args"][5] == 0.777


def test_coopex_sem_cubagem_retorna_dado_incompleto_antes_do_browser(monkeypatch):
    provider = CoopexProvider(
        dominio="DOM",
        usuario="usr",
        senha="secret",
        cnpj_pagador="00000000000191",
        headless=True,
    )

    async def _fail_init():
        raise AssertionError("browser não deveria abrir sem cubagem")

    monkeypatch.setattr(provider, "_init_browser", _fail_init)

    result = asyncio.run(
        provider.coteir(
            origem="01310100",
            destino="01310200",
            peso=10.0,
            valor=500.0,
            volumes=12,
            cubagem_m3=0,
            cubagens=[{"quantidade": 12, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 20}],
        )
    )

    assert result is None
    assert "Cubagem do romaneio não encontrada" in (provider.last_error or "")


def test_translovato_city_uf_normalization_handles_accents_and_masks_doc():
    assert TranslovatoProvider._parse_cidade_uf("Caxias do Sul/RS") == ("CAXIAS DO SUL", "RS")
    assert TranslovatoProvider._parse_cidade_uf("São José - SC") == ("SAO JOSE", "SC")
    masked = TranslovatoProvider._mask_doc("12.345.678/0001-90")
    assert masked == "1234***90"
    assert "3456780001" not in masked


def test_translovato_blocks_when_autofill_city_diverges():
    provider = TranslovatoProvider(cnpj="12345678000190", usuario="u", senha="s")

    class FakePage:
        async def wait_for_timeout(self, _timeout):
            return None

    provider._page = FakePage()

    async def run():
        provider._read_delivery_zip_digits = lambda: _async_value("95000000")
        provider._read_delivery_city_uf = lambda: _async_value(("Farroupilha/RS", "FARROUPILHA", "RS"))
        provider._read_receiver_cnpj_digits = lambda: _async_value("12345678000190")
        await provider._aguardar_e_validar_autopreenchimento_destino(
            expected_receiver="12345678000190",
            expected_cep="95000000",
            expected_city="Caxias do Sul",
            expected_uf="RS",
        )

    with pytest.raises(ValueError, match="Cidade de entrega"):
        asyncio.run(run())


def test_translovato_accepts_matching_autofill_without_manual_zip_fill():
    provider = TranslovatoProvider(cnpj="12345678000190", usuario="u", senha="s")
    calls = {"zip_reads": 0, "city_reads": 0}

    class FakePage:
        async def wait_for_timeout(self, _timeout):
            return None

    provider._page = FakePage()

    async def read_zip():
        calls["zip_reads"] += 1
        return "95000000"

    async def read_city():
        calls["city_reads"] += 1
        return "Caxias do Sul/RS", "CAXIAS DO SUL", "RS"

    async def run():
        provider._read_delivery_zip_digits = read_zip
        provider._read_delivery_city_uf = read_city
        provider._read_receiver_cnpj_digits = lambda: _async_value("12345678000190")
        await provider._aguardar_e_validar_autopreenchimento_destino(
            expected_receiver="12345678000190",
            expected_cep="95000000",
            expected_city="Caxias do Sul",
            expected_uf="RS",
        )

    asyncio.run(run())
    assert calls == {"zip_reads": 1, "city_reads": 1}


async def _async_value(value):
    return value
