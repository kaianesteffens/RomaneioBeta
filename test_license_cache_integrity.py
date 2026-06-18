"""Regressão de segurança (CWE-345): o cache de validação offline é assinado
por HMAC (machine-specific). Cache adulterado ou legado (sem MAC) é rejeitado."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))

import license as lic  # noqa: E402


def _setup(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    (appdata / "Fretio").mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(lic, "get_machine_id", lambda: "MAQ-FIXA-1")
    return lic._validation_cache_file()


def test_signed_cache_roundtrips(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    lic._save_validation_cache("KEY-1", lic.LicenseStatus(valid=True, owner="Dono"))
    loaded = lic._load_validation_cache("KEY-1")
    assert loaded is not None and loaded.valid is True and loaded.offline is True


def test_tampered_cache_is_rejected(monkeypatch, tmp_path):
    cache_file = _setup(monkeypatch, tmp_path)
    lic._save_validation_cache("KEY-1", lic.LicenseStatus(valid=False, blocked=True))
    # Atacante edita o payload para se conceder uma licença válida, sem re-assinar.
    signed = json.loads(cache_file.read_text(encoding="utf-8"))
    signed["d"]["valid"] = True
    signed["d"]["blocked"] = False
    cache_file.write_text(json.dumps(signed), encoding="utf-8")

    assert lic._load_validation_cache("KEY-1") is None  # MAC não confere → rejeitado


def test_legacy_unsigned_cache_is_rejected(monkeypatch, tmp_path):
    cache_file = _setup(monkeypatch, tmp_path)
    # Formato legado (dict plano, sem "mac") — deve ser invalidado.
    cache_file.write_text(
        json.dumps({"key": "KEY-1", "valid": True, "owner": "x", "blocked": False, "timestamp": 9_999_999_999}),
        encoding="utf-8",
    )
    assert lic._load_validation_cache("KEY-1") is None
