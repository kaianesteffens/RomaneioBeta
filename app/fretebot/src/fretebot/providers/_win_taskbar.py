"""Utilitário para ocultar janela do Chrome da barra de tarefas (Windows)."""
import ctypes
import ctypes.wintypes as wintypes
import logging
import sys
import uuid

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    _user32 = ctypes.windll.user32

    _GWL_EXSTYLE = -20
    _WS_EX_APPWINDOW = 0x00040000
    _WS_EX_TOOLWINDOW = 0x00000080
    _SW_HIDE = 0
    _SW_SHOW = 5
    _SW_SHOWNOACTIVATE = 4
    _SWP_NOMOVE = 0x0002
    _SWP_NOSIZE = 0x0001
    _HWND_TOPMOST = -1
    _HWND_NOTOPMOST = -2

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _aplicar_toolwindow(hwnd: int) -> None:
    """Aplica WS_EX_TOOLWINDOW num HWND para ocultá-lo da barra de tarefas."""
    _user32.ShowWindow(hwnd, _SW_HIDE)
    style = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    style = (style | _WS_EX_TOOLWINDOW) & ~_WS_EX_APPWINDOW
    _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
    _user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)


def _ocultar_ime_interno() -> None:
    """Esconde qualquer janela IME/MSCTFIME visível (chamada interna síncrona)."""
    _GetClassNameW = _user32.GetClassNameW

    @_WNDENUMPROC
    def cb(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        _GetClassNameW(hwnd, cls, 256)
        if cls.value in ("IME", "MSCTFIME UI"):
            _user32.ShowWindow(hwnd, _SW_HIDE)
        return True

    _user32.EnumWindows(cb, 0)


def _forcar_foreground(hwnd: int) -> None:
    """Força uma janela para o foreground no Windows 10/11.

    Usa AttachThreadInput + keybd_event para contornar a restrição do Windows
    que impede SetForegroundWindow de funcionar quando o chamador não é o
    processo foreground atual.
    """
    _SW_RESTORE = 9
    _KEYEVENTF_EXTENDEDKEY = 0x0001
    _KEYEVENTF_KEYUP = 0x0002
    _VK_MENU = 0x12  # Alt key

    kernel32 = ctypes.windll.kernel32

    # Restaura se minimizada
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)

    # Obtém threads envolvidas
    fg_hwnd = _user32.GetForegroundWindow()
    fg_thread = _user32.GetWindowThreadProcessId(fg_hwnd, None)
    target_thread = _user32.GetWindowThreadProcessId(hwnd, None)
    our_thread = kernel32.GetCurrentThreadId()

    attached_fg = False
    attached_target = False
    try:
        # Attach nossa thread à thread do foreground para herdar permissão
        if fg_thread != our_thread:
            attached_fg = bool(_user32.AttachThreadInput(our_thread, fg_thread, True))
        if target_thread != our_thread and target_thread != fg_thread:
            attached_target = bool(_user32.AttachThreadInput(our_thread, target_thread, True))

        # Simula Alt press para desbloquear SetForegroundWindow
        _user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_EXTENDEDKEY, 0)
        _user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_EXTENDEDKEY | _KEYEVENTF_KEYUP, 0)

        # Agora SetForegroundWindow funciona
        _user32.ShowWindow(hwnd, _SW_SHOW)
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
    finally:
        if attached_fg:
            _user32.AttachThreadInput(our_thread, fg_thread, False)
        if attached_target:
            _user32.AttachThreadInput(our_thread, target_thread, False)

    # Oculta janelas IME que podem ter ficado visíveis pelo Alt simulado
    _ocultar_ime_interno()


def ocultar_taskbar_por_pid(pid: int) -> bool:
    """Oculta todas as janelas top-level de um PID da barra de tarefas."""
    if sys.platform != "win32":
        return False
    encontrou = False

    @_WNDENUMPROC
    def callback(hwnd, _lparam):
        nonlocal encontrou
        proc_id = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid and _user32.IsWindowVisible(hwnd):
            _aplicar_toolwindow(hwnd)
            encontrou = True
        return True

    _user32.EnumWindows(callback, 0)
    if encontrou:
        logger.debug("Janela(s) do PID %d ocultada(s) da barra de tarefas", pid)
    return encontrou


