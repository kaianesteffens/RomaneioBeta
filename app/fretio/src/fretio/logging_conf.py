import logging
import os
import sys
from pathlib import Path
from typing import Any

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s%(context)s"
_CONTEXT_FIELDS = ("empresa", "provider", "operation", "file", "function", "line")


class ContextFormatter(logging.Formatter):
    """Formatter que adiciona campos extras opcionais ao final da mensagem."""

    def format(self, record: logging.LogRecord) -> str:
        context_parts: list[str] = []
        for field in _CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value in (None, "", 0, "<unknown>"):
                continue
            context_parts.append(f"{field}={value}")
        record.context = f" | {' '.join(context_parts)}" if context_parts else ""
        return super().format(record)

def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))

def _log_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        d = Path(appdata) / "Fretio"
    else:
        d = Path("logs")
    d.mkdir(parents=True, exist_ok=True)
    return d


class ContextLoggerAdapter(logging.LoggerAdapter):
    """LoggerAdapter que mescla contexto padrão com extras passados por chamada."""

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        call_extra = kwargs.get("extra")
        if isinstance(call_extra, dict):
            extra.update({k: v for k, v in call_extra.items() if v not in (None, "")})
        kwargs["extra"] = extra
        return msg, kwargs


def setup_logging() -> None:
    root_logger = logging.getLogger()
    if getattr(root_logger, "_fretio_logging_configured", False):
        return

    log_file = _log_dir() / "fretio.log"
    if _is_frozen():
        # Rotaciona: trunca se > 5 MB para não crescer indefinidamente
        try:
            if log_file.exists() and log_file.stat().st_size > 5 * 1024 * 1024:
                log_file.write_text("", encoding="utf-8")
        except Exception:
            pass
    formatter = ContextFormatter(_LOG_FORMAT)
    level = logging.DEBUG if not _is_frozen() else logging.INFO

    if root_logger.handlers:
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)
        root_logger.setLevel(min(root_logger.level, level) if root_logger.level else level)
        root_logger._fretio_logging_configured = True
        return

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)

    # Quando frozen (console=False), sys.stderr é None — StreamHandler falharia
    handlers: list[logging.Handler] = [fh]
    if not _is_frozen():
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        handlers.append(sh)

    logging.basicConfig(level=level, handlers=handlers)
    root_logger._fretio_logging_configured = True

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def bind_logger(logger_or_name: logging.Logger | str, **context: Any) -> logging.LoggerAdapter:
    logger = get_logger(logger_or_name) if isinstance(logger_or_name, str) else logger_or_name
    clean_context = {k: v for k, v in context.items() if v not in (None, "")}
    return ContextLoggerAdapter(logger, clean_context)
