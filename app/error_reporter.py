"""
Fretio — Registro Local de Erros.

Grava erros em log local (%APPDATA%/Fretio/error_reporter.log), sanitizando
dados sensíveis antes de gravar. Não há mais envio para servidor: o app é
totalmente offline em relação a relatórios de erro. Falhas ao registrar são
silenciosas — nunca impactam o uso do app.
"""
from __future__ import annotations

import os
import re
import sys
import time
import traceback
import threading
from pathlib import Path


# ── Supressão de reports originados de testes ─────────────────────
# Dublês de teste (ex.: ProviderFactory falso em test_*.py) podem percorrer o
# caminho normal de erro do app. Esses "erros" não são reais e não devem poluir
# o log local. Testes que validam o próprio registro desligam o guard com
# `suppress_test_reports = False`.
suppress_test_reports = True

# ── Log de diagnóstico ────────────────────────────────────────────
_LOG_MAX_BYTES = 100 * 1024  # 100 KB — rotaciona apagando metade quando ultrapassar
_log_lock = threading.Lock()


def sanitize_error_payload(text: str) -> str:
    """Remove dados sensíveis de mensagens de erro antes de gravar no log."""
    sanitized = str(text or "")

    secret_field_names = (
        "admin_token",
        "database_url",
        "senha",
        "password",
        "token",
        "error_report_token",
    )
    license_field_names = (
        "license",
        "license_key",
        "licenca",
        "licença",
    )
    field_pattern = "|".join(re.escape(name) for name in secret_field_names + license_field_names)

    def _redact_field(match: re.Match) -> str:
        field_name = match.group(1).casefold()
        if field_name in {"admin_token", "database_url"}:
            return "[TOKEN_REDACTED]"
        marker = "[LICENSE_REDACTED]" if field_name in {name.casefold() for name in license_field_names} else "[TOKEN_REDACTED]"
        return f"{match.group(1)}{match.group(2)}{match.group(3)}{marker}"

    sanitized = re.sub(
        rf"(?i)\b({field_pattern})\b(\s*[:=]\s*)([`'\"]?)([^`'\"\s,;|]+)",
        _redact_field,
        sanitized,
    )

    sanitized = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [TOKEN_REDACTED]",
        sanitized,
    )
    sanitized = re.sub(r"\bghp_[A-Za-z0-9_]{20,}\b", "[TOKEN_REDACTED]", sanitized)
    sanitized = re.sub(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "[TOKEN_REDACTED]", sanitized)
    sanitized = re.sub(
        r"(?i)([?&](?:token|access_token|auth|key|password|senha|license|licenca|licen%C3%A7a)=)[^&#\s]+",
        r"\1[TOKEN_REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)\bhttps?://[^\s`'\"<>]*(?:token|access_token|auth|key|password|senha|license|licenca|licen%C3%A7a)=[^\s`'\"<>]+",
        "[URL_REDACTED]",
        sanitized,
    )

    sanitized = re.sub(
        r"\bFBOT-[A-Z0-9]{4}(?:-[A-Z0-9]{4}){0,5}\b",
        "[LICENSE_REDACTED]",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
        "[EMAIL_REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?<!\d)\d{44}(?!\d)",
        "[NFE_KEY_REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b",
        "[CNPJ_REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",
        "[CPF_REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"\b\d{5}-?\d{3}\b",
        "[CEP_REDACTED]",
        sanitized,
    )
    return sanitized


