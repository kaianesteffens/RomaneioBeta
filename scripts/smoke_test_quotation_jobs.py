#!/usr/bin/env python
from __future__ import annotations

import json
import os
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


DEFAULT_BASE_URL = "https://fretio.api.br"
ADMIN_LICENSE_ENDPOINTS = (
    "/api/admin/licenses",
    "/api/admin/licenses?limit=500",
    "/api/licenses/admin",
)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore[import-untyped]

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _base_url() -> str:
    return (
        os.getenv("BASE_URL", "").strip()
        or os.getenv("FRETIO_API_BASE_URL", "").strip()
        or DEFAULT_BASE_URL
    ).rstrip("/")


def _api_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    route = "/" + path.lstrip("/")
    if base.endswith("/api") and route.startswith("/api/"):
        route = route[4:]
    return base + route


def _headers(admin_token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Fretio-QuotationJobs-Smoke/1.0",
    }
    if admin_token:
        headers["Authorization"] = f"Bearer {admin_token}"
        headers["X-Admin-Token"] = admin_token
    return headers


def _request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    admin_token: str | None = None,
) -> tuple[int | None, Any]:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = Request(url, data=data, method=method, headers=_headers(admin_token))
    with urlopen(req, timeout=20, context=_ssl_context()) as resp:
        status_code = int(getattr(resp, "status", 0) or 0) or None
        raw = resp.read()
    if not raw:
        return status_code, {}
    try:
        return status_code, json.loads(raw.decode("utf-8"))
    except Exception:
        return status_code, {}


def _iter_license_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("licenses", "items", "data", "rows", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            rows = []
            for license_key, item in value.items():
                if isinstance(item, dict):
                    row = dict(item)
                    row.setdefault("license_key", license_key)
                    rows.append(row)
            return rows
    return [data] if data else []


def _license_key(item: dict[str, Any]) -> str:
    for key in ("license_key", "key", "licenseKey", "id"):
        value = str(item.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _is_active_license(item: dict[str, Any]) -> bool:
    if item.get("active") is False:
        return False
    status = str(item.get("status", "") or "").strip().casefold()
    if status and status not in {"active", "ativa", "ok", "valid", "valida", "válida"}:
        return False
    if item.get("blocked") is True:
        return False
    return True


def _machine_id(item: dict[str, Any]) -> str:
    for key in ("machine_id", "machineId"):
        value = str(item.get(key, "") or "").strip()
        if value:
            return value
    for key in ("machines", "machine_ids", "machineIds"):
        value = item.get(key)
        if isinstance(value, list):
            for machine in value:
                if isinstance(machine, dict):
                    candidate = str(machine.get("machine_id") or machine.get("id") or "").strip()
                else:
                    candidate = str(machine or "").strip()
                if candidate:
                    return candidate
    machine = item.get("machine")
    if isinstance(machine, dict):
        return str(machine.get("machine_id") or machine.get("id") or "").strip()
    return ""


def _find_active_license_with_machine(base_url: str, admin_token: str) -> tuple[str, str] | None:
    for endpoint in ADMIN_LICENSE_ENDPOINTS:
        try:
            status_code, data = _request_json(
                "GET",
                _api_url(base_url, endpoint),
                admin_token=admin_token,
            )
        except (HTTPError, URLError, OSError, TimeoutError):
            continue
        if status_code is None or not (200 <= status_code < 300):
            continue
        for item in _iter_license_items(data):
            key = _license_key(item)
            machine_id = _machine_id(item)
            if key and machine_id and _is_active_license(item):
                return key, machine_id
    return None


def _extract_job_id(data: Any) -> Any:
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


def _job_status(data: Any) -> str:
    if isinstance(data, dict):
        job = data.get("job")
        if isinstance(job, dict):
            return str(job.get("status", "") or "")
        return str(data.get("status", "") or "")
    return ""


def main() -> int:
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if not admin_token:
        print("ADMIN_TOKEN ausente para smoke test.")
        return 2

    base_url = _base_url()
    identity = _find_active_license_with_machine(base_url, admin_token)
    if identity is None:
        print("Nenhuma licença ativa com máquina vinculada encontrada para smoke test.")
        return 1

    license_key, machine_id = identity
    created = False
    read = False
    updated = False
    final_status = "error"
    job_id: Any = None

    try:
        status_code, created_data = _request_json(
            "POST",
            _api_url(base_url, "/api/quotations/jobs"),
            payload={
                "license_key": license_key,
                "machine_id": machine_id,
                "app_version": "smoke-test",
                "source_type": "smoke_test",
                "payload": {"modo": "smoke_test"},
            },
        )
        created = status_code is not None and 200 <= status_code < 300
        job_id = _extract_job_id(created_data)

        if job_id:
            status_code, read_data = _request_json(
                "GET",
                _api_url(base_url, f"/api/quotations/jobs/{job_id}"),
            )
            read = status_code is not None and 200 <= status_code < 300

            status_code, _updated_data = _request_json(
                "PATCH",
                _api_url(base_url, f"/api/quotations/jobs/{job_id}/result"),
                payload={
                    "license_key": license_key,
                    "machine_id": machine_id,
                    "app_version": "smoke-test",
                    "status": "finished",
                    "result": {
                        "summary": {
                            "status": "ok",
                            "total_providers": 0,
                            "success_count": 0,
                            "error_count": 0,
                            "disabled_count": 0,
                        },
                        "transportadoras": [],
                    },
                },
            )
            updated = status_code is not None and 200 <= status_code < 300

            status_code, final_data = _request_json(
                "GET",
                _api_url(base_url, f"/api/quotations/jobs/{job_id}"),
            )
            if status_code is not None and 200 <= status_code < 300:
                final_status = (_job_status(final_data) or ("finished" if updated else "error")).strip().lower()
    except (HTTPError, URLError, OSError, TimeoutError):
        pass

    print(f"created: {str(created).lower()}")
    print(f"job_id: {job_id if job_id is not None else 0}")
    print(f"read: {str(read).lower()}")
    print(f"updated: {str(updated).lower()}")
    print(f"final_status: {final_status if final_status in {'finished', 'error'} else 'error'}")
    return 0 if created and read and updated and final_status == "finished" else 1


if __name__ == "__main__":
    raise SystemExit(main())
