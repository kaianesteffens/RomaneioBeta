import sys
from pathlib import Path
from io import BytesIO
from urllib.error import HTTPError
import zipfile


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "installer"))

import error_reporter as er
import launcher
import updater
import validate_update_zip


def _make_zip(path: Path, entries: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return path


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


def test_version_parsing_accepts_v_prefix_and_suffix():
    assert updater._parse_version("v1.22-beta") == (1, 22)
    assert launcher._parse_ver("V2.0.1") == (2, 0, 1)
    assert updater._parse_version("sem-versao") == (0,)


def test_update_zip_validator_accepts_expected_root_format(tmp_path):
    zip_path = _make_zip(
        tmp_path / "Fretio-Update-1.0.zip",
        {
            "Fretio.exe": "",
            "version.txt": "1.0",
            "_internal/version.txt": "1.0",
            "_internal/lib.dll": "",
        },
    )

    validate_update_zip.validate(zip_path)


def test_launcher_safe_extract_supports_single_root_folder(tmp_path):
    zip_path = _make_zip(
        tmp_path / "update.zip",
        {
            "Fretio/Fretio.exe": "",
            "Fretio/_internal/version.txt": "1.0",
        },
    )
    app_dir = tmp_path / "app"

    launcher._safe_extract_zip_to_app(zip_path, app_dir)

    assert (app_dir / "Fretio.exe").exists()
    assert (app_dir / "_internal" / "version.txt").read_text(encoding="utf-8") == "1.0"


def test_launcher_safe_extract_rejects_path_traversal(tmp_path):
    zip_path = _make_zip(
        tmp_path / "bad.zip",
        {
            "Fretio.exe": "",
            "_internal/version.txt": "1.0",
            "../evil.txt": "x",
        },
    )

    try:
        launcher._safe_extract_zip_to_app(zip_path, tmp_path / "app")
    except ValueError as exc:
        assert "caminho inseguro" in str(exc) or "Path traversal" in str(exc)
    else:
        raise AssertionError("ZIP com path traversal deveria falhar")


def test_updater_rejects_incomplete_zip_before_bat(tmp_path):
    zip_path = _make_zip(
        tmp_path / "bad.zip",
        {
            "version.txt": "1.0",
            "_internal/version.txt": "1.0",
        },
    )
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()

    try:
        updater._safe_extract_update_zip(zip_path, extract_dir)
    except ValueError as exc:
        assert "Fretio.exe/FreteBot.exe" in str(exc)
    else:
        raise AssertionError("ZIP sem executável deveria falhar")


def test_apply_update_does_not_create_bat_for_incomplete_zip(monkeypatch, tmp_path):
    update_dir = tmp_path / "update"
    bad_entries = {
        "version.txt": "2.0",
        "_internal/version.txt": "2.0",
    }

    def fake_download(_url, dest, _total_size=0, callback=None):
        _make_zip(dest, bad_entries)

    monkeypatch.setattr(updater, "_license_dir_update", lambda: update_dir)
    monkeypatch.setattr(updater, "_download_with_progress", fake_download)

    info = updater.UpdateInfo(
        tag="v2.0",
        version="2.0",
        download_url="https://example.test/update.zip",
        asset_name="update.zip",
        asset_size=10,
        release_notes="",
        html_url="",
        source_repo="owner/repo",
    )

    assert updater.apply_update(info) is False
    assert not (update_dir / "_apply_update.bat").exists()
    assert not (update_dir / "_pending_update").exists()


def test_launcher_resolves_valid_legacy_executable(monkeypatch, tmp_path):
    app_dir = tmp_path / "Romaneio Beta"
    app_dir.mkdir()
    (app_dir / "FreteBot.exe").write_text("", encoding="utf-8")
    (app_dir / "version.txt").write_text("1.2", encoding="utf-8")
    monkeypatch.setattr(launcher, "APP_DIR_CANDIDATES", (app_dir,))

    assert launcher._resolve_app_dir() == app_dir
    assert launcher._resolve_app_exe(app_dir) == app_dir / "FreteBot.exe"


def test_launcher_opens_valid_local_app_when_github_is_offline(monkeypatch, tmp_path):
    app_dir = tmp_path / "Fretio"
    app_dir.mkdir()
    exe_path = app_dir / "Fretio.exe"
    exe_path.write_text("", encoding="utf-8")
    (app_dir / "version.txt").write_text("1.2", encoding="utf-8")
    launched = []

    monkeypatch.setattr(launcher, "APP_DIR_CANDIDATES", (app_dir,))
    monkeypatch.setattr(launcher, "_resolve_latest_release", lambda: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(launcher, "_launch_app", lambda app_exe: launched.append(app_exe))
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)

    launcher._worker(None)

    assert launched == [exe_path]


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


def test_normalize_embedded_config_backfills_license_api_url(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setitem(sys.modules, "toml", SimpleNamespace(loads=lambda raw: {}, dumps=lambda data: ""))
    sys.modules.pop("normalize_embedded_config", None)
    import normalize_embedded_config

    data = {"fretio": {}}
    normalize_embedded_config._ensure_sections(data)

    assert data["fretio"]["license_api_url"] == normalize_embedded_config.DEFAULT_LICENSE_API_URL
    assert data["fretebot"]["license_api_url"] == normalize_embedded_config.DEFAULT_LICENSE_API_URL
