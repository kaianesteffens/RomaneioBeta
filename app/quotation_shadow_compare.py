from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


_SOURCE_TYPES = {"manual", "romaneio", "nfe"}
_MISSING_DESTINATION = "cep_destino_or_uf_destino"
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_NFE_KEY_RE = re.compile(r"\b\d{44}\b")


def normalize_source_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _SOURCE_TYPES else "manual"


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _clean_text(value: Any, *, max_length: int = 120) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    digits = _digits(text)
    if _EMAIL_RE.search(text) or _CNPJ_RE.search(text) or _CPF_RE.search(text) or _NFE_KEY_RE.search(digits):
        return None
    return text[:max_length]


def _normalize_cep(value: Any) -> str | None:
    digits = _digits(value)
    return digits if len(digits) == 8 else None


def _normalize_uf(value: Any) -> str | None:
    text = _clean_text(value, max_length=8)
    if text is None:
        return None
    normalized = re.sub(r"[^A-Za-z]", "", text).upper()
    return normalized if len(normalized) == 2 else None


def _parse_decimal(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None

    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[^\d,.-]", "", text)
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _decimal_to_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _normalize_positive_number(value: Any, *, allow_zero: bool) -> int | float | None:
    number = _parse_decimal(value)
    if number is None:
        return None
    if allow_zero:
        is_valid = number >= 0
    else:
        is_valid = number > 0
    return _decimal_to_number(number) if is_valid else None


def _normalize_volumes(value: Any) -> int | None:
    number = _parse_decimal(value)
    if number is None or number <= 0 or number != number.to_integral_value():
        return None
    return int(number)


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _normalize_measurements(value: Any) -> list[dict[str, int | float]] | None:
    if value in (None, "", []):
        return None
    if not isinstance(value, list):
        return None

    normalized_items: list[dict[str, int | float]] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        normalized_item: dict[str, int | float] = {}
        field_values = {
            "comprimento_cm": _first_present(item, "comprimento_cm", "comprimento"),
            "largura_cm": _first_present(item, "largura_cm", "largura"),
            "altura_cm": _first_present(item, "altura_cm", "altura"),
            "quantidade": _first_present(item, "quantidade", "qtd", "volumes"),
        }
        valid = True
        for field, raw in field_values.items():
            if raw in (None, ""):
                continue
            parsed = _normalize_positive_number(raw, allow_zero=False)
            if parsed is None:
                valid = False
                continue
            normalized_item[field] = parsed

        if valid and normalized_item:
            normalized_items.append(normalized_item)

    return normalized_items or None


def build_shadow_payload(
    payload: dict[str, Any] | None,
    *,
    modo: Any = None,
    cep_origem: Any = None,
) -> dict[str, Any]:
    """Build the safe payload sent to the server shadow normalizer."""
    source = payload if isinstance(payload, dict) else {}
    safe: dict[str, Any] = {}

    modo_value = _clean_text(modo if modo not in (None, "") else source.get("modo"), max_length=80)
    if modo_value:
        safe["modo"] = modo_value

    cep_origem_value = _normalize_cep(cep_origem if cep_origem not in (None, "") else source.get("cep_origem"))
    if cep_origem_value:
        safe["cep_origem"] = cep_origem_value

    cep_destino = _normalize_cep(_first_present(source, "cep_destino", "destino_cep"))
    if cep_destino:
        safe["cep_destino"] = cep_destino

    uf_destino = _normalize_uf(source.get("uf_destino"))
    if uf_destino:
        safe["uf_destino"] = uf_destino

    volumes = _normalize_volumes(source.get("volumes"))
    if volumes is not None:
        safe["volumes"] = volumes

    peso_total = _normalize_positive_number(_first_present(source, "peso_total_kg", "peso"), allow_zero=False)
    if peso_total is not None:
        safe["peso_total_kg"] = peso_total

    valor_nf = _normalize_positive_number(_first_present(source, "valor_nf", "valor"), allow_zero=True)
    if valor_nf is not None:
        safe["valor_nf"] = valor_nf

    cubagem = _normalize_positive_number(source.get("cubagem_m3"), allow_zero=True)
    if cubagem is not None:
        safe["cubagem_m3"] = cubagem

    medidas = _normalize_measurements(_first_present(source, "medidas", "cubagens"))
    if medidas is not None:
        safe["medidas"] = medidas

    return safe


def comparable_quotation_data(payload: dict[str, Any] | None) -> dict[str, Any]:
    comparable = build_shadow_payload(payload)
    comparable.pop("modo", None)
    return comparable


def _missing_fields(quotation_data: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not (quotation_data.get("cep_destino") or quotation_data.get("uf_destino")):
        missing.append(_MISSING_DESTINATION)
    if not quotation_data.get("volumes"):
        missing.append("volumes")
    if not quotation_data.get("peso_total_kg"):
        missing.append("peso_total_kg")
    return missing


def _values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 0.000001
    return left == right


def compare_quotation_normalization(
    local_payload: dict[str, Any] | None,
    server_response: dict[str, Any] | None,
) -> dict[str, Any]:
    local_data = comparable_quotation_data(local_payload)
    response = server_response if isinstance(server_response, dict) else {}
    remote_raw = response.get("quotation_data", {})
    remote_data = comparable_quotation_data(remote_raw if isinstance(remote_raw, dict) else {})

    differences: list[dict[str, Any]] = []
    for field in sorted(set(local_data) | set(remote_data)):
        local_value = local_data.get(field)
        remote_value = remote_data.get(field)
        if not _values_equal(local_value, remote_value):
            differences.append({"field": field, "local": local_value, "remote": remote_value})

    local_missing = _missing_fields(local_data)
    remote_missing_raw = response.get("missing_fields", [])
    remote_missing = sorted(str(item) for item in remote_missing_raw) if isinstance(remote_missing_raw, list) else []
    if sorted(local_missing) != remote_missing:
        differences.append({"field": "missing_fields", "local": sorted(local_missing), "remote": remote_missing})

    local_ready = not local_missing
    remote_ready = bool(response.get("ready_for_quotation"))
    if local_ready != remote_ready:
        differences.append({"field": "ready_for_quotation", "local": local_ready, "remote": remote_ready})

    warnings = response.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = []

    return {
        "matched": not differences,
        "differences": differences,
        "local": local_data,
        "remote": remote_data,
        "remote_ready_for_quotation": remote_ready,
        "remote_missing_fields": remote_missing,
        "remote_warnings": [str(item) for item in warnings],
    }
