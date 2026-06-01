import sys
from pathlib import Path


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))

import company_config as cc


def _load_toml(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8-sig")
    try:
        import tomllib
        return tomllib.loads(raw)
    except ImportError:
        import toml
        return toml.loads(raw)


def test_company_config_creates_and_lists_empty_company_config(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    cc._criar_config_empresa_vazia("DARLU")

    config_path = cc._empresa_config_path("DARLU")
    data = _load_toml(config_path)
    assert cc._listar_empresas() == ["DARLU"]
    assert data["fretio"]["fator_cubagem"] == 6000
    assert data["fretio"]["github_repo"] == cc._DEFAULT_GITHUB_REPO
    assert data["fretio"]["license_api_url"] == cc._DEFAULT_LICENSE_API_URL
    assert data["fretio"]["license_config_api_url"] == cc._DEFAULT_LICENSE_CONFIG_API_URL
    assert data["fretio"]["license_url"] == cc._DEFAULT_LICENSE_URL
    assert data["fretio"]["error_api_url"] == cc._DEFAULT_ERROR_API_URL
    assert data["fretio"]["usage_api_url"] == cc._DEFAULT_USAGE_API_URL
    assert data["fretio"]["quotation_jobs_api_url"] == cc._DEFAULT_QUOTATION_JOBS_API_URL
    assert data["fretio"]["quotation_normalization_api_url"] == cc._DEFAULT_QUOTATION_NORMALIZATION_API_URL
    assert data["romaneio"]["cep_origem"] == ""
    assert data["romaneio"]["cnpj_pagador_padrao"] == ""
    assert data["transportadoras"]["braspress"]["habilitado"] is False
    assert data["transportadoras"]["rodonaves"]["ufs_atendidas"] == cc.TODAS_UFS


def test_company_config_last_company_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    cc._salvar_ultima_empresa("DARLU")

    assert cc._ler_ultima_empresa() == "DARLU"


def test_company_config_migrates_existing_root_config(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    root_config = appdata / "Fretio" / "CONFIG.toml"
    root_config.parent.mkdir(parents=True)
    root_config.write_text(
        "[romaneio]\n"
        'cep_origem = "99740000"\n'
        "\n"
        "[fretebot]\n"
        'license_api_url = "https://licenses.example.test/validate"\n'
        'license_config_api_url = "https://licenses.example.test/config"\n'
        'license_url = "https://example.test/licenses.json"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))

    cc._migrar_config_se_necessario()

    migrated = cc._empresa_config_path("darlu")
    data = _load_toml(migrated)
    assert migrated.exists()
    assert cc._listar_empresas() == ["darlu"]
    assert cc._ler_ultima_empresa() == "darlu"
    assert data["romaneio"]["cep_origem"] == "99740000"
    assert data["romaneio"]["cnpj_pagador_padrao"] == ""
    assert data["fretio"]["github_repo"] == cc._DEFAULT_GITHUB_REPO
    assert data["fretio"]["license_api_url"] == "https://licenses.example.test/validate"
    assert data["fretio"]["license_config_api_url"] == "https://licenses.example.test/config"
    assert data["fretio"]["license_url"] == "https://example.test/licenses.json"
    assert data["fretio"]["error_api_url"] == cc._DEFAULT_ERROR_API_URL
    assert data["fretio"]["usage_api_url"] == cc._DEFAULT_USAGE_API_URL
    assert data["fretio"]["quotation_jobs_api_url"] == cc._DEFAULT_QUOTATION_JOBS_API_URL
    assert data["fretio"]["quotation_normalization_api_url"] == cc._DEFAULT_QUOTATION_NORMALIZATION_API_URL


def test_company_config_migrates_root_config_without_license_api_url(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    root_config = appdata / "Fretio" / "CONFIG.toml"
    root_config.parent.mkdir(parents=True)
    root_config.write_text(
        "[fretio]\n"
        'license_url = "https://example.test/licenses.json"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))

    cc._migrar_config_se_necessario()

    data = _load_toml(cc._empresa_config_path("darlu"))
    assert data["fretio"]["license_api_url"] == cc._DEFAULT_LICENSE_API_URL
    assert data["fretio"]["license_config_api_url"] == cc._DEFAULT_LICENSE_CONFIG_API_URL
    assert data["fretio"]["license_url"] == "https://example.test/licenses.json"
    assert data["fretio"]["error_api_url"] == cc._DEFAULT_ERROR_API_URL
    assert data["fretio"]["usage_api_url"] == cc._DEFAULT_USAGE_API_URL
    assert data["fretio"]["quotation_jobs_api_url"] == cc._DEFAULT_QUOTATION_JOBS_API_URL
    assert data["fretio"]["quotation_normalization_api_url"] == cc._DEFAULT_QUOTATION_NORMALIZATION_API_URL


def test_company_config_rename_sanitizes_folder_and_updates_last_company(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    cc._criar_config_empresa_vazia("acme")
    cc._salvar_ultima_empresa("acme")

    assert cc._renomear_pasta_empresa("acme", "ACME/RS") is True

    assert not (cc._empresas_dir() / "acme").exists()
    assert (cc._empresas_dir() / "ACME_RS" / "CONFIG.toml").exists()
    assert cc._ler_ultima_empresa() == "ACME_RS"


def test_company_config_garante_defaults_empresa_sem_apagar_dados():
    data = {
        "romaneio": {"cep_origem": "99740000"},
        "transportadoras": {"coopex": {"usuario": "mantem"}},
    }

    changed = cc._garantir_defaults_empresa(data)

    assert changed is True
    assert data["romaneio"]["cep_origem"] == "99740000"
    assert data["romaneio"]["cnpj_pagador_padrao"] == ""
    assert data["transportadoras"]["coopex"]["usuario"] == "mantem"
