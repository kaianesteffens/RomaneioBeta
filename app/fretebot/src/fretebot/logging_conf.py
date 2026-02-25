import logging
import os
import sys
from pathlib import Path

def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))

def _log_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        d = Path(appdata) / "FreteBot"
    else:
        d = Path("logs")
    d.mkdir(parents=True, exist_ok=True)
    return d

def setup_logging() -> None:
    log_file = _log_dir() / "fretebot.log"
    if _is_frozen():
        # Rotaciona: trunca se > 5 MB para não crescer indefinidamente
        try:
            if log_file.exists() and log_file.stat().st_size > 5 * 1024 * 1024:
                log_file.write_text("", encoding="utf-8")
        except Exception:
            pass
    fh = logging.FileHandler(log_file, encoding="utf-8")
    # Quando frozen (console=False), sys.stderr é None — StreamHandler falharia
    handlers: list[logging.Handler] = [fh]
    if not _is_frozen():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if not _is_frozen() else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=handlers,
    )

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
