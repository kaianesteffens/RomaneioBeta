#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from remote_config import get_effective_remote_config  # noqa: E402
from remote_permissions import KNOWN_CARRIERS  # noqa: E402


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "sim", "yes", "on"}:
            return True
        if normalized in {"0", "false", "nao", "n\u00e3o", "no", "off"}:
            return False
        return default
    return bool(value)


def main() -> int:
    config = get_effective_remote_config()
    carriers = config.get("carriers_enabled", {}) if isinstance(config, dict) else {}
    if not isinstance(carriers, dict):
        carriers = {}

    enabled = [name for name in KNOWN_CARRIERS if _as_bool(carriers.get(name), True)]
    disabled = [name for name in KNOWN_CARRIERS if not _as_bool(carriers.get(name), True)]

    print(f"allow_cotacao: {_as_bool(config.get('allow_cotacao'), True)}")
    print(f"allow_rastreio: {_as_bool(config.get('allow_rastreio'), True)}")
    print("transportadoras_habilitadas: " + ", ".join(enabled))
    print("transportadoras_desabilitadas: " + ", ".join(disabled))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
