import base64
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

cryptography = pytest.importorskip(
    "cryptography",
    reason="test_update_security exige cryptography; fora do perfil quick/offline do Codex",
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app"))

import update_security
import updater


def _write_update_zip(path: Path, *, traversal: bool = False) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        if traversal:
            zf.writestr("../evil.txt", "owned")
        else:
            zf.writestr("Fretio.exe", "fake exe")
            zf.writestr("_internal/version.txt", "2.0")


def _sign_file(path: Path, private_key: ed25519.Ed25519PrivateKey, sig_path: Path) -> None:
    signature = private_key.sign(path.read_bytes())
    sig_path.write_text(base64.b64encode(signature).decode("ascii"), encoding="ascii")


@pytest.fixture
def signing_key(monkeypatch):
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_key_b64 = base64.b64encode(
        public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    monkeypatch.setattr(update_security, "_get_update_public_key_b64", lambda: public_key_b64)
    return private_key


def _fake_update_info(tmp_path: Path) -> updater.UpdateInfo:
    return updater.UpdateInfo(
        tag="v2.0",
        version="2.0",
        download_url="https://example.test/Fretio-Update-latest.zip",
        asset_name="Fretio-Update-latest.zip",
        asset_size=10,
        release_notes="",
        html_url="https://example.test/release",
        source_repo="owner/repo",
        signature_download_url="https://example.test/Fretio-Update-latest.zip.sig",
        signature_asset_name="Fretio-Update-latest.zip.sig",
    )


def _patch_update_dirs(monkeypatch, tmp_path: Path):
    update_dir = tmp_path / "update"
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    monkeypatch.setattr(updater, "_license_dir_update", lambda: update_dir)
    monkeypatch.setattr(updater, "_get_app_dir", lambda: app_dir)
    return update_dir, app_dir


def _patch_downloads(monkeypatch, downloads: dict[str, Path]) -> None:
    def fake_download(url, dest, total_size=0, callback=None):
        source = downloads.get(url)
        if source is None:
            raise FileNotFoundError(url)
        shutil.copyfile(source, dest)

    monkeypatch.setattr(updater, "_download_with_progress", fake_download)


def test_apply_update_with_valid_signature_prepares_update(monkeypatch, tmp_path, signing_key):
    zip_src = tmp_path / "valid.zip"
    sig_src = tmp_path / "valid.zip.sig"
    _write_update_zip(zip_src)
    _sign_file(zip_src, signing_key, sig_src)
    info = _fake_update_info(tmp_path)
    update_dir, _app_dir = _patch_update_dirs(monkeypatch, tmp_path)
    _patch_downloads(
        monkeypatch,
        {
            info.download_url: zip_src,
            info.signature_download_url: sig_src,
        },
    )

    assert updater.apply_update(info, callback=lambda _msg: None) is True

    assert (update_dir / "_pending_update").exists()
    assert (update_dir / "extracted" / "Fretio.exe").exists()


def test_apply_update_with_invalid_signature_does_not_prepare_update(monkeypatch, tmp_path, signing_key):
    zip_src = tmp_path / "valid.zip"
    sig_src = tmp_path / "valid.zip.sig"
    _write_update_zip(zip_src)
    sig_src.write_text(base64.b64encode(b"0" * 64).decode("ascii"), encoding="ascii")
    info = _fake_update_info(tmp_path)
    update_dir, _app_dir = _patch_update_dirs(monkeypatch, tmp_path)
    messages = []
    _patch_downloads(
        monkeypatch,
        {
            info.download_url: zip_src,
            info.signature_download_url: sig_src,
        },
    )

    assert updater.apply_update(info, callback=messages.append) is False

    assert not (update_dir / "_pending_update").exists()
    assert not (update_dir / "extracted").exists()
    assert any("Assinatura do update invalida" in msg for msg in messages)


def test_apply_update_without_signature_does_not_prepare_release_update(monkeypatch, tmp_path, signing_key):
    zip_src = tmp_path / "valid.zip"
    _write_update_zip(zip_src)
    info = _fake_update_info(tmp_path)
    update_dir, _app_dir = _patch_update_dirs(monkeypatch, tmp_path)
    messages = []
    _patch_downloads(monkeypatch, {info.download_url: zip_src})

    assert updater.apply_update(info, callback=messages.append) is False

    assert not (update_dir / "_pending_update").exists()
    assert not (update_dir / "extracted").exists()
    assert any("Erro na atualização" in msg for msg in messages)


def test_apply_update_keeps_path_traversal_blocked_after_valid_signature(monkeypatch, tmp_path, signing_key):
    zip_src = tmp_path / "traversal.zip"
    sig_src = tmp_path / "traversal.zip.sig"
    _write_update_zip(zip_src, traversal=True)
    _sign_file(zip_src, signing_key, sig_src)
    info = _fake_update_info(tmp_path)
    update_dir, _app_dir = _patch_update_dirs(monkeypatch, tmp_path)
    _patch_downloads(
        monkeypatch,
        {
            info.download_url: zip_src,
            info.signature_download_url: sig_src,
        },
    )

    assert updater.apply_update(info, callback=lambda _msg: None) is False

    assert not (update_dir / "_pending_update").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_check_for_update_records_matching_signature_asset(monkeypatch):
    monkeypatch.setattr(updater, "get_repo_candidates_from_config", lambda: [])
    monkeypatch.setattr(
        updater,
        "_github_api",
        lambda _url: {
            "tag_name": "v2.0",
            "body": "",
            "html_url": "https://example.test/release",
            "assets": [
                {
                    "name": "Fretio-Update-latest.zip",
                    "browser_download_url": "https://example.test/Fretio-Update-latest.zip",
                    "size": 10,
                },
                {
                    "name": "Fretio-Update-latest.zip.sig",
                    "browser_download_url": "https://example.test/Fretio-Update-latest.zip.sig",
                    "size": 88,
                },
            ],
        },
    )

    info = updater.check_for_update("owner/repo", "1.0")

    assert info is not None
    assert info.asset_name == "Fretio-Update-latest.zip"
    assert info.signature_asset_name == "Fretio-Update-latest.zip.sig"
    assert info.signature_download_url == "https://example.test/Fretio-Update-latest.zip.sig"
