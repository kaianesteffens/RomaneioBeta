from __future__ import annotations

import sys
from pathlib import Path

_SRC_PATH = Path(__file__).resolve().parent / "fretio" / "src"
if _SRC_PATH.exists() and str(_SRC_PATH) not in sys.path:
    sys.path.insert(0, str(_SRC_PATH))

from fretio.logging_conf import bind_logger, get_logger, setup_logging

__all__ = ["setup_logging", "get_logger", "bind_logger"]
