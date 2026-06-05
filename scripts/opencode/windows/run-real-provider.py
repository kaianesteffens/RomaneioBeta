from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any

import tomllib


def _project_dir() -> Path:
    env_value = os.environ.get("FRETIO_PROJECT_DIR", "").strip()
    if env_value:
        return Path(env_value)
    return Path(__file__).resolve().parents[3]


PROJECT_DIR = _project_dir()
APP_DIR = PROJECT_DIR / "app"
FRETIO_SRC_DIR = APP_DIR / "fretio" / "src"

for candidate in (str(APP_DIR), str(FRETIO_SRC_DIR)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

os.chdir(PROJECT_DIR)

from fretio.config_manager import ConfigManager
from fretio.logging_conf import setup_logging
from fretio.providers.factory import ProviderFactory
from fretio.quotation_contract import quote_request_from_legacy_kwargs
from company_config import _empresa_config_path, _ler_ultima_empresa, _listar_empresas


SENSITIVE_KEY_FRAGMENTS = (
    "senha",
    "password",
    "secret",
    "token",
    "cookie",
    "authorization",
    "cpf",
    "cnpj",
)


def _mask_doc(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) <= 6:
        return "***" if digits else ""
    return f"{digits[:4]}***{digits[-2:]}"


def _sanitize_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS):
        if isinstance(value, str):
            return _mask_doc(value) or "***"
        return "***"
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) in {11, 14}:
            return _mask_doc(value)
    if isinstance(value, dict):
        return {str(inner_key): _sanitize_value(str(inner_key), inner_value) for inner_key, inner_value in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(key, item) for item in value]
    return value


def _sanitize_mapping(data: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _sanitize_value(str(key), value) for key, value in data.items()}


