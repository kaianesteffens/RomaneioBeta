import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))

import startup


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_scrub_removes_developer_token_but_keeps_client_credentials(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    cfg = appdata / "Fretio" / "empresas" / "DARLU" / "CONFIG.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "[fretio]\n"
        'error_api_url = "https://fretio.api.br/api/errors"\n'
        'error_gist_id = "gist-123"\n'
        'error_report_token = "ghp_SECRET_DEV_TOKEN"\n'
        "\n"
        "[transportadoras.braspress]\n"
        'cnpj = "11.111.111/0001-11"\n'
        'senha = "senha_do_cliente"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))

    startup._scrub_developer_credentials_from_configs()

    text = _read(cfg)
    assert "error_report_token" not in text
    assert "ghp_SECRET_DEV_TOKEN" not in text
    assert "error_gist_id" not in text
    # Credenciais do próprio cliente devem permanecer intactas.
    assert "senha_do_cliente" in text
    assert "11.111.111/0001-11" in text
    # URL pública (não é segredo) permanece.
    assert "https://fretio.api.br/api/errors" in text


def test_scrub_cleans_legacy_fretebot_root_config(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    cfg = appdata / "FreteBot" / "CONFIG.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "[fretebot]\n"
        'error_report_token = "ghp_LEGACY"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))

    startup._scrub_developer_credentials_from_configs()

    text = _read(cfg)
    assert "ghp_LEGACY" not in text
    assert "error_report_token" not in text


def test_scrub_is_noop_without_developer_fields(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    cfg = appdata / "Fretio" / "CONFIG.toml"
    cfg.parent.mkdir(parents=True)
    original = "[fretio]\nerror_api_url = \"https://fretio.api.br/api/errors\"\n"
    cfg.write_text(original, encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))

    startup._scrub_developer_credentials_from_configs()

    assert "https://fretio.api.br/api/errors" in _read(cfg)
