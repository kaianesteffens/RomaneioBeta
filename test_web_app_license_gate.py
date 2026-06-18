"""Regressão de segurança (CWE-287): operações privilegiadas exigem licença
validada no lado Python — a checagem só no JS (WebView) é burlável pelo console."""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

if "webview" not in sys.modules:
    sys.modules["webview"] = types.SimpleNamespace(
        OPEN_DIALOG=10, create_window=lambda *a, **k: None, start=lambda *a, **k: None,
    )

import license as license_mod  # noqa: E402
import web_app  # noqa: E402


def test_privileged_ops_blocked_without_license():
    api = web_app.Api(empresa="teste", config_path=None)  # _license_ok = False
    api._notas = [object()]  # garante que rastreio passe do check de notas
    assert api.cotacao_iniciar("um romaneio") == {"erro": "Licença não validada."}
    assert api.rastreio_iniciar() == {"erro": "Licença não validada."}
    assert api.nfe_selecionar() == {"erro": "Licença não validada."}
    r = api.abrir_app()
    assert r.get("ok") is False and "Licença" in r.get("erro", "")


def test_startup_licenca_estado_unlocks_when_valid(monkeypatch):
    monkeypatch.setattr(license_mod, "get_saved_license", lambda: "CHAVE-VALIDA")
    monkeypatch.setattr(license_mod, "get_machine_id", lambda: "machine-1")
    monkeypatch.setattr(
        license_mod, "validate_license",
        lambda key, machine: types.SimpleNamespace(valid=True, message="", owner="x", blocked=False, expires=None),
    )
    api = web_app.Api(empresa="teste", config_path=None)
    assert api._license_ok is False

    estado = api.startup_licenca_estado()
    assert estado["fase"] == "ok"
    assert api._license_ok is True

    # Agora abrir_app não é mais bloqueado pela licença.
    assert api.abrir_app().get("ok") is True


def test_revoked_license_keeps_ops_blocked(monkeypatch):
    monkeypatch.setattr(license_mod, "get_saved_license", lambda: "CHAVE-REVOGADA")
    monkeypatch.setattr(license_mod, "get_machine_id", lambda: "machine-1")
    monkeypatch.setattr(
        license_mod, "validate_license",
        lambda key, machine: types.SimpleNamespace(valid=False, message="revogada", owner="", blocked=True, expires=None),
    )
    api = web_app.Api(empresa="teste", config_path=None)
    estado = api.startup_licenca_estado()
    assert estado["fase"] == "revogada"
    assert api._license_ok is False
    assert api.cotacao_iniciar("x") == {"erro": "Licença não validada."}
