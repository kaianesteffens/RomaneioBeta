"""Characterization tests for app/web_app.py — the pywebview bridge (`Api`).

These pin the CURRENT behavior of the pure-ish serializers/formatters so a later
refactor (Phase 7: splitting the Api god-object) is caught if it changes output.

NOTHING here touches Playwright/network/Chrome or a live pywebview window. The
`webview` module is stubbed at import time; `Api.__init__` is lightweight and lazy
(`_loop`/`_sessao` stay None until a cotação/rastreio actually runs).

Quirks intentionally pinned (NOT fixed — see module return notes):
  * `_resolver_tema_efetivo('sistema')` collapses to 'escuro' (POC default).
  * `_num` collapses whole floats to int (3.0 -> 3) and maps invalid -> None.
  * The FOB romaneio's "TOTAL: R$ {valor:.2f}" emits dot-decimal with no
    thousands separator (1.234,56 BRL -> "R$ 1234.56"), NOT Brazilian R$ format.
  * The FOB romaneio's CNPJ/CEP come from the *empresa* config, never the form.
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

# pywebview is not installed in the test env (and we never start a window).
# Stub the bare surface web_app imports at module load time.
if "webview" not in sys.modules:
    sys.modules["webview"] = types.SimpleNamespace(
        OPEN_DIALOG=10,
        create_window=lambda *a, **k: None,
        start=lambda *a, **k: None,
    )

import web_app  # noqa: E402


# ── Fakes (minimal stand-ins for the real backend objects) ──────────────────
class FakeNF:
    """Minimal NotaFiscal-like stub exposing only the attributes _nota_card reads."""

    _DEFAULTS = dict(
        chave_acesso="", numero="", serie="", data_emissao="",
        emitente_nome="", emitente_cnpj="",
        destinatario_nome="", destinatario_cnpj="", destinatario_uf="",
        destinatario_cidade="", destinatario_cep="",
        transportadora_nome="", transportadora_cnpj="",
        produtos_resumo="", info_complementar="",
    )

    def __init__(self, **kw):
        d = dict(self._DEFAULTS)
        d.update(kw)
        self.__dict__.update(d)


class FakeResultado:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeRastreio:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _write_cfg(text: str) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "CONFIG.toml"
    p.write_text(text, encoding="utf-8")
    return p


def _api(config_path: Path | None = None, empresa: str = "ACME") -> "web_app.Api":
    api = web_app.Api(empresa=empresa, config_path=config_path)
    # Sanity: construction must stay lazy (no backend/loop/session created).
    assert api._loop is None
    assert api._sessao is None
    assert api._cotando is False
    return api


# ── _resolver_tema_efetivo ──────────────────────────────────────────────────
def test_char_resolver_tema_efetivo_mapping():
    # 'claro' and 'escuro' pass through; everything else (incl. 'sistema',
    # None, unknown) resolves to 'escuro' — the POC's default identity.
    assert web_app._resolver_tema_efetivo("claro") == "claro"
    assert web_app._resolver_tema_efetivo("escuro") == "escuro"
    assert web_app._resolver_tema_efetivo("sistema") == "escuro"
    assert web_app._resolver_tema_efetivo(None) == "escuro"
    assert web_app._resolver_tema_efetivo("xyz") == "escuro"
    # Input is lowercased before matching, so casing does not matter.
    assert web_app._resolver_tema_efetivo("CLARO") == "claro"
    assert web_app._resolver_tema_efetivo("Escuro") == "escuro"


# ── _num (numeric coercion used across serializers) ─────────────────────────
def test_char_num_coercion_collapses_whole_floats_and_drops_invalid():
    api = _api()
    assert api._num(None) is None
    assert api._num("abc") is None
    assert api._num([]) is None  # TypeError path -> None
    assert api._num(3) == 3
    assert api._num(3.0) == 3 and isinstance(api._num(3.0), int)
    assert api._num("5.0") == 5 and isinstance(api._num("5.0"), int)
    assert api._num(5.5) == 5.5
    assert api._num(1500.0) == 1500 and isinstance(api._num(1500.0), int)


# ── _ser_resultado ──────────────────────────────────────────────────────────
def test_char_ser_resultado_shape_and_none():
    api = _api()
    assert api._ser_resultado(None) is None

    r = FakeResultado(
        status="ok", valor_frete=123.45, prazo_dias=3.0,
        transportadora="braspress", detalhes="via portal", duration_ms=1500.0,
    )
    assert api._ser_resultado(r) == {
        "status": "ok",
        "valor_frete": 123.45,
        "prazo_dias": 3,        # 3.0 collapsed to int by _num
        "transportadora": "braspress",
        "detalhes": "via portal",
        "duration_ms": 1500,    # 1500.0 collapsed to int by _num
    }


def test_char_ser_resultado_missing_attrs_default_to_none():
    api = _api()
    # An object missing every attribute -> all keys present, values None.
    r = FakeResultado()
    assert api._ser_resultado(r) == {
        "status": None,
        "valor_frete": None,
        "prazo_dias": None,
        "transportadora": None,
        "detalhes": None,
        "duration_ms": None,
    }


# ── _ser_rastreio ───────────────────────────────────────────────────────────
def test_char_ser_rastreio_shape_and_bool_coercion():
    api = _api()
    assert api._ser_rastreio(None) is None

    r = FakeRastreio(
        numero_nfe="999", transportadora="trd", entregue=1,
        previsao_entrega="2024-04-01", link_rastreio="http://x",
        screenshot_path="/tmp/a.png", status_texto="Entregue", erro="",
    )
    out = api._ser_rastreio(r)
    assert out == {
        "numero_nfe": "999",
        "transportadora": "trd",
        "entregue": True,       # truthy 1 coerced to real bool
        "previsao_entrega": "2024-04-01",
        "link_rastreio": "http://x",
        "screenshot_path": "/tmp/a.png",
        "status_texto": "Entregue",
        "erro": "",
    }
    assert out["entregue"] is True

    # Missing attrs default: entregue -> False, strings -> "".
    empty = api._ser_rastreio(FakeRastreio())
    assert empty == {
        "numero_nfe": "",
        "transportadora": "",
        "entregue": False,
        "previsao_entrega": "",
        "link_rastreio": "",
        "screenshot_path": "",
        "status_texto": "",
        "erro": "",
    }
    assert empty["entregue"] is False


# ── _nota_card ──────────────────────────────────────────────────────────────
def test_char_nota_card_full_shape():
    api = _api()  # noqa: F841  (kept for parity; _nota_card is module-level)
    # info_complementar="" keeps the output decoupled from the heavy parser
    # (parsear_info_complementar("") == {}), making every block deterministic.
    nf = FakeNF(
        numero="12345",
        data_emissao="2024-03-15",
        transportadora_nome="BRASPRESS TRANSPORTES",
        destinatario_nome="PREFEITURA DE EXEMPLO",
        destinatario_uf="RS",
        destinatario_cidade="PORTO ALEGRE",
        destinatario_cep="90010123",
        produtos_resumo="Notebooks e perifericos",
        chave_acesso="CHAVE123",
    )
    card = web_app.nota_card(2, nf)

    assert card["indice"] == 2
    assert card["numero"] == "12345"
    assert card["chave"] == "CHAVE123"
    # Header: index, NF number, transportadora display, formatted emission date.
    assert card["header"] == "[2] NF-e 12345 — BRASPRESS TRANSPORTES  |  Emissão: 15/03/2024"

    assert card["bloco_licitacao"] == (
        "Processo:   |  PE:   |  Ata:   |  Contrato:   |  Empenho:   |  OF: \n"
        "Entrega:   |  Pagamento: \n"
        "PREFEITURA DE EXEMPLO/RS\n"      # destinatário gets "/UF" appended
        "CRM:   |  PD: \n"
        "\n"
        "NOTA FISCAL: 12345  |  DATA NF: 15/03/2024\n"
        "PRODUTOS: Notebooks e perifericos\n"
        "TRANSPORTADORA: BRASPRESS  |  RASTREIO: (NÃO PREENCHA)"  # transp key uppercased
    )

    assert card["bloco_entrega"] == (
        "LOCAL DE ENTREGA: \n"
        "ENDEREÇO: \n"
        "CEP: 90010-123\n"               # destinatario_cep formatted ##### - ###
        "PORTO ALEGRE/RS\n"
        "\n"
        "AGENDAMENTO: \n"
        "HORÁRIO:   |  CONTATO:   |  TELEFONE:"
    )


def test_char_nota_card_chave_fallback_when_no_chave_acesso():
    # When chave_acesso is empty, chave falls back to "nf-{indice}-{numero}".
    nf = FakeNF(numero="777", transportadora_nome="DESCONHECIDA LTDA")
    card = web_app.nota_card(5, nf)
    assert card["chave"] == "nf-5-777"
    # Unknown carrier name (not in mapping) -> transp_display uses the raw name;
    # transp_bloco falls back to the first word of the name, uppercased.
    assert card["header"].startswith("[5] NF-e 777 — DESCONHECIDA LTDA")
    assert "TRANSPORTADORA: DESCONHECIDA" in card["bloco_licitacao"]


def test_char_nota_card_security_cnpj_never_surfaced():
    # SECURITY BOUNDARY: _nota_card never reads emitente_cnpj/destinatario_cnpj,
    # so those raw CNPJs must NOT appear in any rendered text block.
    nf = FakeNF(
        numero="900",
        transportadora_nome="BRASPRESS",
        emitente_cnpj="11222333000181",
        destinatario_cnpj="99888777000166",
        destinatario_nome="ORGAO PUBLICO",
        destinatario_uf="SP",
    )
    card = web_app.nota_card(1, nf)
    blob = card["bloco_licitacao"] + "\n" + card["bloco_entrega"] + "\n" + card["header"]
    assert "11222333000181" not in blob   # emitente CNPJ never surfaced
    assert "99888777000166" not in blob   # destinatário CNPJ never surfaced


def test_char_nota_card_passes_unparsed_info_complementar_verbatim():
    # QUIRK (pinned, NOT a fix): any info_complementar text that the parser does
    # not recognize as a structured field is captured as 'outras_info_licitacao'
    # and appended VERBATIM under "Outras informações da licitação:". So raw,
    # unsanitized content (here an <xml> fragment) DOES flow into the card text.
    # This is the current behavior — pinned so a later sanitization change is seen.
    raw = "<xml>conteudo bruto nao sanitizado</xml>"
    nf = FakeNF(numero="901", transportadora_nome="BRASPRESS", info_complementar=raw)
    card = web_app.nota_card(1, nf)
    assert card["bloco_licitacao"].endswith(
        "\n\nOutras informações da licitação:\n" + raw
    )
    assert raw in card["bloco_licitacao"]


# ── _montar_romaneio_fornecedor (business-critical FOB format) ──────────────
_FOB_CFG = """
[fretio]
ui_tema = "escuro"
max_paralelo = 4

