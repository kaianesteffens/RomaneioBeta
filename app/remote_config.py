from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import ssl
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from license import get_machine_id, get_saved_license


DEFAULT_LICENSE_CONFIG_API_URL = "https://fretio.api.br/api/licenses/config"
_HTTP_TIMEOUT = 15
_CONFIG_SECTIONS = ("fretio", "fretebot", "romaneio")
_CACHE_LOCK = threading.Lock()
_LOGGER = logging.getLogger("remote_config")
_LAST_FETCH_STATUS = "default"


DEFAULT_REMOTE_CONFIG: dict[str, Any] = {
    "cep_origem": None,
    "fator_cubagem": None,
    "min_app_version": None,
    "force_update": False,
    "allow_cotacao": True,
    "allow_rastreio": True,
    "allow_nfe": True,
    "allow_romaneio": True,
    "carriers_enabled": {
        "braspress": True,
        "trd": True,
        "agex": True,
        "eucatur": True,
        "rodonaves": True,
        "alfa": True,
        "coopex": True,
        "translovato": True,
    },
}

_SENSITIVE_KEYS = {
    "admin_token",
    "authorization",
    "database_url",
    "password",
    "passwd",
    "senha",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "client_secret",
    "api_key",
    "license_key",
    "key",
    "transportadoras",
}
_SENSITIVE_KEY_MARKERS = (
    "credential",
    "credencial",
    "password",
    "senha",
    "secret",
    "token",
    "authorization",
)
_SENSITIVE_VALUE_MARKERS = (
    "admin_token",
    "authorization:",
    "bearer ",
    "basic ",
    "database_url",
    "ghp_",
    "github_pat_",
    "password=",
    "senha=",
    "sk-",
    "token=",
)
_NETWORK_ERRORS = (URLError, OSError, TimeoutError, json.JSONDecodeError)


def _set_last_fetch_status(status: str) -> None:
    global _LAST_FETCH_STATUS
    _LAST_FETCH_STATUS = status


