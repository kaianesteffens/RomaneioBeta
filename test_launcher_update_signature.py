"""Regressão de segurança (CWE-347): o launcher só extrai/instala um update
cuja assinatura Ed25519 foi verificada. Cobre a LÓGICA de verificação (a
validação do empacotamento/build é feita à parte, via build Windows)."""
import base64
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))        # update_security
sys.path.insert(0, str(ROOT / "installer"))  # launcher

import launcher  # noqa: E402

ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
from cryptography.hazmat.primitives import serialization  # noqa: E402


def _keypair(monkeypatch):
    priv = ed25519.Ed25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    monkeypatch.setenv("FRETIO_UPDATE_PUBLIC_KEY_B64", base64.b64encode(pub_raw).decode())
    return priv


def _arm_download(monkeypatch, sig_source: Path):
    """Simula o download do .sig copiando um arquivo local para o destino."""
    def fake_download(url, dest, total, status_cb, progress_cb):
        shutil.copy(sig_source, dest)
    monkeypatch.setattr(launcher, "_download", fake_download)


_ASSETS = [{"name": "fretio-update-latest.zip.sig", "browser_download_url": "https://x/s.sig", "size": 64}]


def test_select_signature_asset_matches_zip_name():
    assets = [
        {"name": "fretio-update-latest.zip", "browser_download_url": "u"},
        {"name": "fretio-update-latest.zip.sig", "browser_download_url": "s"},
    ]
    got = launcher._select_signature_asset(assets, "fretio-update-latest.zip")
    assert got is not None and got["browser_download_url"] == "s"


def test_verify_accepts_valid_signature(tmp_path, monkeypatch):
    priv = _keypair(monkeypatch)
    zip_path = tmp_path / "update.zip"
    payload = b"PK\x03\x04 conteudo do update legitimo"
    zip_path.write_bytes(payload)
    sig_src = tmp_path / "src.sig"
    sig_src.write_bytes(priv.sign(payload))
    _arm_download(monkeypatch, sig_src)

    ok = launcher._verify_downloaded_update(
        zip_path, tmp_path / "dl.sig", _ASSETS, "fretio-update-latest.zip",
        lambda *_: None, lambda *_: None,
    )
    assert ok is True


def test_verify_rejects_tampered_zip(tmp_path, monkeypatch):
    priv = _keypair(monkeypatch)
    zip_path = tmp_path / "update.zip"
    sig_src = tmp_path / "src.sig"
    sig_src.write_bytes(priv.sign(b"conteudo original assinado"))
    zip_path.write_bytes(b"conteudo ADULTERADO pelo atacante")  # assinatura não bate
    _arm_download(monkeypatch, sig_src)

    ok = launcher._verify_downloaded_update(
        zip_path, tmp_path / "dl.sig", _ASSETS, "fretio-update-latest.zip",
        lambda *_: None, lambda *_: None,
    )
    assert ok is False


def test_verify_fails_closed_when_no_signature_asset(tmp_path, monkeypatch):
    _keypair(monkeypatch)
    zip_path = tmp_path / "update.zip"
    zip_path.write_bytes(b"qualquer coisa")
    # Nenhum asset .sig na release -> deve falhar fechado (sem instalar).
    ok = launcher._verify_downloaded_update(
        zip_path, tmp_path / "dl.sig", [], "fretio-update-latest.zip",
        lambda *_: None, lambda *_: None,
    )
    assert ok is False
