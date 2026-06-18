"""Regressão de segurança (CWE-918): updater só aceita download_url https em
host permitido (GitHub), barrando SSRF/redirecionamento via version_api."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))

import updater  # noqa: E402


@pytest.mark.parametrize("url", [
    "https://github.com/owner/repo/releases/download/v2/Fretio-Update-latest.zip",
    "https://objects.githubusercontent.com/abc/Fretio.zip",
    "https://release-assets.githubusercontent.com/x/Fretio.zip",
])
def test_validate_download_url_accepts_github_https(url):
    assert updater._validate_download_url(url) == url


@pytest.mark.parametrize("url", [
    "http://github.com/owner/repo/x.zip",          # esquema inseguro
    "https://evil.example.com/x.zip",              # host fora da allowlist
    "https://github.com.attacker.net/x.zip",       # suffix spoof
    "file:///C:/Windows/System32/calc.exe",        # esquema file
    "https://127.0.0.1/internal",                  # SSRF interno
])
def test_validate_download_url_rejects_untrusted(url):
    with pytest.raises(ValueError):
        updater._validate_download_url(url)


def test_check_server_version_rejects_evil_download_url(monkeypatch):
    monkeypatch.setattr(
        updater, "_version_api",
        lambda url: {"latest_version": "9.9", "download_url": "https://evil.example.com/payload.zip"},
    )
    with pytest.raises(ValueError):
        updater._check_server_version("https://fretio.api.br/version", "1.0")
