from __future__ import annotations

import sys
from pathlib import Path

import toml  # type: ignore[import-untyped]

DEFAULT_GITHUB_REPO = "kaianesteffens/RomaneioBeta-releases"
DEFAULT_LICENSE_API_URL = "http://b3wjxbvlglanxcdwfoft4x0b.2.24.102.36.sslip.io/api/licenses/validate"
DEFAULT_LICENSE_URL = "https://gist.githubusercontent.com/kaianesteffens/4a327b33711420ab88f20806e528f906/raw/licenses.json"
DEFAULT_ERROR_API_URL = "http://b3wjxbvlglanxcdwfoft4x0b.2.24.102.36.sslip.io/api/errors"


def _ensure_sections(data: dict) -> None:
    fretio = data.get("fretio")
    if not isinstance(fretio, dict):
        legacy = data.get("fretebot")
        fretio = dict(legacy) if isinstance(legacy, dict) else {}
        data["fretio"] = fretio

    fretebot = data.get("fretebot")
    if not isinstance(fretebot, dict):
        fretebot = {}
        data["fretebot"] = fretebot

    required = {
        "github_repo": DEFAULT_GITHUB_REPO,
        "license_api_url": DEFAULT_LICENSE_API_URL,
        "license_url": DEFAULT_LICENSE_URL,
        "error_api_url": DEFAULT_ERROR_API_URL,
    }
    for key, default in required.items():
        fretio_value = str(fretio.get(key, "") or "").strip()
        fretebot_value = str(fretebot.get(key, "") or "").strip()
        final_value = fretio_value or fretebot_value or default
        fretio[key] = final_value
        fretebot[key] = final_value


def main() -> int:
    if len(sys.argv) != 2:
        print("uso: normalize_embedded_config.py <CONFIG.toml>", file=sys.stderr)
        return 2

    config_path = Path(sys.argv[1])
    if not config_path.exists():
        print(f"arquivo inexistente: {config_path}", file=sys.stderr)
        return 1

    raw = config_path.read_text(encoding="utf-8-sig")
    data = toml.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        data = {}

    _ensure_sections(data)
    config_path.write_text(toml.dumps(data), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
