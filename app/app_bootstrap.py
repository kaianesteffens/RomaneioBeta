"""Partida de processo (Qt-free) compartilhada pelo app.

Extraído de romaneio_app.main(): migração de AppData, limpeza de credenciais de
dev, log de crash, instância única (mutex), instalação canônica, hooks globais de
exceção e logging de diagnóstico. Nenhuma dependência de PySide6 — usado tanto
pela UI web (web_app.py) quanto, historicamente, pela UI Qt.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

_SINGLE_INSTANCE_MUTEX_HANDLE = None


# ── Instância / instalação única ───────────────────────────────────────────
def _executavel_instalacao_canonica() -> Path:
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if not local_appdata:
        return Path(sys.executable).resolve()
    exe_names = [Path(sys.executable).name, "Fretio.exe", "FreteBot.exe"]
    vistos: set[str] = set()
    candidatos_dirs = [
        Path(local_appdata) / "Programs" / "Fretio",
        Path(local_appdata) / "Programs" / "Romaneio Beta",
        Path(local_appdata) / "Programs" / "FreteBot",
    ]
    fallback = candidatos_dirs[0] / exe_names[0]
    for base_dir in candidatos_dirs:
        for exe_name in exe_names:
            exe_name_norm = exe_name.lower()
            if exe_name_norm in vistos:
                continue
            vistos.add(exe_name_norm)
            candidato = base_dir / exe_name
            if candidato.exists():
                return candidato
    return fallback


def _msgbox(msg: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "Fretio", 0x00000000 | 0x00000030)
    except Exception:
        print(f"[fretio] {msg}", file=sys.stderr, flush=True)


def garantir_instalacao_unica() -> bool:
    """Força execução pela instalação canônica para evitar cópias paralelas."""
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return True
    try:
        exe_atual = Path(sys.executable).resolve()
        exe_canonico = _executavel_instalacao_canonica().resolve()
        if exe_atual == exe_canonico:
            return True
        if exe_canonico.exists():
            try:
                os.startfile(str(exe_canonico))  # type: ignore[attr-defined]
            except Exception:
                pass
            _msgbox(
                "Detectamos mais de uma cópia do Fretio neste computador.\n\n"
                f"A instalação oficial é:\n{exe_canonico}\n\n"
                "Esta cópia será encerrada para evitar conflito."
            )
            return False
    except Exception:
        return True
    return True


def garantir_instancia_unica() -> bool:
    """Impede mais de uma instância do app por máquina/sessão de usuário."""
    global _SINGLE_INSTANCE_MUTEX_HANDLE
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        create_mutex.restype = wintypes.HANDLE
        _SINGLE_INSTANCE_MUTEX_HANDLE = create_mutex(None, False, "Local\\Fretio.Singleton.v1")
        if not _SINGLE_INSTANCE_MUTEX_HANDLE:
            return True
        ERROR_ALREADY_EXISTS = 183
        return ctypes.get_last_error() != ERROR_ALREADY_EXISTS
    except Exception:
        return True


def avisar_instancia_ativa() -> None:
    _msgbox(
        "O Fretio já está em execução neste computador.\n\n"
        "Feche a janela atual antes de abrir outra instância."
    )


# ── Crash log / hooks / logging ────────────────────────────────────────────
def iniciar_crash_log() -> None:
    """Redireciona stderr para %APPDATA%/Fretio/crash.log, filtrando ruído."""
    try:
        appdata = os.getenv("APPDATA")
        if not appdata:
            return
        log_dir = Path(appdata) / "Fretio"
        log_dir.mkdir(parents=True, exist_ok=True)
        crash_log = log_dir / "crash.log"
        try:
            if crash_log.exists() and crash_log.stat().st_size > 2 * 1024 * 1024:
                crash_log.write_text("", encoding="utf-8")
        except Exception:
            pass

        class _FilteredStderr:
            _IGNORE = (
                "I/O operation on closed pipe", "DEP0169", "EPIPE: broken pipe",
                "Exception ignored in:", "_ProactorBasePipeTransport.__del__",
                "BaseSubprocessTransport.__del__",
            )

            def __init__(self, stream):
                self._stream = stream
                self._buf = ""

            def write(self, s):
                if not s:
                    return
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if any(ign in line for ign in self._IGNORE):
                        continue
                    self._stream.write(line + "\n")

            def flush(self):
                if self._buf and not any(ign in self._buf for ign in self._IGNORE):
                    self._stream.write(self._buf)
                self._buf = ""
                self._stream.flush()

            def __getattr__(self, name):
                return getattr(self._stream, name)

        sys.stderr = _FilteredStderr(open(crash_log, "a", encoding="utf-8"))
    except Exception:
        pass


def instalar_thread_excepthook() -> None:
    import traceback as _tb
    _original = threading.excepthook

    def _hook(args):
        msg = "".join(_tb.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        print(f"[fretio] Exceção em thread {args.thread}:\n{msg}", file=sys.stderr, flush=True)
        try:
            import logging
            logging.getLogger("thread").error("Exceção em thread %s:\n%s", args.thread, msg)
        except Exception:
            pass
        if _original:
            try:
                _original(args)
            except Exception:
                pass

    threading.excepthook = _hook


def instalar_hooks_excecao() -> None:
    instalar_thread_excepthook()
    try:
        from cotacao_transportadoras import setup_global_exception_handler
        setup_global_exception_handler()
    except Exception:
        pass
    try:
        from error_reporter import install_global_hooks
        install_global_hooks()
    except Exception:
        pass
    # Configura o error reporter cedo (pela última empresa, se houver)
    try:
        from company_config import _ler_ultima_empresa, _empresa_config_path
        from error_reporter import configure as _er_configure
        ultima = _ler_ultima_empresa()
        if ultima:
            _er_configure(_empresa_config_path(ultima))
    except Exception:
        pass


def configurar_logging_diag():
    """Configura logging e registra diagnóstico de inicialização. Retorna o logger."""
    try:
        from fretio.logging_conf import setup_logging, get_logger
        setup_logging()
        log = get_logger("startup")
        log.info("=" * 60)
        log.info("Fretio (web) iniciando")
        log.info("Python: %s", sys.version)
        log.info("Frozen: %s", getattr(sys, "frozen", False))
        log.info("Exe: %s", sys.executable)
        log.info("APPDATA: %s", os.getenv("APPDATA", "?"))
        log.info("_MEIPASS: %s", getattr(sys, "_MEIPASS", ""))
        return log
    except Exception as exc:
        print(f"[fretio] Falha ao configurar logging: {exc}", file=sys.stderr, flush=True)
        return None


def run_process_startup() -> tuple[bool, object]:
    """Executa a partida de processo Qt-free. Retorna (continuar, logger).

    Se continuar=False, o app deve encerrar (instância/instalação duplicada)."""
    try:
        from startup import _migrate_appdata_fretebot_to_fretio
        _migrate_appdata_fretebot_to_fretio()
    except Exception:
        pass
    try:
        from company_config import _scrub_developer_credentials_from_configs
        _scrub_developer_credentials_from_configs()
    except Exception:
        pass

    iniciar_crash_log()

    if not garantir_instalacao_unica():
        return False, None
    if not garantir_instancia_unica():
        avisar_instancia_ativa()
        return False, None

    instalar_hooks_excecao()
    logger = configurar_logging_diag()
    return True, logger
