import os
import shutil


def find_chrome() -> str:
    """Retorna o caminho do Google Chrome instalado. Levanta FileNotFoundError se nao encontrado."""
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%UserProfile%\AppData\Local\Google\Chrome\Application\chrome.exe"),
    ]
    try:
        import winreg

        for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(
                    root_key,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                )
                path, _ = winreg.QueryValueEx(key, "")
                winreg.CloseKey(key)
                if path and os.path.isfile(path):
                    candidates.append(path)
            except OSError:
                pass
    except ImportError:
        pass
    candidates.extend(filter(None, [shutil.which("chrome"), shutil.which("google-chrome")]))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise FileNotFoundError("Google Chrome nao encontrado. Instale o Chrome para usar o Fretio.")