[romaneio]
cep_origem = "01001000"
cnpj_pagador_padrao = "11222333000181"

[transportadoras.braspress]
habilitado = true
cnpj = "12345678000199"
senha = "super-secret-pw"
ufs_atendidas = ["rs", "sc"]

[transportadoras.trd]
habilitado = false
email = "user@example.com"
senha = "trd-secret"
"""

_EXPECTED_FOB = (
    "CNPJ/CPF: 12.345.678/0001-99\n"   # from EMPRESA braspress.cnpj, NOT the form
    "CEP: 01001-000\n"                 # from EMPRESA romaneio.cep_origem
    "- VOL: 10\n"
    "- CUBAGEM: 0.600000 m3\n"
    "- PESO: 25.00 kg\n"
    "- TOTAL: R$ 1234.56\n"            # NOTE: dot-decimal, no thousands sep (quirk)
    "10 x Volume fornecedor - 2.500 kg - 0.060000 m3 - 30x40x50"
)


def test_char_montar_romaneio_fornecedor_exact_format_peso_caixa():
    api = _api(_write_cfg(_FOB_CFG))
    form = {
        "cnpj": "98.765.432/0001-10",   # fornecedor CNPJ — NOT used in the text
        "cep": "04567-000",             # fornecedor CEP — returned separately
        "qtd": "10",
        "alt": "30", "larg": "40", "comp": "50",
        "peso_cx": "2,5",               # BR decimal comma -> 2.5 kg/box
        "valor": "1.234,56",            # BR currency -> 1234.56
    }
    texto, cep_forn = api._montar_romaneio_fornecedor(form)
    assert texto == _EXPECTED_FOB
    assert cep_forn == "04567000"       # form CEP, digits only


def test_char_montar_romaneio_fornecedor_peso_total_matches_peso_caixa():
    # peso_total path: peso_caixa derived as total/qtd. With total=25, qtd=10
    # the per-box weight is 2.5 -> identical text to the peso_cx path above.
    api = _api(_write_cfg(_FOB_CFG))
    form = {
        "cnpj": "98.765.432/0001-10", "cep": "04567-000",
        "qtd": "10", "alt": "30", "larg": "40", "comp": "50",
        "peso_total": "25", "valor": "1.234,56",
    }
    texto, cep_forn = api._montar_romaneio_fornecedor(form)
    assert texto == _EXPECTED_FOB
    assert cep_forn == "04567000"


def test_char_montar_romaneio_fornecedor_weight_required_raises():
    api = _api(_write_cfg(_FOB_CFG))
    form = {
        "cnpj": "98.765.432/0001-10", "cep": "04567-000",
        "qtd": "10", "alt": "30", "larg": "40", "comp": "50", "valor": "10",
    }
    # Neither peso_cx nor peso_total provided -> ValueError raised *before*
    # the empresa-config validation block.
    with pytest.raises(ValueError) as exc:
        api._montar_romaneio_fornecedor(form)
    assert str(exc.value) == (
        "Informe o peso por volume ou o peso total (pelo menos um é obrigatório)"
    )


def test_char_montar_romaneio_fornecedor_missing_empresa_cfg_aggregates_errors():
    # Empty/missing config -> empresa CNPJ and CEP both unconfigured; errors are
    # accumulated and joined with "\n" in a single ValueError.
    api = _api(Path("does-not-exist-config.toml"))
    form = {
        "cnpj": "1", "cep": "04567000",
        "qtd": "10", "alt": "30", "larg": "40", "comp": "50",
        "peso_cx": "2", "valor": "10",
    }
    with pytest.raises(ValueError) as exc:
        api._montar_romaneio_fornecedor(form)
    msg = str(exc.value)
    assert "CNPJ da empresa não configurado (Configurações > Credenciais)" in msg
    assert "CEP da empresa não configurado (Configurações > Empresa > CEP de origem)" in msg
    assert "\n" in msg  # multiple errors joined with newline


# ── config_get — Phase 1 password sentinel + no plaintext leak ──────────────
def test_char_config_get_password_sentinel_and_no_plaintext_leak():
    import json

    api = _api(_write_cfg(_FOB_CFG))
    cg = api.config_get()

    by_name = {c["nome"]: c for c in cg["transportadoras"]}

    # braspress has a stored password -> password field serializes with EMPTY
    # valor but tem_valor True (the sentinel telling the UI "a secret exists").
    bras = by_name["braspress"]
    senha_field = next(f for f in bras["campos"] if f["key"] == "senha")
    assert senha_field["tipo"] == "password"
    assert senha_field["valor"] == ""        # never the plaintext
    assert senha_field["tem_valor"] is True  # but flagged as present

    # Non-password fields DO carry their value (e.g. cnpj, ufs uppercased).
    cnpj_field = next(f for f in bras["campos"] if f["key"] == "cnpj")
    assert cnpj_field["valor"] == "12345678000199"
    assert cnpj_field["tem_valor"] is True
    assert bras["ufs_atendidas"] == ["RS", "SC"]
    assert bras["habilitado"] is True

    # trd has a stored password too (sentinel True), but is disabled.
    trd = by_name["trd"]
    trd_senha = next(f for f in trd["campos"] if f["key"] == "senha")
    assert trd_senha["valor"] == "" and trd_senha["tem_valor"] is True
    assert trd["habilitado"] is False

    # A carrier with NO stored secret -> tem_valor False.
    agex = by_name["agex"]
    agex_senha = next(f for f in agex["campos"] if f["key"] == "senha")
    assert agex_senha["valor"] == "" and agex_senha["tem_valor"] is False

    # SECURITY BOUNDARY: no plaintext password may cross the bridge anywhere
    # in the serialized config payload.
    dump = json.dumps(cg, ensure_ascii=False)
    assert "super-secret-pw" not in dump
    assert "trd-secret" not in dump


def test_char_config_get_defaults_on_missing_config():
    # Missing config file -> _load_config returns {} -> documented defaults.
    api = _api(Path("missing-cfg-defaults.toml"), empresa="MinhaEmpresa")
    cg = api.config_get()
    assert cg["empresa"]["nome"] == "MinhaEmpresa"
    assert cg["empresa"]["cep_origem"] == ""
    assert cg["empresa"]["cnpj_pagador"] == ""
    assert cg["empresa"]["paralelas"] == 3            # default max_paralelo
    assert cg["aparencia"]["tema"] == "sistema"       # default ui_tema
    assert cg["aparencia"]["raio"] == "Suave"
    assert cg["aparencia"]["botao"] == "Solido"
    assert cg["aparencia"]["accent"] == "Claude"
    assert cg["aparencia"]["temas"] == ["claro", "escuro", "sistema"]
    # All 8 registered carriers are always present, even with no config.
    names = [c["nome"] for c in cg["transportadoras"]]
    assert names == ["braspress", "trd", "agex", "eucatur", "rodonaves",
                     "alfa", "coopex", "translovato"]
    # ufs mirrors company_config.TODAS_UFS (27 federative units).
    assert len(cg["ufs"]) == 27
    assert "RS" in cg["ufs"] and "SP" in cg["ufs"]


# ── get_bootstrap (read path; no window/session needed) ─────────────────────
def test_char_get_bootstrap_shape_with_config():
    api = _api(_write_cfg(_FOB_CFG))
    bs = api.get_bootstrap()
    assert bs["empresa"] == "ACME"
    assert bs["tema"] == "escuro"
    assert bs["tema_efetivo"] == "escuro"
    # Both configured carriers surface with their habilitado flag + "pending".
    transp = {t["nome"]: t for t in bs["transportadoras"]}
    assert transp["braspress"] == {"nome": "braspress", "habilitado": True, "status": "pending"}
    assert transp["trd"] == {"nome": "trd", "habilitado": False, "status": "pending"}
    # Dashboard always starts empty (session state, not persisted) in the POC.
    assert bs["dashboard"] == {
        "total_romaneios": 0,
        "total_volumes": 0,
        "melhor_frete": None,
        "sucesso_pct": None,
        "romaneios_recentes": [],
    }


def test_char_get_bootstrap_defaults_on_missing_config():
    api = _api(Path("missing-bootstrap.toml"), empresa="Vazia")
    bs = api.get_bootstrap()
    assert bs["empresa"] == "Vazia"
    assert bs["tema"] == "sistema"             # default
    assert bs["tema_efetivo"] == "escuro"      # 'sistema' -> 'escuro'
    assert bs["transportadoras"] == []
    assert bs["raio"] == "Suave"
    assert bs["botao"] == "Solido"
