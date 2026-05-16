from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR / "app"
ERROR_API_URL = "http://b3wjxbvlglanxcdwfoft4x0b.2.24.102.36.sslip.io/api/errors"

sys.path.insert(0, str(APP_DIR))

import error_reporter  # noqa: E402


def _iter_config_toml_candidates():
    appdata = os.getenv("APPDATA", "").strip()

    yield APP_DIR / "CONFIG.toml"

    if not appdata:
        return

    appdata_dir = Path(appdata)
    for app_name in ("Fretio", "FreteBot"):
        base_dir = appdata_dir / app_name
        yield base_dir / "CONFIG.toml"

        empresas_dir = base_dir / "empresas"
        if not empresas_dir.exists():
            continue
        try:
            for empresa_dir in sorted(empresas_dir.iterdir()):
                if empresa_dir.is_dir():
                    yield empresa_dir / "CONFIG.toml"
        except OSError:
            continue


def _configure_error_reporter() -> Path | None:
    os.environ.setdefault("FRETIO_ERROR_API_URL", ERROR_API_URL)

    for config_path in _iter_config_toml_candidates():
        if config_path.exists():
            error_reporter.configure(config_path)
            return config_path

    error_reporter.reload_config()
    return None


def main() -> None:
    config_path = _configure_error_reporter()
    if config_path is not None:
        print(f"CONFIG.toml usado: {config_path}")
    else:
        print("CONFIG.toml nao encontrado; usando endpoint local de teste.")

    error_reporter.report_error_message(
        "Teste manual de envio para API de erros",
        context="manual_error_api_test",
        wait=True,
    )
    print("Teste manual de envio para API de erros disparado.")


if __name__ == "__main__":
    main()
