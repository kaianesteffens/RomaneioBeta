"""Golden snapshot of the web_app.Api PUBLIC surface (the pywebview contract).

pywebview exposes every public method of the ``js_api`` object as
``pywebview.api.<name>``; the JS frontend (app/web/*) calls these by exact name
and signature. This test pins that surface so the Phase 7 decomposition of the
``Api`` god-object into per-domain delegates cannot silently rename, drop, or
re-signature any method the frontend depends on.

Only the PUBLIC surface is pinned — private helpers (``_emit``, ``_ser_*``, …)
are free to move between classes during the split. The event envelope and
serializers are covered separately by test_char_web_app_serializers.py.
"""

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

# pywebview não é instalado no ambiente de CI (ubuntu); stuba a superfície mínima
# que web_app importa no load (mesmo padrão de test_char_web_app_serializers.py).
import types
if "webview" not in sys.modules:
    sys.modules["webview"] = types.SimpleNamespace(
        OPEN_DIALOG=10,
        create_window=lambda *a, **k: None,
        start=lambda *a, **k: None,
    )

import web_app


# name -> str(inspect.signature(...)). Annotations are strings because web_app
# uses `from __future__ import annotations`.
EXPECTED_PUBLIC_SURFACE = {
    "abrir_app": "(self) -> 'dict'",
    "abrir_externo": "(self, alvo: 'str') -> 'dict'",
    "abrir_screenshots": "(self) -> 'dict'",
    "attach_window": "(self, window: 'Any') -> 'None'",
    "config_get": "(self) -> 'dict'",
    "config_salvar_aparencia": "(self, data: 'dict') -> 'dict'",
    "config_salvar_credenciais": "(self, nome: 'str', campos: 'dict') -> 'dict'",
    "config_salvar_empresa": "(self, data: 'dict') -> 'dict'",
    "config_salvar_transportadora": "(self, nome: 'str', data: 'dict') -> 'dict'",
    "cotacao_iniciar": "(self, romaneio_texto: 'str', cnpj_remetente: 'str' = '', cep_origem: 'str' = '') -> 'dict'",
    "fornecedor_cotar": "(self, form: 'dict') -> 'dict'",
    "get_bootstrap": "(self) -> 'dict[str, Any]'",
    "get_dashboard": "(self) -> 'dict'",
    "get_romaneio_texto": "(self) -> 'dict'",
    "listar_empresas": "(self) -> 'list[str]'",
    "nfe_cards": "(self) -> 'dict'",
    "nfe_selecionar": "(self) -> 'dict'",
    "rastreio_iniciar": "(self, chaves: 'list | None' = None) -> 'dict'",
    "rastreio_limpar": "(self) -> 'dict'",
    "romaneio_processar": "(self) -> 'dict'",
    "sair": "(self) -> 'dict'",
    "set_tema": "(self, modo: 'str') -> 'dict[str, Any]'",
    "startup_aplicar_update": "(self) -> 'dict'",
    "startup_ativar_licenca": "(self, key: 'str') -> 'dict'",
    "startup_criar_empresa": "(self, nome: 'str') -> 'dict'",
    "startup_empresas": "(self) -> 'list'",
    "startup_entrar": "(self, empresa: 'str') -> 'dict'",
    "startup_licenca_estado": "(self) -> 'dict'",
    "startup_pos_licenca": "(self) -> 'dict'",
    "startup_renomear_empresa": "(self, atual: 'str', novo: 'str') -> 'dict'",
    "trocar_empresa": "(self) -> 'dict'",
}


def _public_surface() -> dict[str, str]:
    api = web_app.Api
    surface = {}
    for name in dir(api):
        if name.startswith("_"):
            continue
        member = getattr(api, name)
        if callable(member):
            surface[name] = str(inspect.signature(member))
    return surface


def test_public_api_surface_names_unchanged():
    assert set(_public_surface()) == set(EXPECTED_PUBLIC_SURFACE)


def test_public_api_surface_signatures_unchanged():
    assert _public_surface() == EXPECTED_PUBLIC_SURFACE