def _encontrar_hwnd_por_titulo(substring: str) -> int:
    """Encontra HWND de janela cujo título contenha a substring."""
    resultado = [0]

    @_WNDENUMPROC
    def callback(hwnd, _lparam):
        length = _user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buf, length + 1)
            if substring in buf.value:
                resultado[0] = hwnd
                return False  # Para a enumeração
        return True

    _user32.EnumWindows(callback, 0)
    return resultado[0]


def trazer_janela_frente_por_pid(pid: int) -> bool:
    """Traz janela(s) de um PID para frente usando Win32 API (sem Playwright)."""
    if sys.platform != "win32":
        return False
    encontrou = False

    @_WNDENUMPROC
    def callback(hwnd, _lparam):
        nonlocal encontrou
        proc_id = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid and _user32.IsWindowVisible(hwnd):
            # Restaura WS_EX_APPWINDOW para taskbar
            style = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            style = (style & ~_WS_EX_TOOLWINDOW) | _WS_EX_APPWINDOW
            _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
            _forcar_foreground(hwnd)
            encontrou = True
        return True

    _user32.EnumWindows(callback, 0)
    if encontrou:
        logger.debug("Janela(s) do PID %d trazida(s) para frente", pid)
    return encontrou


async def trazer_janela_frente(page) -> bool:
    """Traz a janela do navegador para frente de todas usando Win32 API."""
    if sys.platform != "win32":
        return False
    try:
        titulo_original = await page.evaluate("document.title")
        marcador = f"_FreteBot_{uuid.uuid4().hex[:8]}"
        await page.evaluate(f"document.title = {marcador!r}")
        await page.wait_for_timeout(200)

        hwnd = _encontrar_hwnd_por_titulo(marcador)

        # Restaura título original
        await page.evaluate(f"document.title = {titulo_original!r}")

        if hwnd:
            # Restaura WS_EX_APPWINDOW para a taskbar exibir durante o CAPTCHA
            style = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            style = (style & ~_WS_EX_TOOLWINDOW) | _WS_EX_APPWINDOW
            _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
            _forcar_foreground(hwnd)
            logger.debug("Janela HWND=%d trazida para frente", hwnd)
            return True
        logger.debug("Não foi possível encontrar HWND para trazer à frente")
        return False
    except Exception as e:
        logger.warning("Falha ao trazer janela para frente: %s", e)
        return False


async def ocultar_taskbar_por_pagina(page) -> bool:
    """Oculta a janela do navegador da barra de tarefas usando título temporário."""
    if sys.platform != "win32":
        return False
    try:
        titulo_original = await page.evaluate("document.title")
        marcador = f"_FreteBot_{uuid.uuid4().hex[:8]}"
        await page.evaluate(f"document.title = {marcador!r}")
        await page.wait_for_timeout(200)

        hwnd = _encontrar_hwnd_por_titulo(marcador)

        # Restaura título original
        await page.evaluate(f"document.title = {titulo_original!r}")

        if hwnd:
            _aplicar_toolwindow(hwnd)
            logger.debug("Janela HWND=%d ocultada da barra de tarefas", hwnd)
            return True
        logger.debug("Não foi possível encontrar HWND pelo título")
        return False
    except Exception as e:
        logger.warning("Falha ao ocultar janela da taskbar: %s", e)
        return False


def ocultar_janelas_ime() -> int:
    """Oculta todas as janelas 'Default IME' visíveis.

    Essas janelas são criadas automaticamente pelo Windows IME para cada
    processo Chrome e podem interferir com foco/cliques.
    Retorna a quantidade de janelas ocultadas.
    """
    if sys.platform != "win32":
        return 0
    count = 0

    _GetClassNameW = _user32.GetClassNameW

    @_WNDENUMPROC
    def callback(hwnd, _lparam):
        nonlocal count
        if not _user32.IsWindowVisible(hwnd):
            return True
        cls_buf = ctypes.create_unicode_buffer(256)
        _GetClassNameW(hwnd, cls_buf, 256)
        if cls_buf.value == "IME" or cls_buf.value == "MSCTFIME UI":
            _user32.ShowWindow(hwnd, _SW_HIDE)
            count += 1
        return True

    _user32.EnumWindows(callback, 0)
    if count:
        logger.debug("Ocultou %d janela(s) IME visível(is)", count)
    return count
