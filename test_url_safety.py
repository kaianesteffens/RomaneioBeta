"""Regressão de segurança (CWE-918/CWE-319): URLs de endpoint configuráveis só
aceitam https (ou http localhost para dev); http arbitrário cai para o default."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))

import url_safety  # noqa: E402
import usage_reporter  # noqa: E402


def test_is_safe_api_url():
    assert url_safety.is_safe_api_url("https://fretio.api.br/api/x")
    assert url_safety.is_safe_api_url("http://localhost:8000/x")
    assert url_safety.is_safe_api_url("http://127.0.0.1/x")
    assert not url_safety.is_safe_api_url("http://evil.example.com/x")
    assert not url_safety.is_safe_api_url("file:///etc/passwd")
    assert not url_safety.is_safe_api_url("ftp://host/x")
    assert not url_safety.is_safe_api_url("")


def test_require_https_url_falls_back_on_unsafe():
    assert url_safety.require_https_url("http://evil.example.com", "https://d/") == "https://d/"
    assert url_safety.require_https_url("https://ok/", "https://d/") == "https://ok/"
    assert url_safety.require_https_url("", "https://d/") == "https://d/"


def test_usage_resolver_rejects_http_env(monkeypatch):
    monkeypatch.setenv("FRETIO_USAGE_API_URL", "http://evil.example.com/usage")
    assert usage_reporter._get_usage_api_url() == usage_reporter.DEFAULT_USAGE_API_URL


def test_usage_resolver_keeps_https_env(monkeypatch):
    monkeypatch.setenv("FRETIO_USAGE_API_URL", "https://meu.host/usage")
    assert usage_reporter._get_usage_api_url() == "https://meu.host/usage"