def _sanitize_extra_value(value):
    """Sanitiza recursivamente valores do dict ``extra`` (dict/list/str)."""
    if isinstance(value, dict):
        return {k: _sanitize_extra_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_extra_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_error_payload(value)
    return value


def _log_path() -> Path:
    appdata = os.getenv("APPDATA", "")
    if appdata:
        return Path(appdata) / "Fretio" / "error_reporter.log"
    return Path(__file__).parent / "error_reporter.log"


def _diag(level: str, msg: str) -> None:
    """Grava linha de diagnóstico no log local. Nunca lança exceção."""
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {msg}\n"
        with _log_lock:
            # Rotação simples: se ultrapassar limite, mantém só a metade final
            if p.exists() and p.stat().st_size > _LOG_MAX_BYTES:
                content = p.read_bytes()
                p.write_bytes(content[len(content) // 2:])
            with p.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def _read_recent_diag_log(max_bytes: int = 12_000) -> str:
    try:
        p = _log_path()
        if not p.exists():
            return ""
        raw = p.read_bytes()
        tail = raw[-max_bytes:] if len(raw) > max_bytes else raw
        return tail.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def configure(config_path=None) -> None:
    """No-op. Mantida por compatibilidade — não há mais configuração remota.

    Ainda é chamada no boot (app_bootstrap.py / web_app.py); permanece inerte.
    """
    pass


def _running_under_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _traceback_is_test_originated(text: str) -> bool:
    """True se algum frame do traceback vem de arquivo de teste.

    Cobre ``test_*.py``, ``*_test.py``, ``conftest.py`` e qualquer caminho sob
    um diretório ``tests/``. Um erro real de produção nunca tem frame assim."""
    if not text:
        return False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("File "):
            continue
        match = re.search(r'File "([^"]+)"', line)
        path = (match.group(1) if match else line).replace("\\", "/").lower()
        base = path.rsplit("/", 1)[-1]
        if base.startswith("test_") or base.endswith("_test.py") or base == "conftest.py":
            return True
        if "/tests/" in path:
            return True
    return False


def _is_test_originated_report(traceback_text: str = "") -> bool:
    """Decide se um report deve ser descartado por ter origem em teste."""
    if not suppress_test_reports:
        return False
    return _running_under_pytest() or _traceback_is_test_originated(traceback_text)


def report_error_payload(payload: dict, wait: bool = False) -> None:
    """Registra um payload de erro estruturado no log local (sanitizado).

    ``wait`` é ignorado (mantido por compatibilidade de assinatura — não há mais
    envio assíncrono). Best-effort e sem propagar exceções.
    """
    try:
        if not isinstance(payload, dict):
            return

        module = str(payload.get("module") or "")
        provider = str(payload.get("provider") or "")
        stage = str(payload.get("stage") or "")
        message = str(payload.get("message") or "")
        traceback_text = str(payload.get("traceback") or "")

        if _is_test_originated_report(traceback_text):
            _diag("DEBUG", f"report_error_payload({module}/{provider}/{stage}): origem de teste — descartado")
            return

        sanitized_extra = _sanitize_extra_value(payload)
        header = f"payload/{module or 'N/A'}/{provider or 'N/A'}/{stage or 'N/A'}"
        parts = [header]
        if message:
            parts.append(f"message={sanitize_error_payload(message)}")
        if traceback_text:
            parts.append(f"traceback=\n{sanitize_error_payload(traceback_text)}")
        parts.append(f"extra={sanitized_extra}")
        _diag("ERROR", " | ".join(parts))
    except Exception:
        pass


def report_error(
    exc_type: type | None = None,
    exc_value: BaseException | None = None,
    exc_tb=None,
    context: str = "",
    wait: bool = False,
) -> None:
    """
    Registra um erro no log local (sanitizado).

    Pode ser chamado diretamente ou como sys.excepthook. ``wait`` é ignorado
    (mantido por compatibilidade — não há mais envio assíncrono). Falhas ao
    registrar são silenciosas.
    """
    try:
        if exc_type is None and exc_value is None:
            # Usa a exceção atual do sys.exc_info()
            exc_type, exc_value, exc_tb = sys.exc_info()

        if exc_type is None:
            return

        # Não registrar KeyboardInterrupt ou SystemExit
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return

        exc_type_name = getattr(exc_type, "__name__", str(exc_type))
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

        if _is_test_originated_report(tb_text):
            _diag("DEBUG", f"report_error({exc_type_name}): origem de teste — descartado")
            return

        message = sanitize_error_payload(f"{exc_type_name}: {exc_value}")
        traceback_text = sanitize_error_payload(tb_text)
        parts = [f"{exc_type_name}/{context or 'N/A'}", f"message={message}"]
        if traceback_text:
            parts.append(f"traceback=\n{traceback_text}")
        _diag("ERROR", " | ".join(parts))
    except Exception:
        pass  # Falha silenciosa — error reporting NUNCA deve crashar o app


def report_error_message(message: str, context: str = "", wait: bool = False) -> None:
    """
    Registra uma mensagem de erro customizada (sem exceção Python) no log local.

    Útil para erros de lógica ou condições inesperadas. ``wait`` é ignorado
    (mantido por compatibilidade — não há mais envio assíncrono).
    """
    try:
        if _is_test_originated_report("".join(traceback.format_stack())):
            _diag("DEBUG", f"report_error_message: origem de teste — descartado: {message[:80]}")
            return

        sanitized = sanitize_error_payload(str(message or ""))
        _diag("ERROR", f"msg/{context or 'N/A'} | message={sanitized}")
    except Exception:
        pass


# ── Hooks globais ───────────────────────────────────────────────
_original_excepthook = None
_original_threading_excepthook = None


def install_global_hooks() -> None:
    """
    Instala hooks globais para capturar exceções não tratadas.
    Chame uma vez na inicialização do app.
    """
    global _original_excepthook, _original_threading_excepthook

    _diag("INFO", "install_global_hooks(): inicializando")

    # Hook principal (exceções não tratadas no thread principal)
    _original_excepthook = sys.excepthook

    def _sys_excepthook(exc_type, exc_value, exc_tb):
        report_error(exc_type, exc_value, exc_tb, context="sys.excepthook", wait=True)
        if _original_excepthook:
            _original_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _sys_excepthook

    # Hook de threading (exceções não tratadas em threads)
    _original_threading_excepthook = threading.excepthook

    def _thread_excepthook(args):
        thread_name = getattr(args.thread, "name", None) or str(args.thread)
        report_error(
            args.exc_type, args.exc_value, args.exc_traceback,
            context=f"thread:{thread_name}",
        )
        if _original_threading_excepthook:
            try:
                _original_threading_excepthook(args)
            except Exception:
                pass

    threading.excepthook = _thread_excepthook
    _diag("INFO", "install_global_hooks(): hooks instalados (sys.excepthook + threading.excepthook)")
