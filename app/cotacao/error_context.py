"""Contexto padronizado para erros de cotação enviados ao servidor."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import json
import re
import traceback as traceback_mod

from . import common as _common

try:
    from error_reporter import (  # type: ignore[import-not-found]
        _get_machine_id_for_report,
        _get_saved_license_key,
        _get_version,
    )
except Exception:
    def _get_version() -> str:
        return ""

    def _get_saved_license_key() -> str:
        return ""

    def _get_machine_id_for_report() -> str:
        return ""


MODULE = "cotacao"
DEFAULT_SOURCE = "cotacao_provider"
ALLOWED_STAGES = {
    "pre_login",
    "login",
    "abrir_pagina",
    "preencher_formulario",
    "enviar_cotacao",
    "aguardar_resultado",
    "interpretar_resultado",
    "cleanup",
}

_SENSITIVE_KEY_RE = re.compile(
    r"(senha|password|passwd|pwd|token|secret|cookie|authorization|auth|"
    r"credential|credencial|login|usuario|user|email)",
    re.IGNORECASE,
)
_HTML_KEY_RE = re.compile(r"(html|outer_html|inner_html|dom|page_content|body_html|raw_html)", re.IGNORECASE)
_CNPJ_RE = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")
_CPF_RE = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
_NFE_KEY_RE = re.compile(r"(?<!\d)\d{44}(?!\d)")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_ASSIGNMENT_RE = re.compile(
    r"\b(senha|password|passwd|pwd|token|secret|authorization|cookie|login|usuario|email)"
    r"\b\s*[:=]\s*([^\s,;]+)",
    re.IGNORECASE,
)
_AUTH_HEADER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._\-+/=]+", re.IGNORECASE)
_HTML_BLOCK_RE = re.compile(r"(?is)<!doctype\s+html.*|<html\b.*?</html>")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]{0,500}>")

_MAX_DEPTH = 6
_MAX_ITEMS = 60
_MAX_LIST_ITEMS = 40
_MAX_STRING_LENGTH = 1200


def report_provider_error(
    provider: str,
    stage: str,
    message: str,
    exception: BaseException | None = None,
    context: Mapping[str, Any] | None = None,
    severity: str = "error",
) -> None:
    """Reporta erro técnico de provider em formato agrupável e sanitizado.

    O reporte é best-effort: qualquer falha local é ignorada para não travar a
    cotação.
    """
    try:
        provider_norm = _normalize_short(provider, lowercase=True)
        stage_norm = _normalize_stage(stage)
        context_dict = dict(context or {})
        browser_state = context_dict.pop("browser_state", None)
        source = _normalize_short(context_dict.pop("source", DEFAULT_SOURCE), lowercase=True) or DEFAULT_SOURCE
        carrier_enabled = context_dict.pop("carrier_enabled", None)
        event = _normalize_short(
            context_dict.pop("event", f"{provider_norm or 'provider'}_{stage_norm}_failed"),
            lowercase=True,
            max_length=100,
        )

        tb_text = ""
        if exception is not None:
            tb_text = "".join(
                traceback_mod.format_exception(type(exception), exception, exception.__traceback__)
            )
            if not message:
                message = f"{type(exception).__name__}: {exception}"

        payload = {
            "module": MODULE,
            "provider": provider_norm,
            "stage": stage_norm,
            "event": event,
            "severity": _normalize_short(severity, lowercase=True, max_length=32) or "error",
            "source": source,
            "carrier_enabled": bool(carrier_enabled) if carrier_enabled is not None else None,
            "message": _sanitize_text(message, max_length=2000),
            "traceback": _sanitize_text(tb_text, max_length=20000),
            "context_json": sanitize_context(context_dict),
            "browser_state_json": sanitize_context(browser_state),
        }

        for key, getter in (
            ("app_version", _get_version),
            ("license_key", _get_saved_license_key),
            ("machine_id", _get_machine_id_for_report),
        ):
            try:
                value = str(getter() or "").strip()
            except Exception:
                value = ""
            if value:
                payload[key] = value

        reporter = getattr(_common, "report_error_payload", None)
        if callable(reporter):
            reporter(payload)
    except Exception:
        pass


def sanitize_context(value: Any) -> Any:
    return _sanitize_value(value, depth=0)


def _sanitize_value(value: Any, *, depth: int) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if depth > _MAX_DEPTH:
        return "[LIMITE_DE_PROFUNDIDADE]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, raw_item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                result["_truncated_items"] = True
                break
            safe_key = _normalize_json_key(key)
            if _SENSITIVE_KEY_RE.search(safe_key):
                result[safe_key] = "[DADO_SENSIVEL_REMOVIDO]"
            elif _HTML_KEY_RE.search(safe_key):
                result[safe_key] = "[HTML_REMOVIDO]"
            else:
                result[safe_key] = _sanitize_value(raw_item, depth=depth + 1)
        return result
    if isinstance(value, list | tuple | set):
        items = list(value)
        result = [_sanitize_value(item, depth=depth + 1) for item in items[:_MAX_LIST_ITEMS]]
        if len(items) > _MAX_LIST_ITEMS:
            result.append({"_truncated_items": True})
        return result
    if isinstance(value, str):
        return _sanitize_text(value, max_length=_MAX_STRING_LENGTH)
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except Exception:
        return _sanitize_text(str(value), max_length=_MAX_STRING_LENGTH)


def _sanitize_text(value: Any, *, max_length: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        return ""
    text = _HTML_BLOCK_RE.sub("[HTML_REMOVIDO]", text)
    if _looks_like_raw_html(text):
        text = "[HTML_REMOVIDO]"
    text = _ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[DADO_SENSIVEL_REMOVIDO]", text)
    text = _AUTH_HEADER_RE.sub("Bearer [DADO_SENSIVEL_REMOVIDO]", text)
    text = _EMAIL_RE.sub("[EMAIL_REMOVIDO]", text)
    text = _CNPJ_RE.sub("[CNPJ_REMOVIDO]", text)
    text = _CPF_RE.sub("[CPF_REMOVIDO]", text)
    text = _NFE_KEY_RE.sub("[CHAVE_NFE_REMOVIDA]", text)
    return text[:max_length]


def _looks_like_raw_html(text: str) -> bool:
    lowered = text.lower()
    if "<html" in lowered or "</body" in lowered:
        return True
    return len(_HTML_TAG_RE.findall(text[:5000])) >= 8


def _normalize_stage(stage: str) -> str:
    normalized = _normalize_short(stage, lowercase=True, max_length=64) or "interpretar_resultado"
    aliases = {
        "prelogin": "pre_login",
        "pre-login": "pre_login",
        "cotacao": "enviar_cotacao",
        "navegando_cotacao": "abrir_pagina",
        "submetendo_cotacao": "enviar_cotacao",
        "valor_resultado": "interpretar_resultado",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in ALLOWED_STAGES else "interpretar_resultado"


def _normalize_short(value: Any, *, lowercase: bool = True, max_length: int = 100) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if lowercase:
        text = text.lower()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", text)
    return text.strip("_.:-")[:max_length]


def _normalize_json_key(value: Any) -> str:
    return _normalize_short(value, lowercase=False, max_length=80) or "value"


__all__ = [
    "ALLOWED_STAGES",
    "report_provider_error",
    "sanitize_context",
]