def get_last_fetch_status() -> str:
    return _LAST_FETCH_STATUS


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
    appdata = os.getenv("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "Fretio" / "CONFIG.toml")
        paths.append(Path(appdata) / "FreteBot" / "CONFIG.toml")

    base = Path(getattr(sys, "_MEIPASS", "") or Path(__file__).parent)
    paths.append(base / "CONFIG.toml")
    if base != Path(__file__).parent:
        paths.append(Path(__file__).parent / "CONFIG.toml")
    return paths


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


def _get_license_config_api_url() -> str:
    for env_name in ("FRETIO_LICENSE_CONFIG_API_URL", "FRETEBOT_LICENSE_CONFIG_API_URL"):
        url = os.environ.get(env_name, "").strip()
        if url:
            return url
    return _get_config_value("license_config_api_url") or DEFAULT_LICENSE_CONFIG_API_URL


def _config_api_url_from_validate_api_url(validate_api_url: str) -> str:
    raw = str(validate_api_url or "").strip()
    if not raw:
        return ""
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(raw)
        path = (parsed.path or "").rstrip("/")
        if path.endswith("/validate"):
            path = f"{path.rsplit('/', 1)[0]}/config"
        elif path.endswith("/api/licenses"):
            path = f"{path}/config"
        elif not path:
            path = "/api/licenses/config"
        else:
            path = f"{path}/config"
        return urlunparse(parsed._replace(path=path))
    except Exception:
        return ""


def _remote_config_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        path = Path(appdata) / "Fretio"
    else:
        path = Path.home() / ".Fretio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _remote_config_cache_file() -> Path:
    return _remote_config_dir() / "remote_config.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _copy_defaults() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_REMOTE_CONFIG)


def _normalize_key(key: Any) -> str:
    return str(key or "").strip().lower().replace("-", "_").replace(" ", "_")


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalize_key(key)
    if normalized in _SENSITIVE_KEYS:
        return True
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def _looks_sensitive_value(value: str) -> bool:
    folded = value.casefold()
    return any(marker.casefold() in folded for marker in _SENSITIVE_VALUE_MARKERS)


def _sanitize_for_cache(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                continue
            sanitized[str(key)] = _sanitize_for_cache(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_cache(item) for item in value]
    if isinstance(value, str) and _looks_sensitive_value(value):
        return ""
    return value


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "sim", "yes", "on", "enabled", "habilitado"}:
            return True
        if normalized in {"0", "false", "nao", "n\u00e3o", "no", "off", "disabled", "desabilitado"}:
            return False
        return default
    return bool(value)


def _merge_with_defaults(config: Any) -> dict[str, Any]:
    effective = _copy_defaults()
    if not isinstance(config, dict):
        return effective

    for key, value in config.items():
        key_str = str(key)
        if key_str == "carriers_enabled":
            if isinstance(value, dict):
                merged_carriers = dict(effective["carriers_enabled"])
                for carrier_name, enabled in value.items():
                    normalized_name = _normalize_key(carrier_name)
                    if normalized_name:
                        merged_carriers[normalized_name] = _coerce_bool(enabled, True)
                effective["carriers_enabled"] = merged_carriers
            continue
        effective[key_str] = value
    return effective


def _default_cache() -> dict[str, Any]:
    return {
        "fetched_at": "",
        "valid": False,
        "license": {},
        "config": _copy_defaults(),
    }


def _normalize_cache_payload(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    sanitized = _sanitize_for_cache(data)
    if not isinstance(sanitized, dict):
        return None
    license_info = sanitized.get("license", {})
    if not isinstance(license_info, dict):
        license_info = {}
    return {
        "fetched_at": str(sanitized.get("fetched_at", "") or ""),
        "valid": _coerce_bool(sanitized.get("valid"), False),
        "license": license_info,
        "config": _merge_with_defaults(sanitized.get("config", {})),
    }


def _cache_payload_from_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return _default_cache()

    license_info = data.get("license", {})
    if not isinstance(license_info, dict):
        license_info = {}
    status_info = data.get("status", {})
    if not isinstance(status_info, dict):
        status_info = {}

    valid_value = data.get("valid", license_info.get("valid", status_info.get("valid", False)))
    valid = _coerce_bool(valid_value, False)

    config_info = data.get("config", {})
    if not isinstance(config_info, dict) and isinstance(license_info.get("config"), dict):
        config_info = license_info.get("config", {})

    safe_license = dict(license_info)
    safe_license.pop("config", None)
    for field in ("owner", "expires", "message", "blocked"):
        if field in data and field not in safe_license:
            safe_license[field] = data[field]
        if field in status_info and field not in safe_license:
            safe_license[field] = status_info[field]

    return {
        "fetched_at": _utc_now_iso(),
        "valid": valid,
        "license": _sanitize_for_cache(safe_license),
        "config": _merge_with_defaults(_sanitize_for_cache(config_info)),
    }


def _write_cache(cache_payload: dict[str, Any]) -> None:
    normalized = _normalize_cache_payload(cache_payload) or _default_cache()
    cache_file = _remote_config_cache_file()
    tmp_file = cache_file.with_suffix(".json.tmp")
    content = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True)
    with _CACHE_LOCK:
        tmp_file.write_text(content, encoding="utf-8")
        tmp_file.replace(cache_file)


class RemoteConfigClient:
    def __init__(self, api_url: str) -> None:
        self.api_url = str(api_url or "").strip() or DEFAULT_LICENSE_CONFIG_API_URL

    def fetch(self, key: str, machine_id: str) -> dict[str, Any]:
        payload = json.dumps(
            {
                "key": key,
                "machine_id": machine_id,
            }
        ).encode("utf-8")
        req = Request(
            self.api_url,
            data=payload,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Fretio-RemoteConfig/1.0",
            },
        )
        with urlopen(req, timeout=_HTTP_TIMEOUT, context=_ssl_context()) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}


def get_cached_remote_config() -> dict[str, Any] | None:
    cache_file = _remote_config_cache_file()
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        return _normalize_cache_payload(data)
    except Exception as exc:
        _LOGGER.warning("Falha ao ler cache de configuracao remota: %s", exc)
        return None


def _fetch_remote_config_now(
    *,
    key: str = "",
    machine_id: str = "",
    api_url: str = "",
) -> dict[str, Any]:
    key = str(key or get_saved_license() or "").strip().upper()
    if not key:
        _LOGGER.info("Configuracao remota ignorada: licenca local ausente")
        _set_last_fetch_status("default")
        return _default_cache()

    resolved_api_url = str(api_url or "").strip() or _get_license_config_api_url()
    machine_id = str(machine_id or get_machine_id()).strip()
    response = RemoteConfigClient(resolved_api_url).fetch(key, machine_id)
    cache_payload = _cache_payload_from_response(response)
    if cache_payload.get("valid") is True:
        _write_cache(cache_payload)
        _LOGGER.info("Configuracao remota da licenca atualizada")
        _set_last_fetch_status("ok")
        return cache_payload

    _LOGGER.warning("Servidor de configuracao remota retornou valid=false")
    cached = get_cached_remote_config()
    if cached:
        _LOGGER.info("Servidor de configuracao remota recusou a licenca; usando ultimo cache valido")
        _set_last_fetch_status("cache")
        return cached
    _set_last_fetch_status("error")
    return _default_cache()


