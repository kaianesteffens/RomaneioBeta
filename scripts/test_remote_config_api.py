#!/usr/bin/env python
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import remote_config  # noqa: E402


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
    result = remote_config.fetch_remote_config(wait=True)
    config = result.get("config", {}) if isinstance(result, dict) else {}
    license_info = result.get("license", {}) if isinstance(result, dict) else {}
    if not isinstance(config, dict):
        config = {}
    if not isinstance(license_info, dict):
        license_info = {}

    print(f"valid: {_as_bool(result.get('valid', False), False)}")
    print(f"owner: {license_info.get('owner', '')}")
    print(f"force_update: {_as_bool(config.get('force_update'), False)}")
    print(f"allow_cotacao: {_as_bool(config.get('allow_cotacao'), True)}")
    print(
        "carriers_enabled: "
        + json.dumps(config.get("carriers_enabled", {}), ensure_ascii=False, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
