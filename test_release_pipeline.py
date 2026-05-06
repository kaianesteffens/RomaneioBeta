import sys
from pathlib import Path
from io import BytesIO
from urllib.error import HTTPError


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "installer"))

import error_reporter as er
import launcher
import updater


def test_updater_collects_repo_aliases_from_config_and_env(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    config_dir = appdata / "Fretio"
    config_dir.mkdir(parents=True)
    (config_dir / "CONFIG.toml").write_text(
        (
            "[fretio]\n"
            'github_repo = "owner/primary"\n'
            'github_repo_aliases = ["owner/legacy", "owner/fallback"]\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("FRETIO_GITHUB_REPO_ALIASES", "env/one, env/two")

    candidates = updater.get_repo_candidates_from_config()

    assert candidates[:5] == [
        "env/one",
        "env/two",
        "owner/primary",
        "owner/legacy",
        "owner/fallback",
    ]
    assert "kaianesteffens/RomaneioBeta-releases" in candidates


def test_updater_tries_repo_aliases_and_prefers_stable_latest_asset(monkeypatch):
    calls = []
    responses = {
        "https://api.github.com/repos/owner/primary/releases/latest": OSError("offline"),
        "https://api.github.com/repos/owner/fallback/releases/latest": {
            "tag_name": "v2.0.0",
            "body": "notes",
            "html_url": "https://example.test/release",
            "assets": [
                {"name": "Random.zip", "browser_download_url": "https://example.test/random.zip", "size": 1},
                {"name": "Fretio-Update-latest.zip", "browser_download_url": "https://example.test/latest.zip", "size": 2},
            ],
        },
    }

    def fake_api(url):
        calls.append(url)
        result = responses[url]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(updater, "_github_api", fake_api)
    monkeypatch.setattr(updater, "get_repo_candidates_from_config", lambda: ["owner/fallback"])

    info = updater.check_for_update(["owner/primary", "owner/fallback"], "1.0.0")

    assert info is not None
    assert info.source_repo == "owner/fallback"
    assert info.asset_name == "Fretio-Update-latest.zip"
    assert calls == [
        "https://api.github.com/repos/owner/primary/releases/latest",
        "https://api.github.com/repos/owner/fallback/releases/latest",
    ]


def test_launcher_prefers_stable_latest_zip_alias():
    asset = launcher._select_zip_asset(
        [
            {"name": "Fretio-Update-2.0.zip"},
            {"name": "Fretio-Update-latest.zip"},
        ]
    )

    assert asset is not None
    assert asset["name"] == "Fretio-Update-latest.zip"


def test_error_reporter_uses_env_fallback_without_embedded_secret(monkeypatch):
    monkeypatch.setenv("FRETIO_ERROR_GIST_ID", "gist-123")
    monkeypatch.setenv("FRETIO_ERROR_REPORT_TOKEN", "token-456")
    monkeypatch.setattr(er, "_iter_config_candidates", lambda: [])
    er._gist_id = ""
    er._token = ""
    er._initialized = False
    er._invalid_token_fingerprints.clear()

    er._load_config()

    assert er._EMBEDDED_ERROR_GIST_ID == ""
    assert er._EMBEDDED_ERROR_REPORT_TOKEN == ""
    assert er._gist_id == "gist-123"
    assert er._token == "token-456"


def test_error_reporter_configure_falls_back_to_global_config(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    root_cfg = appdata / "Fretio" / "CONFIG.toml"
    root_cfg.parent.mkdir(parents=True)
    root_cfg.write_text(
        "[fretio]\n"
        'error_gist_id = "gist-global"\n'
        'error_report_token = "token-global"\n',
        encoding="utf-8",
    )
    company_cfg = appdata / "Fretio" / "empresas" / "DARLU" / "CONFIG.toml"
    company_cfg.parent.mkdir(parents=True)
    company_cfg.write_text("[fretio]\n", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    er._gist_id = ""
    er._token = ""
    er._initialized = False
    er._invalid_token_fingerprints.clear()

    er.configure(company_cfg)

    assert er._gist_id == "gist-global"
    assert er._token == "token-global"


def test_error_reporter_retries_with_next_token_after_bad_credentials(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    root_cfg = appdata / "Fretio" / "CONFIG.toml"
    root_cfg.parent.mkdir(parents=True)
    root_cfg.write_text(
        "[fretio]\n"
        'error_gist_id = "gist-old"\n'
        'error_report_token = "token-old"\n',
        encoding="utf-8",
    )
    bundled_cfg = tmp_path / "bundle" / "CONFIG.toml"
    bundled_cfg.parent.mkdir(parents=True)
    bundled_cfg.write_text(
        "[fretio]\n"
        'error_gist_id = "gist-new"\n'
        'error_report_token = "token-new"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(er, "_iter_config_candidates", lambda: [root_cfg, bundled_cfg])
    er._gist_id = ""
    er._token = ""
    er._initialized = False
    er._invalid_token_fingerprints.clear()

    requests = []

    class _ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

        def join(self, timeout=None):
            return None

    class _Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=15):
        auth = req.get_header("Authorization")
        requests.append(auth)
        if auth == "Bearer token-old":
            raise HTTPError(
                req.full_url,
                401,
                "Unauthorized",
                hdrs=None,
                fp=BytesIO(b'{"message":"Bad credentials","status":"401"}'),
            )
        return _Response()

    monkeypatch.setattr(er, "urlopen", fake_urlopen)
    monkeypatch.setattr(er.threading, "Thread", _ImmediateThread)

    er.report_error_message("falha operacional", context="cotacao_RODONAVES", wait=True)

    assert requests == ["Bearer token-old", "Bearer token-new"]
    assert er._gist_id == "gist-new"
    assert er._token == "token-new"
    assert er._token_fingerprint("token-old") in er._invalid_token_fingerprints


def test_error_reporter_appends_recent_diag_log(monkeypatch, tmp_path):
    log_path = tmp_path / "error_reporter.log"
    log_path.write_text("linha antiga\nlinha recente\n", encoding="utf-8")
    sent = {}

    class _ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(er, "_log_path", lambda: log_path)
    monkeypatch.setattr(er, "_load_config", lambda: None)
    monkeypatch.setattr(er, "_is_rate_limited", lambda fingerprint: False)
    monkeypatch.setattr(
        er,
        "_send_to_gist",
        lambda body, label="": sent.setdefault("payload", {"body": body, "label": label}) or True,
    )
    monkeypatch.setattr(er.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(er, "_gist_id", "gist")
    monkeypatch.setattr(er, "_token", "token")

    try:
        raise RuntimeError("falha remota")
    except RuntimeError:
        er.report_error(context="teste", wait=True)

    body = sent["payload"]["body"]
    assert "### Traceback" in body
    assert "### Diagnostico Local Recente" in body
    assert "linha recente" in body