def _fetch_remote_config_safely(
    *,
    key: str = "",
    machine_id: str = "",
    api_url: str = "",
) -> dict[str, Any]:
    try:
        return _fetch_remote_config_now(key=key, machine_id=machine_id, api_url=api_url)
    except _NETWORK_ERRORS as exc:
        _LOGGER.warning("Falha ao buscar configuracao remota; usando cache/defaults: %s", exc)
    except Exception as exc:
        _LOGGER.warning("Erro inesperado na configuracao remota; usando cache/defaults: %s", exc)
        _set_last_fetch_status("error")
    cached = get_cached_remote_config()
    if cached:
        _LOGGER.info("Usando cache offline da configuracao remota")
        _set_last_fetch_status("cache")
        return cached
    if get_last_fetch_status() != "error":
        _set_last_fetch_status("default")
    return _default_cache()


def fetch_remote_config(wait: bool = True) -> dict[str, Any]:
    return fetch_remote_config_for_license(wait=wait)


def fetch_remote_config_for_license(
    *,
    key: str = "",
    machine_id: str = "",
    api_url: str = "",
    validate_api_url: str = "",
    wait: bool = True,
) -> dict[str, Any]:
    resolved_api_url = str(api_url or "").strip()
    if not resolved_api_url:
        resolved_api_url = _config_api_url_from_validate_api_url(validate_api_url)
    if not resolved_api_url:
        resolved_api_url = _get_license_config_api_url()

    if wait:
        return _fetch_remote_config_safely(
            key=key,
            machine_id=machine_id,
            api_url=resolved_api_url,
        )

    thread = threading.Thread(
        target=_fetch_remote_config_safely,
        kwargs={
            "key": key,
            "machine_id": machine_id,
            "api_url": resolved_api_url,
        },
        name="FretioRemoteConfigFetch",
        daemon=True,
    )
    thread.start()
    return get_cached_remote_config() or _default_cache()


def get_effective_remote_config() -> dict[str, Any]:
    cached = get_cached_remote_config()
    if cached:
        return _merge_with_defaults(cached.get("config", {}))
    return _copy_defaults()


def is_carrier_enabled(name: str) -> bool:
    carrier_name = _normalize_key(name)
    if not carrier_name:
        return True
    config = get_effective_remote_config()
    carriers = config.get("carriers_enabled", {})
    if not isinstance(carriers, dict):
        return True
    return _coerce_bool(carriers.get(carrier_name), True)


def is_feature_allowed(name: str) -> bool:
    feature_name = _normalize_key(name)
    if not feature_name:
        return True
    if not feature_name.startswith("allow_"):
        feature_name = f"allow_{feature_name}"
    config = get_effective_remote_config()
    return _coerce_bool(config.get(feature_name), True)


def _safe_remote_cep_origem(value: Any) -> str:
    cep = re.sub(r"\D", "", str(value or ""))
    return cep if len(cep) == 8 else ""


def _safe_remote_fator_cubagem(value: Any) -> float | int | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except Exception:
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    if parsed.is_integer():
        return int(parsed)
    return parsed


def get_safe_runtime_overrides() -> dict[str, Any]:
    config = get_effective_remote_config()
    overrides: dict[str, Any] = {}
    if not isinstance(config, dict):
        return overrides

    cep_origem = _safe_remote_cep_origem(config.get("cep_origem"))
    if cep_origem:
        overrides["cep_origem"] = cep_origem

    fator_cubagem = _safe_remote_fator_cubagem(config.get("fator_cubagem"))
    if fator_cubagem is not None:
        overrides["fator_cubagem"] = fator_cubagem

    return overrides


def apply_safe_runtime_overrides(config: dict[str, Any] | None) -> dict[str, Any]:
    base: dict[str, Any] = copy.deepcopy(config) if isinstance(config, dict) else {}
    overrides = get_safe_runtime_overrides()
    if not overrides:
        return base

    if "cep_origem" in overrides:
        romaneio_cfg = base.get("romaneio", {})
        if not isinstance(romaneio_cfg, dict):
            romaneio_cfg = {}
        romaneio_cfg = dict(romaneio_cfg)
        romaneio_cfg["cep_origem"] = overrides["cep_origem"]
        base["romaneio"] = romaneio_cfg

    if "fator_cubagem" in overrides:
        fretio_cfg = base.get("fretio", {})
        if not isinstance(fretio_cfg, dict):
            fretio_cfg = {}
        fretio_cfg = dict(fretio_cfg)
        fretio_cfg["fator_cubagem"] = overrides["fator_cubagem"]
        base["fretio"] = fretio_cfg

    return base
