import atexit
import ctypes
import os
import socket
import subprocess
import threading
from ctypes import wintypes

from fretio.logging_conf import get_logger

_logger = get_logger(__name__)

_WINDOWS_JOB_LOCK = threading.Lock()
_WINDOWS_JOB_HANDLE = None
_FALLBACK_OWNED_PIDS: set[int] = set()
_BROWSER_SHUTDOWN_STARTED = threading.Event()

if os.name == "nt":
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def _kill_pid_tree(pid: int) -> None:
    if pid <= 0:
        return
    # No Windows, taskkill /T mata a arvore inteira (pai + filhos).
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except Exception:
            pass
    try:
        os.kill(pid, 9)
    except Exception:
        pass


def _get_windows_job_handle():
    if os.name != "nt":
        return None
    global _WINDOWS_JOB_HANDLE
    if _WINDOWS_JOB_HANDLE is not None:
        return _WINDOWS_JOB_HANDLE
    with _WINDOWS_JOB_LOCK:
        if _WINDOWS_JOB_HANDLE is not None:
            return _WINDOWS_JOB_HANDLE
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            raise ctypes.WinError(ctypes.get_last_error())

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job_handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(job_handle)
            raise ctypes.WinError(ctypes.get_last_error())

        _WINDOWS_JOB_HANDLE = job_handle
        return _WINDOWS_JOB_HANDLE


def _register_owned_proc(proc, *, source: str = "chrome") -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name != "nt":
        return

    pid = proc.pid
    try:
        job_handle = _get_windows_job_handle()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        proc_handle = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
            False,
            pid,
        )
        if not proc_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ok = kernel32.AssignProcessToJobObject(job_handle, proc_handle)
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel32.CloseHandle(proc_handle)
    except Exception as exc:
        with _WINDOWS_JOB_LOCK:
            _FALLBACK_OWNED_PIDS.add(pid)
        _logger.warning(
            "Falha ao associar %s PID=%d a Job Object; usando fallback por PID: %s",
            source,
            pid,
            exc,
        )


def _forget_owned_proc(proc) -> None:
    if proc is None:
        return
    with _WINDOWS_JOB_LOCK:
        _FALLBACK_OWNED_PIDS.discard(getattr(proc, "pid", None))


def _cleanup_fallback_owned_procs() -> None:
    with _WINDOWS_JOB_LOCK:
        pids = list(_FALLBACK_OWNED_PIDS)
        _FALLBACK_OWNED_PIDS.clear()
    for pid in pids:
        _kill_pid_tree(pid)


def request_browser_shutdown() -> None:
    _BROWSER_SHUTDOWN_STARTED.set()


def browser_shutdown_requested() -> bool:
    return _BROWSER_SHUTDOWN_STARTED.is_set()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill_proc(proc):
    """Encerra processo Chrome e todos os seus filhos (tree kill)."""
    if proc is None or proc.poll() is not None:
        _forget_owned_proc(proc)
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    if proc.poll() is None:
        _kill_pid_tree(proc.pid)
    _forget_owned_proc(proc)


atexit.register(_cleanup_fallback_owned_procs)
