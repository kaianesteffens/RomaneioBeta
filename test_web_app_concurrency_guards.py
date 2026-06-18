"""Concurrency guards on web_app.Api (Phase 7 step 3).

cotacao_iniciar already rejected a second concurrent cotação; rastreio_iniciar
had NO equivalent guard (you could launch several at once). These tests pin:
  * a second concurrent cotação/rastreio is rejected while one is in flight;
  * the in-flight flag is RESERVED atomically and RESET on every early-return
    failure path (so a backend error does not leave the op stuck "in progress").
"""

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


def _api():
    api = web_app.Api(empresa="teste", config_path=None)
    api._gate = lambda feature: None  # isola o gating de licença/remote
    api._license_ok = True  # sessão licenciada: isola dos testes de gate de licença
    return api


def _boom():
    raise RuntimeError("backend indisponível")


def test_cotacao_iniciar_rejects_second_concurrent_call():
    api = _api()
    api._cotando = True
    r = api.cotacao_iniciar("algum romaneio colado")
    assert "andamento" in (r.get("erro") or "").lower()


def test_rastreio_iniciar_rejects_second_concurrent_call():
    api = _api()
    api._notas = [object()]  # não-vazio p/ passar do primeiro check
    api._rastreando = True
    r = api.rastreio_iniciar()
    assert "andamento" in (r.get("erro") or "").lower()


def test_cotacao_flag_is_reset_when_backend_fails():
    api = _api()
    api._ensure_backend = _boom
    r = api.cotacao_iniciar("algum romaneio colado")
    assert "Falha ao preparar" in (r.get("erro") or "")
    assert api._cotando is False  # reservada e liberada — não fica presa


def test_rastreio_flag_is_reset_when_backend_fails():
    api = _api()
    api._notas = [object()]
    api._ensure_backend = _boom
    r = api.rastreio_iniciar()
    assert "Falha ao preparar" in (r.get("erro") or "")
    assert api._rastreando is False