def _sanitize_text(text: str) -> str:
    text = re.sub(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b", "***", text)
    text = re.sub(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", "***", text)
    text = re.sub(r"\b\d{14}\b", "***", text)
    text = re.sub(r"\b\d{11}\b", "***", text)
    return text


def _quiet_console_logging() -> None:
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            root_logger.removeHandler(handler)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _load_inputs() -> tuple[dict[str, Any], Path]:
    default_path = PROJECT_DIR / "scripts" / "opencode" / "windows" / "real-provider-inputs.json"
    override_paths: list[Path] = []
    env_override = os.environ.get("FRETIO_VM_TEST_INPUTS", "").strip()
    if env_override:
        override_paths.append(Path(env_override))
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        override_paths.append(Path(appdata) / "Fretio" / "vm-test-inputs.json")

    merged = _load_json(default_path)
    source_path = default_path
    for candidate in override_paths:
        if not candidate.exists():
            continue
        override_data = _load_json(candidate)
        for key, value in override_data.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        source_path = candidate
    return merged, source_path


def _provider_payload(all_inputs: dict[str, Any], provider_name: str) -> dict[str, Any]:
    defaults = dict(all_inputs.get("defaults") or {})
    specific = dict(all_inputs.get(provider_name) or {})
    payload = {**defaults, **specific}
    cubagens = payload.get("cubagens")
    if not isinstance(cubagens, list) or not cubagens:
        raise ValueError("Arquivo de inputs sem cubagens válidas")
    return payload


def _resolve_empresa() -> str:
    explicit = os.environ.get("FRETIO_EMPRESA", "").strip()
    if explicit:
        return explicit
    ultima = _ler_ultima_empresa().strip()
    if ultima:
        return ultima
    empresas = _listar_empresas()
    if empresas:
        return empresas[0]
    return "default"


def _load_company_config(empresa: str) -> tuple[dict[str, Any] | None, Path | None]:
    candidates = [
        _empresa_config_path(empresa),
        PROJECT_DIR / "Fretio_data" / "empresas" / empresa / "CONFIG.toml",
    ]
    for config_path in candidates:
        if not config_path.exists():
            continue
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
        return (data if isinstance(data, dict) else None), config_path
    return None, None


def _response_payload(response: Any) -> dict[str, Any]:
    return {
        "provider": getattr(response, "provider", ""),
        "status": getattr(response, "status", ""),
        "valor_frete": getattr(response, "valor_frete", None),
        "prazo_dias": getattr(response, "prazo_dias", None),
        "detalhes": getattr(response, "detalhes", None),
        "duration_ms": getattr(response, "duration_ms", None),
        "stage": getattr(response, "stage", None),
        "error_code": getattr(response, "error_code", None),
        "raw": getattr(response, "raw", None),
    }


def _provider_timeout_s(provider_name: str, payload: dict[str, Any]) -> float:
    env_value = os.environ.get("FRETIO_VM_PROVIDER_TIMEOUT_S", "").strip()
    raw_value = env_value or str(payload.get("timeout_s", "") or "")
    if raw_value:
        try:
            return max(30.0, float(raw_value))
        except Exception:
            pass
    return 150.0 if provider_name == "rodonaves" else 120.0


def _tail_fretio_log() -> str:
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return "APPDATA não definido; sem fretio.log."
    log_path = Path(appdata) / "Fretio" / "fretio.log"
    if not log_path.exists():
        return f"fretio.log não encontrado em {log_path}"
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        return f"Falha lendo fretio.log: {exc}"
    return _sanitize_text("\n".join(lines[-200:]))


async def _run(provider_name: str) -> int:
    setup_logging()
    _quiet_console_logging()

    result_dir = Path(os.environ.get("FRETIO_VM_RESULT_DIR", "").strip() or ".")
    result_dir.mkdir(parents=True, exist_ok=True)
    log_path = result_dir / f"{provider_name}-real.log"
    result_path = result_dir / f"{provider_name}-result.json"
    fretio_tail_path = result_dir / f"{provider_name}-fretio-tail.log"

    log_handle = log_path.open("w", encoding="utf-8")

    def log(message: str = "") -> None:
        print(message, flush=True)
        log_handle.write(f"{message}\n")
        log_handle.flush()

    provider = None
    response = None
    try:
        empresa = _resolve_empresa()
        inputs_data, inputs_source = _load_inputs()
        payload = _provider_payload(inputs_data, provider_name)
        provider_overrides = {}
        if provider_name == "rodonaves":
            provider_overrides["headless"] = bool(payload.get("headless", False))
        elif "headless" in payload:
            provider_overrides["headless"] = bool(payload.get("headless"))

        config, config_path = _load_company_config(empresa)
        if config is None:
            config_manager = ConfigManager.get_instance(empresa)
            config = config_manager.load_config()
            config_path = config_manager.get_loaded_path()
        factory = ProviderFactory(config=config)
        provider_config = factory.get_provider_config(provider_name)
        provider = factory.create(provider_name, ignore_disabled=True, **provider_overrides)

        log(f"Provider: {provider_name}")
        log(f"Empresa: {empresa}")
        log(f"Config: {config_path if config_path else 'fallback'}")
        log(f"Inputs: {inputs_source}")
        log(f"ResultDir: {result_dir}")
        log(f"ProviderConfig: {json.dumps(_sanitize_mapping(provider_config), ensure_ascii=False)}")
        log(f"Payload: {json.dumps(_sanitize_mapping(payload), ensure_ascii=False)}")

        if provider is None:
            raise RuntimeError("Provider indisponível ou configuração mínima incompleta")

        request = quote_request_from_legacy_kwargs(
            payload,
            uf_destino=str(payload.get("uf_destino", "") or ""),
            cnpj_destinatario=str(payload.get("cnpj_destinatario", "") or ""),
        )

        timeout_s = _provider_timeout_s(provider_name, payload)
        log(f"TimeoutS: {timeout_s:.0f}")
        response = await asyncio.wait_for(provider.cotar(request), timeout=timeout_s)
        response_data = _response_payload(response)
        last_error = getattr(provider, "last_error", None)
        result_data = {
            "provider": provider_name,
            "config_path": str(config_path) if config_path else "fallback",
            "inputs_source": str(inputs_source),
            "payload": _sanitize_mapping(payload),
            "provider_config": _sanitize_mapping(provider_config),
            "response": response_data,
            "last_error": last_error,
        }
        result_path.write_text(json.dumps(result_data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        fretio_tail_path.write_text(_tail_fretio_log(), encoding="utf-8")

        log(f"Status: {response_data['status']}")
        log(f"Stage: {response_data['stage']}")
        log(f"DurationMs: {response_data['duration_ms']}")
        log(f"ErrorCode: {response_data['error_code']}")
        log(f"Detalhes: {response_data['detalhes']}")
        log(f"LastError: {last_error}")
        log(f"ResultJson: {result_path}")
        log(f"FretioTail: {fretio_tail_path}")

        return 0 if response_data["status"] in {"ok", "sem_cotacao", "nao_atendido"} else 1
    except asyncio.TimeoutError:
        stage = getattr(provider, "_passo_atual", None) if provider is not None else None
        last_error = getattr(provider, "last_error", None) if provider is not None else None
        error_message = f"Timeout no teste real de {provider_name}"
        result_data = {
            "provider": provider_name,
            "status": "timeout",
            "stage": stage,
            "last_error": last_error,
            "error": error_message,
        }
        result_path.write_text(json.dumps(result_data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        fretio_tail_path.write_text(_tail_fretio_log(), encoding="utf-8")
        log(f"ERRO: {error_message}")
        log(f"Stage: {stage}")
        log(f"LastError: {last_error}")
        log(f"ResultJson: {result_path}")
        log(f"FretioTail: {fretio_tail_path}")
        return 124
    except Exception as exc:
        fretio_tail_path.write_text(_tail_fretio_log(), encoding="utf-8")
        error_payload = {
            "provider": provider_name,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        result_path.write_text(json.dumps(error_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"ERRO: {exc}")
        log(traceback.format_exc().rstrip())
        log(f"ResultJson: {result_path}")
        log(f"FretioTail: {fretio_tail_path}")
        return 1
    finally:
        if provider is not None:
            cleanup = getattr(provider, "cleanup", None)
            if callable(cleanup):
                try:
                    await asyncio.wait_for(cleanup(), timeout=20.0)
                except Exception as cleanup_exc:
                    log(f"CleanupError: {cleanup_exc}")
        log_handle.close()


def main() -> int:
    if len(sys.argv) != 2:
        print("Uso: python scripts/opencode/windows/run-real-provider.py <rodonaves|translovato>", flush=True)
        return 2
    provider_name = sys.argv[1].strip().lower()
    if provider_name not in {"rodonaves", "translovato"}:
        print(f"Provider inválido: {provider_name}", flush=True)
        return 2
    return asyncio.run(_run(provider_name))


if __name__ == "__main__":
    raise SystemExit(main())
