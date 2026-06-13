"""Contexto padronizado para erros de cotação enviados ao servidor."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import json
import re
import traceback as traceback_mod

try:
    from error_reporter import (  # type: ignore[import-not-found]
        _get_machine_id_for_report,
        _get_saved_license_key,
        _get_version,
        report_error_payload,
    )
except Exception:
    def _get_version() -> str:
        return ""

    def _get_saved_license_key() -> str:
        return ""

    def _get_machine_id_for_report() -> str:
        return ""

    def report_error_payload(*a, **kw):
        return None


# Re-exportado de error_classifiers (fonte única dos classificadores). Mantido em
# __all__ p/ quem importa `from cotacao.error_context import is_expected_prelogin_failure`.
from .error_classifiers import is_expected_prelogin_failure


MODULE = "cotacao"
DEFAULT_SOURCE = "cotacao_provider"
ALLOWED_STAGES = {
    "pre_validacao",
    "montar_request",
    "criar_job",
    "instanciar_provider",
    "pre_login",
    "login",
    "abrir_cotacao",
    "preencher_origem_destino",
    "preencher_destinatario",
    "preencher_peso_valor",
    "preencher_volumes",
    "preencher_cubagem",
    "submeter_cotacao",
    "ler_resultado",
    "sem_cotacao",
    "cleanup",
    "erro_desconhecido",
}

_ALLOWED_SOURCE_TYPES = {"romaneio", "nfe", "manual", "unknown"}

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

        if callable(report_error_payload):
            report_error_payload(payload)
    except Exception:
        pass


def build_quotation_error_diagnostic(
    *,
    provider: str = "",
    stage: str = "",
    source_type: str = "unknown",
    quote_job_id: Any = None,
    dados: Mapping[str, Any] | None = None,
    kwargs: Mapping[str, Any] | None = None,
    provider_context: Mapping[str, Any] | None = None,
    safe_hints: Mapping[str, Any] | None = None,
    error: BaseException | None = None,
    last_error: Any = None,
) -> dict[str, Any]:
    """Monta diagnóstico sanitizado para falhas de cotação.

    O retorno contém apenas flags, contagens e metadados técnicos seguros.
    Dados operacionais sensíveis (CNPJ/CPF completos, credenciais, HTML, XML/PDF,
    cookies ou screenshots) não são copiados para o diagnóstico.
    """
    merged_data = _merge_quote_data(dados, kwargs)
    provider_norm = _normalize_short(provider, lowercase=True)
    stage_norm = _normalize_stage(stage)
    last_error_text = str(last_error or error or "")

    provider_ctx = dict(provider_context or {})
    provider_ctx.setdefault("provider_key", provider_norm)
    provider_ctx.setdefault("stage", stage_norm)
    provider_ctx.setdefault("error_type", _classify_error_type(error=error, message=last_error_text, stage=stage_norm))
    provider_ctx.setdefault("last_error_kind", _classify_last_error_kind(error=error, message=last_error_text))

    hints = dict(safe_hints or {})
    if "headless" in provider_ctx and "headless" not in hints:
        hints["headless"] = provider_ctx.get("headless")
        provider_ctx.pop("headless", None)

    diagnostic: dict[str, Any] = {
        "diagnostic_version": 1,
        "flow": "cotacao",
        "source_type": _normalize_source_type(source_type),
        "provider": provider_norm,
        "stage": stage_norm,
        "data_flags": _quote_data_flags(merged_data),
        "provider_context": provider_ctx,
        "safe_hints": hints,
    }
    if quote_job_id not in (None, ""):
        diagnostic["quote_job_id"] = _safe_job_id(quote_job_id)
    return sanitize_context(diagnostic)


def _merge_quote_data(
    dados: Mapping[str, Any] | None,
    kwargs: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if isinstance(dados, Mapping):
        merged.update(dados)
    if isinstance(kwargs, Mapping):
        aliases = {
            "origem": "cep_origem",
            "destino": "destino_cep",
            "peso": "peso",
            "valor": "valor",
            "volumes": "volumes",
            "cubagens": "cubagens",
            "cubagem_m3": "cubagem_m3",
            "uf_destino": "uf_destino",
            "cnpj_destinatario": "cnpj_destinatario",
        }
        for src_key, dest_key in aliases.items():
            if src_key in kwargs and kwargs.get(src_key) not in (None, ""):
                merged[dest_key] = kwargs.get(src_key)
    return merged


def _quote_data_flags(data: Mapping[str, Any]) -> dict[str, Any]:
    cubagens = data.get("cubagens")
    cubagens_list = cubagens if isinstance(cubagens, list) else []
    cubagens_count = len([item for item in cubagens_list if isinstance(item, Mapping)])
    volumes_total = _safe_int(data.get("volumes"))
    if volumes_total <= 0 and cubagens_count:
        volumes_total = sum(_safe_int(item.get("quantidade")) for item in cubagens_list if isinstance(item, Mapping))

    return {
        "cep_origem_ok": len(_digits(data.get("cep_origem") or data.get("origem"))) == 8,
        "cep_destino_ok": len(_digits(data.get("destino_cep") or data.get("destino"))) == 8,
        "uf_destino_ok": len(str(data.get("uf_destino") or "").strip()) == 2,
        "cnpj_destinatario_ok": len(_digits(data.get("cnpj_destinatario"))) == 14,
        "peso_ok": _safe_float(data.get("peso")) > 0,
        "valor_ok": _safe_float(data.get("valor")) >= 0,
        "cubagens_count": cubagens_count,
        "volumes_total": volumes_total,
        "has_cubagens": cubagens_count > 0,
    }


def _classify_error_type(*, error: BaseException | None, message: str, stage: str) -> str:
    text = f"{type(error).__name__ if error else ''} {message}".lower()
    if "timeout" in text or "timed out" in text:
        if "selector" in text or "locator" in text:
            return "selector_timeout"
        return "timeout"
    if "login" in stage or "login" in text or "credenciais" in text or "acesso negado" in text:
        return "login_failed"
    if "sem cot" in text or "sem valor" in text or stage == "sem_cotacao":
        return "sem_cotacao"
    if "cubagem" in text or "cnpj" in text or "cep" in text or "peso" in text:
        return "dados_invalidos"
    return "provider_error"


def _classify_last_error_kind(*, error: BaseException | None, message: str) -> str:
    text = f"{type(error).__name__ if error else ''} {message}".lower()
    if "playwright" in text or "locator" in text or "page." in text:
        return "playwright_timeout" if "timeout" in text else "playwright_error"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if error is not None:
        return _normalize_short(type(error).__name__, lowercase=True, max_length=80) or "exception"
    return "last_error" if str(message or "").strip() else "unknown"


def _normalize_source_type(value: Any) -> str:
    normalized = _normalize_short(value, lowercase=True, max_length=32) or "unknown"
    aliases = {
        "pdf": "romaneio",
        "romaneio_colado": "manual",
        "fornecedor": "manual",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _ALLOWED_SOURCE_TYPES else "unknown"


def _safe_job_id(value: Any) -> int | str:
    try:
        if isinstance(value, bool):
            return str(value)
        return int(value)
    except Exception:
        return _normalize_short(value, lowercase=False, max_length=80)


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


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
            if _is_safe_flag_key(safe_key, raw_item):
                result[safe_key] = _sanitize_value(raw_item, depth=depth + 1)
            elif _SENSITIVE_KEY_RE.search(safe_key):
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


def _is_safe_flag_key(key: str, value: Any) -> bool:
    return key.endswith("_ok") and isinstance(value, bool)


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
        "init_browser": "pre_login",
        "abrir_pagina": "abrir_cotacao",
        "navegando_cotacao": "abrir_cotacao",
        "preenchendo_formulario": "preencher_cubagem",
        "preencher_formulario": "preencher_cubagem",
        "enviar_cotacao": "submeter_cotacao",
        "submetendo_cotacao": "submeter_cotacao",
        "aguardar_resultado": "ler_resultado",
        "interpretar_resultado": "ler_resultado",
        "valor_resultado": "ler_resultado",
        "resultado": "ler_resultado",
        "validacao": "pre_validacao",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in ALLOWED_STAGES else "erro_desconhecido"


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
    "build_quotation_error_diagnostic",
    "is_expected_prelogin_failure",
    "report_provider_error",
    "sanitize_context",
]
