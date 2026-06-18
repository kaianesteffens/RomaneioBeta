from __future__ import annotations

import json
import os
import re
import ssl
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from license import get_machine_id, get_saved_license


DEFAULT_USAGE_API_URL = "https://fretio.api.br/api/usage/events"
_HTTP_TIMEOUT = 10
_CONFIG_SECTIONS = ("fretio", "fretebot", "romaneio")
_MAX_METADATA_DEPTH = 4
_MAX_METADATA_ITEMS = 40
_MAX_LIST_ITEMS = 20
_MAX_STRING_LENGTH = 240
_MAX_METADATA_BYTES = 4096
_CONFIG_PATH: Path | None = None

_SENSITIVE_KEYS = {
    "admin_token",
    "authorization",
    "cnpj",
    "cpf",
    "database_url",
    "danfe",
    "email",
    "exception",
    "key",
    "license",
    "license_key",
    "licenca",
    "login",
    "password",
    "passwd",
    "secret",
    "senha",
    "stack",
    "token",
    "trace",
    "traceback",
    "usuario",
}
_SENSITIVE_KEY_MARKERS = (
    "access_token",
    "api_key",
    "authorization",
    "chave",
    "client_secret",
    "cnpj",
    "cpf",
    "credential",
    "credencial",
    "database_url",
    "login",
    "password",
    "refresh_token",
    "senha",
    "secret",
    "token",
    "traceback",
)
_SENSITIVE_VALUE_MARKERS = (
    "admin_token",
    "authorization:",
    "basic ",
    "bearer ",
    "database_url",
    "ghp_",
    "github_pat_",
    "mysql://",
    "password=",
    "postgres://",
    "senha=",
    "sk-",
    "sqlite://",
    "token=",
    "traceback (most recent call last)",
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_NFE_KEY_RE = re.compile(r"\b\d{44}\b")


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
        ultima = (fretio_dir / "ultima_empresa.txt")
        try:
            empresa = ultima.read_text(encoding="utf-8").strip()
            if empresa:
                paths.append(fretio_dir / "empresas" / empresa / "CONFIG.toml")
        except Exception:
            pass
        empresas_dir = fretio_dir / "empresas"
        try:
            for candidate in sorted(empresas_dir.glob("*/CONFIG.toml")):
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


def _get_usage_api_url() -> str:
    from url_safety import require_https_url
    for env_name in ("FRETIO_USAGE_API_URL", "FRETEBOT_USAGE_API_URL"):
        url = os.environ.get(env_name, "").strip()
        if url:
            return require_https_url(url, DEFAULT_USAGE_API_URL)
    return require_https_url(_get_config_value("usage_api_url") or DEFAULT_USAGE_API_URL, DEFAULT_USAGE_API_URL)


def _get_app_version() -> str:
    candidates = [
        Path(getattr(sys, "_MEIPASS", "") or "") / "version.txt",
        Path(__file__).resolve().parent / "version.txt",
    ]
    for path in candidates:
        try:
            if path.exists():
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    return value
        except Exception:
            pass
    return ""


def _normalize_key(key: Any) -> str:
    return str(key or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalize_key(key)
    if normalized in _SENSITIVE_KEYS:
        return True
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def _looks_sensitive_value(value: str) -> bool:
    folded = value.casefold()
    if any(marker in folded for marker in _SENSITIVE_VALUE_MARKERS):
        return True
    if _EMAIL_RE.search(value) or _CNPJ_RE.search(value) or _CPF_RE.search(value):
        return True
    digits = re.sub(r"\D", "", value)
    return bool(_NFE_KEY_RE.search(digits))


def _sanitize_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    if _looks_sensitive_value(text):
        return None
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _MAX_STRING_LENGTH:
        return text[:_MAX_STRING_LENGTH]
    return text


def _sanitize_metadata_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_METADATA_DEPTH:
        return None
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        count = 0
        for key, item in value.items():
            if count >= _MAX_METADATA_ITEMS:
                sanitized["truncated"] = True
                break
            if _is_sensitive_key(key):
                continue
            clean_value = _sanitize_metadata_value(item, depth=depth + 1)
            if clean_value is None:
                continue
            sanitized[str(key)[:80]] = clean_value
            count += 1
        return sanitized
    if isinstance(value, (list, tuple, set)):
        sanitized_list: list[Any] = []
        for item in list(value)[:_MAX_LIST_ITEMS]:
            clean_value = _sanitize_metadata_value(item, depth=depth + 1)
            if clean_value is not None:
                sanitized_list.append(clean_value)
        return sanitized_list
    return _sanitize_scalar(value)


def sanitize_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    sanitized = _sanitize_metadata_value(metadata, depth=0)
    if not isinstance(sanitized, dict):
        return {}
    try:
        encoded = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) <= _MAX_METADATA_BYTES:
            return sanitized
    except Exception:
        return {}

    trimmed: dict[str, Any] = {}
    for key, value in sanitized.items():
        if key == "truncated":
            continue
        candidate = dict(trimmed)
        candidate[key] = value
        candidate["truncated"] = True
        try:
            size = len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except Exception:
            continue
        if size > _MAX_METADATA_BYTES:
            break
        trimmed = candidate
    trimmed["truncated"] = True
    return trimmed


def _coerce_int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, float):
            return int(round(value))
        text = str(value).strip()
        if not text:
            return None
        return int(text)
    except Exception:
        return None


