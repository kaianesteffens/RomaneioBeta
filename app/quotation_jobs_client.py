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
from urllib.parse import quote
from urllib.request import Request, urlopen

from license import get_machine_id, get_saved_license

try:
    from usage_reporter import sanitize_metadata
except Exception:
    def sanitize_metadata(metadata: Any) -> dict[str, Any]:
        return metadata if isinstance(metadata, dict) else {}


DEFAULT_QUOTATION_JOBS_API_URL = "https://fretio.api.br/api/quotations/jobs"
_HTTP_TIMEOUT = 8
_CONFIG_SECTIONS = ("fretio", "fretebot", "romaneio")
_CONFIG_PATH: Path | None = None
_LOGGER = logging.getLogger("quotation_jobs_client")


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


def _get_quotation_jobs_api_url() -> str:
    for env_name in ("FRETIO_QUOTATION_JOBS_API_URL", "FRETEBOT_QUOTATION_JOBS_API_URL"):
        url = os.environ.get(env_name, "").strip()
        if url:
            return url.rstrip("/")
    return (_get_config_value("quotation_jobs_api_url") or DEFAULT_QUOTATION_JOBS_API_URL).rstrip("/")


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


def _sanitize_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    try:
        return sanitize_metadata(value)
    except Exception:
        return {}


def _sanitize_error_message(value: Any) -> str:
    if value is None:
        return ""
    try:
        sanitized = sanitize_metadata({"error_message": str(value)})
        error_message = sanitized.get("error_message", "") if isinstance(sanitized, dict) else ""
    except Exception:
        error_message = ""
    if not error_message:
        return ""
    return str(error_message).replace("\r", " ").replace("\n", " ").strip()[:240]


def _coerce_job_id(job_id: Any) -> str:
    text = str(job_id or "").strip()
    return text if text and text.lower() not in {"none", "null"} else ""


def _extract_job_id(data: Any) -> int | str | None:
    if not isinstance(data, dict):
        return None
    for key in ("job_id", "id"):
        value = data.get(key)
        if value:
            return value
    job = data.get("job")
    if isinstance(job, dict):
        for key in ("job_id", "id"):
            value = job.get(key)
            if value:
                return value
    return None


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
            "User-Agent": "Fretio-QuotationJobs/1.0",
        },
    )
    with urlopen(req, timeout=_HTTP_TIMEOUT, context=_ssl_context()) as resp:
        status_code = int(getattr(resp, "status", 0) or 0) or None
        data = _decode_response(resp.read())
    return status_code, data


def _identity_payload(app_version: str | None = None) -> dict[str, Any]:
    return {
        "license_key": str(get_saved_license() or "").strip().upper(),
        "machine_id": str(get_machine_id() or "").strip(),
        "app_version": str(app_version or _get_app_version() or "").strip(),
    }


def _create_quotation_job_now(
    source_type: str,
    payload: dict[str, Any] | None = None,
    app_version: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sent": False,
        "created": False,
        "status_code": None,
        "job_id": None,
    }
    try:
        body = _identity_payload(app_version)
        if not body["license_key"] or not body["machine_id"]:
            result["skipped"] = True
            return result
        body["source_type"] = str(source_type or "").strip().lower()[:80]
        body["payload"] = _sanitize_dict(payload or {})
        status_code, data = _request_json(_get_quotation_jobs_api_url(), method="POST", payload=body)
        result["status_code"] = status_code
        result["sent"] = status_code is not None and 200 <= int(status_code) < 300
        result["created"] = bool(result["sent"])
        result["job_id"] = _extract_job_id(data)
    except HTTPError as exc:
        result["status_code"] = int(getattr(exc, "code", 0) or 0) or None
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        _LOGGER.info("Falha ao criar job de cotacao: %s", exc)
    except Exception as exc:
        _LOGGER.info("Erro inesperado ao criar job de cotacao: %s", exc)
    return result


def _get_quotation_job_now(job_id: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sent": False,
        "status_code": None,
        "job": None,
    }
    job_id_text = _coerce_job_id(job_id)
    if not job_id_text:
        result["skipped"] = True
        return result
    try:
        url = f"{_get_quotation_jobs_api_url()}/{quote(job_id_text, safe='')}"
        status_code, data = _request_json(url, method="GET")
        result["status_code"] = status_code
        result["sent"] = status_code is not None and 200 <= int(status_code) < 300
        result["job"] = data.get("job", data) if isinstance(data, dict) else None
    except HTTPError as exc:
        result["status_code"] = int(getattr(exc, "code", 0) or 0) or None
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        _LOGGER.info("Falha ao consultar job de cotacao: %s", exc)
    except Exception as exc:
        _LOGGER.info("Erro inesperado ao consultar job de cotacao: %s", exc)
    return result


def _update_quotation_job_result_now(
    job_id: Any,
    status: str,
    result_payload: dict[str, Any] | None = None,
    error_message: Any = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sent": False,
        "updated": False,
        "status_code": None,
    }
    job_id_text = _coerce_job_id(job_id)
    if not job_id_text:
        result["skipped"] = True
        return result
    try:
        body = _identity_payload()
        if not body["license_key"] or not body["machine_id"]:
            result["skipped"] = True
            return result
        body["status"] = str(status or "").strip().lower()[:40]
        body["result"] = _sanitize_dict(result_payload or {})
        safe_error = _sanitize_error_message(error_message)
        if safe_error:
            body["error_message"] = safe_error
        url = f"{_get_quotation_jobs_api_url()}/{quote(job_id_text, safe='')}/result"
        status_code, _data = _request_json(url, method="PATCH", payload=body)
        result["status_code"] = status_code
        result["sent"] = status_code is not None and 200 <= int(status_code) < 300
        result["updated"] = bool(result["sent"])
    except HTTPError as exc:
        result["status_code"] = int(getattr(exc, "code", 0) or 0) or None
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        _LOGGER.info("Falha ao atualizar job de cotacao: %s", exc)
    except Exception as exc:
        _LOGGER.info("Erro inesperado ao atualizar job de cotacao: %s", exc)
    return result


def _run_in_background(target, *args, **kwargs) -> None:
    try:
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, name="FretioQuotationJobs", daemon=True)
        thread.start()
    except Exception:
        pass


def create_quotation_job(
    source_type: str,
    payload: dict[str, Any] | None = None,
    app_version: str | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    if wait:
        return _create_quotation_job_now(source_type, payload, app_version)
    _run_in_background(_create_quotation_job_now, source_type, payload, app_version)
    return {"sent": False, "created": False, "status_code": None, "job_id": None, "queued": True}


def get_quotation_job(job_id: Any, wait: bool = True) -> dict[str, Any]:
    if wait:
        return _get_quotation_job_now(job_id)
    _run_in_background(_get_quotation_job_now, job_id)
    return {"sent": False, "status_code": None, "job": None, "queued": True}


def update_quotation_job_result(
    job_id: Any,
    status: str,
    result: dict[str, Any] | None = None,
    error_message: Any = None,
    wait: bool = True,
) -> dict[str, Any]:
    if wait:
        return _update_quotation_job_result_now(job_id, status, result, error_message)
    _run_in_background(_update_quotation_job_result_now, job_id, status, result, error_message)
    return {"sent": False, "updated": False, "status_code": None, "queued": True}
