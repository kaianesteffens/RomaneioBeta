from __future__ import annotations

import json
import logging
import os
import ssl
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from license import get_machine_id, get_saved_license
from quotation_shadow_compare import (
    build_shadow_payload,
    compare_quotation_normalization,
    normalize_source_type,
)


DEFAULT_QUOTATION_NORMALIZATION_API_URL = "https://fretio.api.br/api/quotations/normalize"
_HTTP_TIMEOUT = 6
_CONFIG_SECTIONS = ("fretio", "fretebot", "romaneio")
_CONFIG_PATH: Path | None = None
_LOGGER = logging.getLogger("quotation_normalization_client")


def configure(config_path: str | Path | None = None) -> None:
    """Use a specific company CONFIG.toml before fallback path scanning."""
    global _CONFIG_PATH
    _CONFIG_PATH = Path(config_path) if config_path else None


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore[import-untyped]

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _load_toml_file(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8-sig")
    data: Any = None
    try:
        import tomllib  # type: ignore[import]

        data = tomllib.loads(raw)
    except Exception:
        pass
    if data is None:
        try:
            import toml  # type: ignore[import-untyped]

            data = toml.loads(raw)
        except Exception:
            pass
    if data is None:
        try:
            import tomli  # type: ignore[import-not-found]

            data = tomli.loads(raw)
        except Exception:
            pass
    return data if isinstance(data, dict) else {}


def _iter_config_paths() -> list[Path]:
    paths: list[Path] = []
    if _CONFIG_PATH is not None:
        paths.append(_CONFIG_PATH)

    appdata = os.getenv("APPDATA")
    if appdata:
        fretio_dir = Path(appdata) / "Fretio"
        paths.append(fretio_dir / "CONFIG.toml")
        try:
            empresa = (fretio_dir / "ultima_empresa.txt").read_text(encoding="utf-8").strip()
            if empresa:
                paths.append(fretio_dir / "empresas" / empresa / "CONFIG.toml")
        except Exception:
            pass
        try:
            for candidate in sorted((fretio_dir / "empresas").glob("*/CONFIG.toml")):
                paths.append(candidate)
        except Exception:
            pass
        paths.append(Path(appdata) / "FreteBot" / "CONFIG.toml")

    base = Path(getattr(sys, "_MEIPASS", "") or Path(__file__).parent)
    paths.append(base / "CONFIG.toml")
    if base != Path(__file__).parent:
        paths.append(Path(__file__).parent / "CONFIG.toml")

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).casefold()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def _get_config_value(key: str) -> str:
    try:
        for config_path in _iter_config_paths():
            if not config_path.exists():
                continue
            data = _load_toml_file(config_path)
            for section_name in _CONFIG_SECTIONS:
                section = data.get(section_name, {})
                if not isinstance(section, dict):
                    continue
                value = section.get(key, "")
                if value:
                    return str(value).strip()
    except Exception:
        pass
    return ""


def _get_quotation_normalization_api_url() -> str:
    for env_name in ("FRETIO_QUOTATION_NORMALIZATION_API_URL", "FRETEBOT_QUOTATION_NORMALIZATION_API_URL"):
        url = os.environ.get(env_name, "").strip()
        if url:
            return url.rstrip("/")
    return (
        _get_config_value("quotation_normalization_api_url")
        or DEFAULT_QUOTATION_NORMALIZATION_API_URL
    ).rstrip("/")


def _decode_response(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _request_json(
    url: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int | None, Any]:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Fretio-QuotationNormalizationShadow/1.0",
        },
    )
    with urlopen(req, timeout=_HTTP_TIMEOUT, context=_ssl_context()) as resp:
        status_code = int(getattr(resp, "status", 0) or 0) or None
        data = _decode_response(resp.read())
    return status_code, data


def _identity_payload() -> dict[str, Any]:
    return {
        "license_key": str(get_saved_license() or "").strip().upper(),
        "machine_id": str(get_machine_id() or "").strip(),
    }


def _sanitize_quotation_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    return build_shadow_payload(payload or {})


def _log_shadow_result(result: dict[str, Any]) -> None:
    try:
        if not result.get("sent"):
            return
        comparison = result.get("comparison")
        if not isinstance(comparison, dict):
            return
        if comparison.get("matched"):
            _LOGGER.info("Normalizacao remota shadow compativel com normalizacao local.")
            return
        differences = comparison.get("differences", [])
        _LOGGER.info("Normalizacao remota shadow divergente: %s", differences[:8])
    except Exception:
        pass


def _normalize_quotation_remote_shadow_now(
    source_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sent": False,
        "status_code": None,
        "normalized": None,
        "comparison": None,
    }
    try:
        body = _identity_payload()
        if not body["license_key"] or not body["machine_id"]:
            result["skipped"] = True
            return result

        safe_payload = _sanitize_quotation_payload(payload or {})
        body["source_type"] = normalize_source_type(source_type)
        body["payload"] = safe_payload

        status_code, data = _request_json(
            _get_quotation_normalization_api_url(),
            method="POST",
            payload=body,
        )
        result["status_code"] = status_code
        result["sent"] = status_code is not None and 200 <= int(status_code) < 300
        result["normalized"] = data if isinstance(data, dict) else None
        if isinstance(data, dict):
            result["comparison"] = compare_quotation_normalization(safe_payload, data)
        _log_shadow_result(result)
    except HTTPError as exc:
        result["status_code"] = int(getattr(exc, "code", 0) or 0) or None
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        _LOGGER.info("Falha na normalizacao remota shadow: %s", exc)
    except Exception as exc:
        _LOGGER.info("Erro inesperado na normalizacao remota shadow: %s", exc)
    return result


def _run_in_background(target, *args, **kwargs) -> None:
    try:
        thread = threading.Thread(
            target=target,
            args=args,
            kwargs=kwargs,
            name="FretioQuotationNormalizationShadow",
            daemon=True,
        )
        thread.start()
    except Exception:
        pass


def normalize_quotation_remote_shadow(
    source_type: str,
    payload: dict[str, Any] | None = None,
    *,
    wait: bool = False,
) -> dict[str, Any]:
    """Call the server normalizer in shadow mode without changing local quotation data."""
    if wait:
        return _normalize_quotation_remote_shadow_now(source_type, payload)
    _run_in_background(_normalize_quotation_remote_shadow_now, source_type, payload)
    return {
        "sent": False,
        "status_code": None,
        "normalized": None,
        "comparison": None,
        "queued": True,
    }
