import sys
from pathlib import Path
from urllib.error import URLError


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))

import updater


def test_server_version_returns_new_update(monkeypatch):
    monkeypatch.setattr(updater, "get_version_api_url_from_config", lambda: "https://api.test/api/version/latest")
    monkeypatch.setattr(
        updater,
        "_version_api",
        lambda _url: {
            "latest_version": "2.0.0",
            "download_url": "https://github.com/org/releases/download/v2/Fretio-Update-latest.zip",
            "mandatory": False,
            "release_notes": "Notas publicas",
        },
    )

    info = updater.check_for_update("owner/releases", "1.0.0")

    assert info == updater.UpdateInfo(
        tag="2.0.0",
        version="2.0.0",
        download_url="https://github.com/org/releases/download/v2/Fretio-Update-latest.zip",
        asset_name="Fretio-Update-latest.zip",
        asset_size=0,
        release_notes="Notas publicas",
        html_url="",
        source_repo="version_api",
        mandatory=False,
        source="server",
    )


def test_server_version_returns_same_version(monkeypatch):
    github_calls = []
    monkeypatch.setattr(updater, "get_version_api_url_from_config", lambda: "https://api.test/api/version/latest")
    monkeypatch.setattr(
        updater,
        "_version_api",
        lambda _url: {
            "latest_version": "2.0.0",
            "download_url": "https://github.com/org/releases/download/v2/Fretio-Update-latest.zip",
            "mandatory": False,
            "release_notes": "Sem update",
        },
    )
    monkeypatch.setattr(updater, "get_repo_candidates_from_config", lambda: [])
    monkeypatch.setattr(updater, "_github_api", lambda url: github_calls.append(url))

    assert updater.check_for_update("owner/releases", "2.0.0") is None
    assert github_calls == []


def test_server_unavailable_falls_back_to_github_releases(monkeypatch):
    monkeypatch.setattr(updater, "get_version_api_url_from_config", lambda: "https://api.test/api/version/latest")
    monkeypatch.setattr(updater, "_version_api", lambda _url: (_ for _ in ()).throw(URLError("offline")))
    monkeypatch.setattr(updater, "get_repo_candidates_from_config", lambda: [])
    monkeypatch.setattr(
        updater,
        "_github_api",
        lambda _url: {
            "tag_name": "v2.1.0",
            "body": "Notas GitHub",
            "html_url": "https://github.com/org/releases/tag/v2.1.0",
            "assets": [
                {
                    "name": "Fretio-Update-latest.zip",
                    "browser_download_url": "https://github.com/org/releases/download/v2.1/Fretio-Update-latest.zip",
                    "size": 456,
                },
            ],
        },
    )

    info = updater.check_for_update("owner/releases", "2.0.0")

    assert info is not None
    assert info.version == "2.1.0"
    assert info.download_url == "https://github.com/org/releases/download/v2.1/Fretio-Update-latest.zip"
    assert info.release_notes == "Notas GitHub"
    assert info.source == "github"


def test_server_version_mandatory_true(monkeypatch):
    monkeypatch.setattr(updater, "get_version_api_url_from_config", lambda: "https://api.test/api/version/latest")
    monkeypatch.setattr(
        updater,
        "_version_api",
        lambda _url: {
            "latest_version": "2.0.0",
            "download_url": "https://github.com/org/releases/download/v2/Fretio-Update-latest.zip",
            "mandatory": True,
            "release_notes": "Atualizacao obrigatoria",
        },
    )

    info = updater.check_for_update("owner/releases", "1.0.0")

    assert info is not None
    assert info.mandatory is True
    assert info.release_notes == "Atualizacao obrigatoria"
    assert info.download_url.endswith("Fretio-Update-latest.zip")
