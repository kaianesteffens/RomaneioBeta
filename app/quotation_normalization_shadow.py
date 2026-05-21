from __future__ import annotations

import math
import os
import re
import threading
from typing import Any, Callable


_TRUTHY = {"1", "true", "sim", "yes", "on"}
_FALSEY = {"0", "false", "nao", "não", "no", "off"}
_CANONICAL_KEYS = (
    "cep_origem",
    "cep_destino",
    "uf_destino",
    "volumes",
    "peso_total_kg",
    "valor_nf",
    "cubagem_m3",
    "medidas",
)
_FLOAT_TOLERANCE = 0.001


def _digits(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _cep_or_none(value: Any) -> str | None:
    digits = _digits(value)
    return digits if len(digits) == 8 else None


def _truthy(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if text in _TRUTHY:
        return True
    if text in _FALSEY:
        return False
    return None


def _fretio_config(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    section = config.get("fretio")
    if isinstance(section, dict):
        return section
    section = config.get("Fretio")
    return section if isinstance(section, dict) else {}


def is_shadow_enabled(config: dict[str, Any] | None = None) -> bool:
    env_value = os.getenv("FRETIO_QUOTATION_NORMALIZATION_SHADOW")
    if env_value is not None:
        parsed = _truthy(env_value)
        if parsed is not None:
            return parsed

    section = _fretio_config(config)
    parsed = _truthy(section.get("quotation_normalization_shadow_enabled"))
    return bool(parsed)


def build_shadow_payload(dados: dict[str, Any], cep_origem: str = "", modo: str = "") -> dict[str, Any]:
    dados = dados if isinstance(dados, dict) else {}
    return {
        "modo": str(modo or ""),
        "cep_origem": _cep_or_none(cep_origem) or "",
        "destino_cep": dados.get("destino_cep"),
        "uf_destino": dados.get("uf_destino"),
        "volumes": dados.get("volumes"),
        "peso": dados.get("peso"),
        "valor": dados.get("valor"),
        "cubagem_m3": dados.get("cubagem_m3"),
        "cubagens": dados.get("cubagens"),
    }


def _coerce_number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _coerce_int(value: Any) -> int | None:
    number = _coerce_number(value)
    if number is None:
        return None
    rounded = round(float(number))
    if abs(float(number) - rounded) > _FLOAT_TOLERANCE:
        return None
    return int(rounded)


def _coerce_dimension(value: Any) -> float | int | None:
    number = _coerce_number(value)
    if number is None:
        return None
    rounded = round(float(number))
    if abs(float(number) - rounded) <= _FLOAT_TOLERANCE:
        return int(rounded)
    return number


def _normalize_uf(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if re.fullmatch(r"[A-Z]{2}", text) else None


def _normalize_medidas(cubagens: Any) -> list[dict[str, Any]]:
    if not isinstance(cubagens, list):
        return []
    medidas: list[dict[str, Any]] = []
    for row in cubagens:
        if not isinstance(row, dict):
            continue
        medidas.append(
            {
                "quantidade": _coerce_int(row.get("quantidade")),
                "comprimento_cm": _coerce_dimension(row.get("comprimento_cm")),
                "largura_cm": _coerce_dimension(row.get("largura_cm")),
                "altura_cm": _coerce_dimension(row.get("altura_cm")),
            }
        )
    return medidas


def build_local_normalized_data(dados: dict[str, Any], cep_origem: str = "") -> dict[str, Any]:
    dados = dados if isinstance(dados, dict) else {}
    return {
        "cep_origem": _cep_or_none(cep_origem),
        "cep_destino": _cep_or_none(dados.get("destino_cep")),
        "uf_destino": _normalize_uf(dados.get("uf_destino")),
        "volumes": _coerce_int(dados.get("volumes")),
        "peso_total_kg": _coerce_number(dados.get("peso")),
        "valor_nf": _coerce_number(dados.get("valor")),
        "cubagem_m3": _coerce_number(dados.get("cubagem_m3")),
        "medidas": _normalize_medidas(dados.get("cubagens")),
    }


def _numbers_match(left: Any, right: Any) -> bool:
    left_number = _coerce_number(left)
    right_number = _coerce_number(right)
    if left_number is None or right_number is None:
        return left_number is None and right_number is None
    return abs(float(left_number) - float(right_number)) <= _FLOAT_TOLERANCE


def _ints_match(left: Any, right: Any) -> bool:
    return _coerce_int(left) == _coerce_int(right)


def _text_match(left: Any, right: Any) -> bool:
    return (None if left in ("", None) else str(left)) == (None if right in ("", None) else str(right))


def _medidas_match(local: Any, remote: Any) -> bool:
    local_items = _normalize_medidas(local)
    remote_items = _normalize_medidas(remote)
    if len(local_items) != len(remote_items):
        return False
    for left, right in zip(local_items, remote_items):
        if not _ints_match(left.get("quantidade"), right.get("quantidade")):
            return False
        for key in ("comprimento_cm", "largura_cm", "altura_cm"):
            if not _numbers_match(left.get(key), right.get(key)):
                return False
    return True


def compare_normalized_data(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    local = local if isinstance(local, dict) else {}
    remote = remote if isinstance(remote, dict) else {}
    different_fields: list[str] = []

    for key in _CANONICAL_KEYS:
        if key == "medidas":
            if not _medidas_match(local.get(key), remote.get(key)):
                different_fields.append(key)
        elif key in {"volumes"}:
            if not _ints_match(local.get(key), remote.get(key)):
                different_fields.append(key)
        elif key in {"peso_total_kg", "valor_nf", "cubagem_m3"}:
            if not _numbers_match(local.get(key), remote.get(key)):
                different_fields.append(key)
        elif key in {"cep_origem", "cep_destino"}:
            if (_cep_or_none(local.get(key)) or None) != (_cep_or_none(remote.get(key)) or None):
                different_fields.append(key)
        elif key == "uf_destino":
            if _normalize_uf(local.get(key)) != _normalize_uf(remote.get(key)):
                different_fields.append(key)
        elif not _text_match(local.get(key), remote.get(key)):
            different_fields.append(key)

    return {"match": not different_fields, "different_fields": different_fields}


def _safe_log(log_func: Callable[[str], Any] | None, message: str) -> None:
    try:
        if log_func is not None:
            log_func(message)
    except Exception:
        pass


def _default_normalize_func() -> Callable[..., dict[str, Any]]:
    from quotation_jobs_client import normalize_quotation_payload

    return normalize_quotation_payload


def run_shadow_normalization(
    source_type: str,
    config: dict,
    dados: dict,
    cep_origem: str = "",
    modo: str = "",
    log_func: Callable[[str], Any] | None = None,
    normalize_func: Callable[..., dict[str, Any]] | None = None,
) -> None:
    try:
        if not is_shadow_enabled(config):
            return None
        payload = build_shadow_payload(dados, cep_origem=cep_origem, modo=modo)
        local = build_local_normalized_data(dados, cep_origem=cep_origem)
    except Exception as exc:
        _safe_log(log_func, f"shadow normalization skipped/fail: {exc}")
        return None

    def _worker() -> None:
        try:
            func = normalize_func or _default_normalize_func()
            result = func(source_type, payload=payload, wait=True)
            if not isinstance(result, dict):
                _safe_log(log_func, "shadow normalization skipped/fail: resposta remota invalida")
                return
            data = result.get("data")
            if not isinstance(data, dict):
                status_code = result.get("status_code")
                detail = f" HTTP {status_code}" if status_code else ""
                _safe_log(log_func, f"shadow normalization skipped/fail: sem data{detail}")
                return
            remote = data.get("quotation_data")
            if not isinstance(remote, dict):
                _safe_log(log_func, "shadow normalization skipped/fail: sem quotation_data")
                return

            comparison = compare_normalized_data(local, remote)
            if comparison.get("match"):
                _safe_log(log_func, "Normalização remota sombra OK")
                return
            fields = ", ".join(str(field) for field in comparison.get("different_fields", []))
            _safe_log(log_func, f"Normalização remota sombra divergiu: campos={fields}")
        except Exception as exc:
            _safe_log(log_func, f"shadow normalization skipped/fail: {exc}")

    try:
        thread = threading.Thread(target=_worker, name="FretioQuotationNormalizationShadow", daemon=True)
        thread.start()
    except Exception as exc:
        _safe_log(log_func, f"shadow normalization skipped/fail: {exc}")
    return None
