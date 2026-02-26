"""
FreteBot — Relatório de Erros Remoto.

Envia erros automaticamente para um GitHub Gist (como comentários).
Cada erro vira um comentário no gist, sem criar arquivos locais.
Falhas no envio são silenciosas — nunca impacta o uso do app.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
import traceback
import threading
from hashlib import sha256
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen


# ── Rate-limit: no máximo 1 report por erro idêntico a cada 10min ───
_RATE_LIMIT_SECONDS = 600
_recent_errors: dict[str, float] = {}
_lock = threading.Lock()

# ── Configurações (lidas do CONFIG.toml) ─────────────────────────
_gist_id: str = ""
_token: str = ""
_initialized = False


def _load_config() -> None:
    """Carrega error_gist_id e error_report_token do CONFIG.toml."""
    global _gist_id, _token, _initialized
    if _initialized:
        return
    _initialized = True
    try:
        import toml
        # Mesmo esquema de busca que o restante do app
        for candidate in [
            Path(os.getenv("APPDATA", "")) / "FreteBot" / "CONFIG.toml",
            Path(getattr(sys, "_MEIPASS", "")) / "CONFIG.toml",
            Path(__file__).parent / "CONFIG.toml",
        ]:
            if candidate.exists():
                cfg = toml.load(candidate)
                fb = cfg.get("fretebot", {})
                _gist_id = fb.get("error_gist_id", "")
                _token = fb.get("error_report_token", "")
                if _gist_id and _token:
                    return
    except Exception:
        pass


def _get_version() -> str:
    try:
        p = Path(getattr(sys, "_MEIPASS", "")) / "version.txt"
        if p.exists():
            return p.read_text().strip()
        p = Path(__file__).parent / "version.txt"
        if p.exists():
            return p.read_text().strip()
    except Exception:
        pass
    return "?"


def _get_machine_hash() -> str:
    """Retorna hash parcial da máquina (sem expor dados sensíveis)."""
    try:
        node = platform.node()
        user = os.getenv("USERNAME", "?")
        return sha256(f"{node}|{user}".encode()).hexdigest()[:12]
    except Exception:
        return "unknown"


def _get_license_key() -> str:
    """Lê a chave de licença salva (para identificar o cliente)."""
    try:
        f = Path(os.getenv("APPDATA", "")) / "FreteBot" / "license.key"
        if f.exists():
            key = f.read_text(encoding="utf-8").strip()
            # Retorna só os primeiros 9 chars para privacidade (FBOT-XXXX)
            return key[:9] if len(key) > 9 else key
    except Exception:
        pass
    return "?"


def _error_fingerprint(exc_type_name: str, tb_text: str) -> str:
    """Gera hash do erro para deduplicação/rate-limit."""
    # Normaliza o traceback removendo números de linha específicos
    normalized = ""
    for line in tb_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("File "):
            # Mantém só nome do arquivo e função, não número de linha
            parts = stripped.split(",")
            normalized += parts[0] + (parts[-1] if len(parts) > 1 else "") + "\n"
        else:
            normalized += stripped + "\n"
    return sha256(f"{exc_type_name}:{normalized}".encode()).hexdigest()[:16]


def _is_rate_limited(fingerprint: str) -> bool:
    """Verifica se esse erro já foi reportado recentemente."""
    now = time.time()
    with _lock:
        last = _recent_errors.get(fingerprint, 0)
        if now - last < _RATE_LIMIT_SECONDS:
            return True
        _recent_errors[fingerprint] = now
        # Limpa entradas antigas
        expired = [k for k, v in _recent_errors.items() if now - v > _RATE_LIMIT_SECONDS * 2]
        for k in expired:
            del _recent_errors[k]
    return False


def _send_to_gist(body: str) -> bool:
    """Envia um comentário ao Gist via API do GitHub."""
    _load_config()
    if not _gist_id or not _token:
        return False
    url = f"https://api.github.com/gists/{_gist_id}/comments"
    payload = json.dumps({"body": body}).encode("utf-8")
    req = Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"token {_token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urlopen(req, timeout=15) as resp:
            return resp.status == 201
    except Exception:
        return False


def report_error(
    exc_type: type | None = None,
    exc_value: BaseException | None = None,
    exc_tb=None,
    context: str = "",
) -> None:
    """
    Envia um erro para o GitHub Gist.

    Pode ser chamado diretamente ou como sys.excepthook.
    Falhas no envio são silenciosas.
    """
    try:
        if exc_type is None and exc_value is None:
            # Usa a exceção atual do sys.exc_info()
            exc_type, exc_value, exc_tb = sys.exc_info()

        if exc_type is None:
            return

        # Não reportar KeyboardInterrupt ou SystemExit
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return

        exc_type_name = getattr(exc_type, "__name__", str(exc_type))
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

        # Rate-limit por fingerprint
        fp = _error_fingerprint(exc_type_name, tb_text)
        if _is_rate_limited(fp):
            return

        # Monta o corpo do comentário (Markdown)
        version = _get_version()
        machine = _get_machine_hash()
        license_id = _get_license_key()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        os_info = f"{platform.system()} {platform.release()} ({platform.version()})"

        body_parts = [
            f"## {exc_type_name}: {str(exc_value)[:200]}",
            "",
            f"| Campo | Valor |",
            f"|-------|-------|",
            f"| Versão | `{version}` |",
            f"| Máquina | `{machine}` |",
            f"| Licença | `{license_id}` |",
            f"| OS | `{os_info}` |",
            f"| Data/Hora | `{timestamp}` |",
            f"| Contexto | `{context or 'N/A'}` |",
            f"| Fingerprint | `{fp}` |",
            "",
            "### Traceback",
            "```python",
            tb_text.strip(),
            "```",
        ]
        body = "\n".join(body_parts)

        # Envia em thread separada para não bloquear o app
        t = threading.Thread(target=_send_to_gist, args=(body,), daemon=True)
        t.start()

    except Exception:
        pass  # Falha silenciosa — error reporting NUNCA deve crashar o app


def report_error_message(message: str, context: str = "") -> None:
    """
    Envia uma mensagem de erro customizada (sem exceção Python).
    Útil para erros de lógica ou condições inesperadas.
    """
    try:
        fp = sha256(message.encode()).hexdigest()[:16]
        if _is_rate_limited(fp):
            return

        version = _get_version()
        machine = _get_machine_hash()
        license_id = _get_license_key()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        body_parts = [
            f"## ⚠️ {message[:200]}",
            "",
            f"| Campo | Valor |",
            f"|-------|-------|",
            f"| Versão | `{version}` |",
            f"| Máquina | `{machine}` |",
            f"| Licença | `{license_id}` |",
            f"| Data/Hora | `{timestamp}` |",
            f"| Contexto | `{context or 'N/A'}` |",
        ]
        body = "\n".join(body_parts)

        t = threading.Thread(target=_send_to_gist, args=(body,), daemon=True)
        t.start()

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

    # Hook principal (exceções não tratadas no thread principal)
    _original_excepthook = sys.excepthook

    def _sys_excepthook(exc_type, exc_value, exc_tb):
        report_error(exc_type, exc_value, exc_tb, context="sys.excepthook")
        if _original_excepthook:
            _original_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _sys_excepthook

    # Hook de threading (exceções não tratadas em threads)
    _original_threading_excepthook = threading.excepthook

    def _thread_excepthook(args):
        report_error(
            args.exc_type, args.exc_value, args.exc_traceback,
            context=f"thread:{args.thread}",
        )
        if _original_threading_excepthook:
            try:
                _original_threading_excepthook(args)
            except Exception:
                pass

    threading.excepthook = _thread_excepthook