def _build_payload(
    event_type: str,
    *,
    module: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    duration_ms: Any = None,
    value_cents: Any = None,
    metadata: Any = None,
) -> dict[str, Any]:
    return {
        "event_type": str(event_type or "").strip()[:80],
        "license_key": str(get_saved_license() or "").strip().upper(),
        "machine_id": str(get_machine_id() or "").strip(),
        "app_version": _get_app_version(),
        "module": str(module or "").strip()[:80],
        "provider": str(provider or "").strip().lower()[:80],
        "status": str(status or "").strip().lower()[:40],
        "duration_ms": _coerce_int_or_none(duration_ms),
        "value_cents": _coerce_int_or_none(value_cents),
        "metadata": sanitize_metadata(metadata),
    }


def _send_payload(payload: dict[str, Any]) -> dict[str, Any]:
    api_url = _get_usage_api_url()
    result: dict[str, Any] = {
        "sent": False,
        "status_code": None,
        "id": None,
    }
    try:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req = Request(
            api_url,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Fretio-Usage/1.0",
            },
        )
        with urlopen(req, timeout=_HTTP_TIMEOUT, context=_ssl_context()) as resp:
            result["status_code"] = int(getattr(resp, "status", 0) or 0) or None
            raw = resp.read()
        if result["status_code"] is not None:
            result["sent"] = 200 <= int(result["status_code"]) < 300
        if raw:
            try:
                data = json.loads(raw.decode("utf-8"))
                if isinstance(data, dict):
                    event_id = data.get("id") or data.get("event_id")
                    if event_id:
                        result["id"] = str(event_id)
            except Exception:
                pass
    except HTTPError as exc:
        result["status_code"] = int(getattr(exc, "code", 0) or 0) or None
    except (URLError, OSError, TimeoutError, json.JSONDecodeError):
        pass
    except Exception:
        pass
    return result


def report_usage_event(
    event_type: str,
    module: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    duration_ms: Any = None,
    value_cents: Any = None,
    metadata: Any = None,
    wait: bool = False,
) -> dict[str, Any]:
    try:
        payload = _build_payload(
            event_type,
            module=module,
            provider=provider,
            status=status,
            duration_ms=duration_ms,
            value_cents=value_cents,
            metadata=metadata,
        )
    except Exception:
        return {"sent": False, "status_code": None, "id": None}

    if wait:
        return _send_payload(payload)

    def _worker() -> None:
        _send_payload(payload)

    try:
        thread = threading.Thread(target=_worker, name="FretioUsageReporter", daemon=True)
        thread.start()
    except Exception:
        pass
    return {"sent": False, "status_code": None, "id": None, "queued": True}


def report_app_started() -> dict[str, Any]:
    return report_usage_event("app_started", module="app", status="ok")


def report_license_validated(status: str = "ok") -> dict[str, Any]:
    return report_usage_event("license_validated", module="license", status=status)


def report_remote_config_fetched(status: str = "ok") -> dict[str, Any]:
    return report_usage_event("remote_config_fetched", module="remote_config", status=status)


def report_quotation_started(metadata: Any = None) -> dict[str, Any]:
    return report_usage_event("quotation_started", module="quotation", status="started", metadata=metadata)


def report_quotation_finished(
    status: str,
    duration_ms: Any = None,
    metadata: Any = None,
) -> dict[str, Any]:
    return report_usage_event(
        "quotation_finished",
        module="quotation",
        status=status,
        duration_ms=duration_ms,
        metadata=metadata,
    )


def report_carrier_quotation_result(
    provider: str,
    status: str,
    duration_ms: Any = None,
    value_cents: Any = None,
    metadata: Any = None,
) -> dict[str, Any]:
    return report_usage_event(
        "carrier_quotation_result",
        module="quotation",
        provider=provider,
        status=status,
        duration_ms=duration_ms,
        value_cents=value_cents,
        metadata=metadata,
    )


def report_tracking_started(metadata: Any = None) -> dict[str, Any]:
    return report_usage_event("tracking_started", module="tracking", status="started", metadata=metadata)


def report_tracking_finished(
    status: str,
    duration_ms: Any = None,
    metadata: Any = None,
) -> dict[str, Any]:
    return report_usage_event(
        "tracking_finished",
        module="tracking",
        status=status,
        duration_ms=duration_ms,
        metadata=metadata,
    )


def report_nfe_imported(status: str = "ok", metadata: Any = None) -> dict[str, Any]:
    return report_usage_event("nfe_imported", module="nfe", status=status, metadata=metadata)


def report_romaneio_processed(status: str = "ok", metadata: Any = None) -> dict[str, Any]:
    return report_usage_event("romaneio_processed", module="romaneio", status=status, metadata=metadata)
